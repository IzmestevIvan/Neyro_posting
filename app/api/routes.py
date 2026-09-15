import json
import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

import httpx
import psutil
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import db
from app.ai import gemini, key_pool
from app.api.auth import (
    active_user,
    current_user,
    has_access,
    is_admin,
    owned_channel,
    require_admin,
)
from app.config import (
    ADMIN_IDS,
    LOGO_DIR,
    DELAY_MODES,
    LANGUAGES,
    PACE_MODES,
    TIMEZONES,
)
from app.core import promo, publisher, scheduler, watermark, runtime, business
from app.sources import rss, telegram_web

router = APIRouter(prefix="/api")
log = logging.getLogger('api')

BOOL_FIELDS = {
    "business_mode", "business_auto",
    "autopost", "paused", "channel_voice", "hits_only", "media_only", "digest_enabled", "watermark",
}
TEXT_FIELDS = {"instructions", "stopwords", "signature_text", "signature_url", "gemini_key"}
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
QUALITY = {"fast", "balanced", "super"}


def public_channel(channel: dict) -> dict:
    return {**{k: v for k, v in channel.items() if k not in ('gemini_key','logo_path','voice_sample')},
            'gemini_key_configured': bool(channel.get('gemini_key')),
            'logo_configured': bool(channel.get('logo_path'))}


def _clean_settings(payload: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key == 'business_profile':
            try:
                out[key] = business.clean_profile(value)
            except (ValueError, httpx.InvalidURL) as exc:
                raise HTTPException(422, str(exc)) from exc
        elif key in BOOL_FIELDS:
            if type(value) is not bool:
                raise HTTPException(422, f"{key}: ожидается переключатель true/false")
            out[key] = int(value)
        elif key in TEXT_FIELDS:
            if not isinstance(value, str) or len(value) > 4000:
                raise HTTPException(422, f'{key}: ожидается текст до 4000 символов')
            if key == 'signature_url' and value and not re.match(r'^https?://[^\s<>"\x00-\x20]+$', value):
                raise HTTPException(422, 'Ссылка подписи должна начинаться с https:// или http://')
            out[key] = str(value)[:4000]
        elif key == "delay_mode" and isinstance(value, str) and value in DELAY_MODES:
            out[key] = value
        elif key == 'watermark_position' and isinstance(value, str) and value in watermark.POSITIONS:
            out[key] = value
        elif key == "pace" and isinstance(value, str) and value in PACE_MODES:
            out[key] = value
        elif key == "tz" and isinstance(value, str) and value in TIMEZONES:
            out[key] = value
        elif key == "lang" and isinstance(value, str) and value in LANGUAGES:
            out[key] = value
        elif key == "quality" and isinstance(value, str) and value in QUALITY:
            out[key] = value
        elif key == "digest_time":
            if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise HTTPException(422, "время дайджеста: укажите ЧЧ:ММ от 00:00 до 23:59")
            out[key] = value
        elif key in {"window_start", "window_end"}:
            low, high = (0, 23) if key == "window_start" else (1, 24)
            if isinstance(value, bool) or not re.fullmatch(r"\d{1,2}", str(value)) or not low <= int(value) <= high:
                raise HTTPException(422, f"{key}: укажите целый час от {low} до {high}")
            out[key] = int(value)
        else:
            raise HTTPException(422, f"неизвестная настройка или недопустимое значение: {key}")
    if not out:
        raise HTTPException(400, "нечего сохранять")
    return out


async def _load_voice_sample(channel: dict) -> Optional[str]:
    if not channel.get("username"):
        return None
    try:
        async with httpx.AsyncClient() as client:
            items, _ = await telegram_web.fetch(client, channel["username"])
    except Exception:
        return None
    texts = [i.text for i in items[-8:] if len(i.text) > 80]
    return "\n\n---\n\n".join(texts[-5:]) or None


@router.get("/bootstrap")
async def bootstrap(request: Request, user: dict = Depends(current_user)) -> dict:
    channels = await db.fetch_all(
        "SELECT * FROM channels WHERE owner_id = ? ORDER BY id", (user["tg_id"],)
    )
    return {
        "bot_username": request.app.state.bot_username,
        "support_username": await db.get_kv('support_username'),
        "user": {
            "id": user["tg_id"],
            "first_name": user["first_name"],
            "is_admin": is_admin(user),
            "daily_limit": user["daily_limit"],
            "max_channels": user["max_channels"],
            "access_until": user["access_until"],
            "has_access": has_access(user),
            "promo_code": user["promo_code"],
        },
        "channels": [public_channel(c) for c in channels],
        "languages": LANGUAGES,
        "timezones": TIMEZONES,
        "delay_modes": list(DELAY_MODES),
        "pace_modes": list(PACE_MODES),
    }


@router.post("/promo/redeem")
async def redeem_promo(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict:
    try:
        result = await promo.redeem(str(payload.get("code", "")), user["tg_id"])
    except (promo.PromoError, ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.post("/channels")
async def add_channel(
    request: Request, payload: dict = Body(...), user: dict = Depends(active_user)
) -> dict:
    ref = str(payload.get("ref", "")).strip()
    if not ref:
        raise HTTPException(400, "укажите @канал")

    owned = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM channels WHERE owner_id = ?", (user["tg_id"],)
    )
    if not is_admin(user) and owned["n"] >= user["max_channels"]:
        raise HTTPException(
            403, f"по вашему тарифу доступно каналов: {user['max_channels']}"
        )

    username = telegram_web.normalize_ref(ref)

    bot = request.app.state.bot
    try:
        chat_id, title, resolved = await publisher.resolve_channel(bot, f"@{username}")
    except Exception as exc:
        raise HTTPException(400, f"бот не видит канал: добавьте его администратором. {exc}") from exc

    try:
        await publisher.verify_channel_permissions(bot, chat_id, user["tg_id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        log.warning('Не удалось проверить права канала', exc_info=True)
        raise HTTPException(502, "не удалось проверить права на канал; повторите позже") from exc

    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        owner = await conn.fetchrow('SELECT * FROM users WHERE tg_id=$1 FOR UPDATE', user['tg_id'])
        if not owner or not has_access(dict(owner)):
            raise HTTPException(403, 'Доступ закрыт')
        await conn.execute('SELECT pg_advisory_xact_lock($1::bigint)', chat_id)
        if await conn.fetchval('SELECT id FROM channels WHERE chat_id=$1', chat_id):
            raise HTTPException(409, 'Этот канал уже подключён к сервису')
        count = await conn.fetchval('SELECT count(*) FROM channels WHERE owner_id=$1', user['tg_id'])
        if not is_admin(dict(owner)) and count >= owner['max_channels']:
            raise HTTPException(403, 'Достигнут лимит каналов тарифа')
        channel_id = await conn.fetchval(
            'INSERT INTO channels(owner_id,chat_id,username,title,created_at) VALUES($1,$2,$3,$4,now()) RETURNING id',
            user['tg_id'],chat_id,resolved or username,title)
    await db.set_kv(f"active:{user['tg_id']}", channel_id)
    return public_channel(await db.fetch_one("SELECT * FROM channels WHERE id = ?", (channel_id,)))


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: int, user: dict = Depends(current_user)) -> dict:
    await owned_channel(channel_id, user)
    await db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    return {"ok": True}


@router.patch("/channels/{channel_id}")
async def update_channel(
    channel_id: int, payload: dict = Body(...), user: dict = Depends(active_user)
) -> dict:
    channel = await owned_channel(channel_id, user)
    fields = _clean_settings(payload)

    if fields.get("channel_voice") and not channel["voice_sample"]:
        sample = await _load_voice_sample(channel)
        if sample:
            fields["voice_sample"] = sample

    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        fresh = await conn.fetchrow('SELECT * FROM channels WHERE id=$1 FOR UPDATE', channel_id)
        if not fresh:
            raise HTTPException(404, 'Канал удалён')
        if fields.get('business_mode', fresh['business_mode']):
            fields.update(autopost=0, digest_enabled=0)
        if fields.get('business_auto', fresh['business_auto']):
            profile = json.loads(fields.get('business_profile', fresh['business_profile']))
            if not profile.get('name') or not profile.get('services'):
                raise HTTPException(422, 'Для ежедневных предложений заполните название и услуги компании')
        assignments = ', '.join(f'{key}=${i}' for i, key in enumerate(fields, 1))
        await conn.execute(f'UPDATE channels SET {assignments} WHERE id=${len(fields)+1}', *fields.values(), channel_id)
        if fields.get('business_mode') and not fresh['business_mode']:
            await conn.execute("UPDATE posts SET status='pending',business_draft=1,publish_at=NULL, "
                "reason='Включён бизнес-режим: требуется согласование' WHERE channel_id=$1 "
                "AND status IN ('approved','digest')", channel_id)
    if any(fields.get(key) == 0 and channel[key] for key in ('hits_only', 'media_only')):
        await scheduler.reconsider_filtered(channel_id)
    await db.set_kv(f"active:{user['tg_id']}", channel_id)
    return public_channel(await db.fetch_one("SELECT * FROM channels WHERE id = ?", (channel_id,)))


@router.get("/channels/{channel_id}/stats")
async def channel_stats(channel_id: int, user: dict = Depends(active_user)) -> dict:
    channel = await owned_channel(channel_id, user)

    last_rejection = await db.fetch_one(
        "SELECT reason, created_at FROM posts WHERE channel_id=? AND status='filtered' ORDER BY id DESC LIMIT 1",
        (channel_id,),
    )
    waiting = await db.fetch_one(
        "SELECT COUNT(*) FILTER(WHERE status='digest') AS digest, "
        "COUNT(*) FILTER(WHERE status='new') AS processing, "
        "MIN(publish_at) FILTER(WHERE status='approved') AS next_at, "
        "MAX(published_at) FILTER(WHERE status='published') AS last_published_at "
        "FROM posts WHERE channel_id=?", (channel_id,),
    )

    # Lifetime counters come from stats_daily because old post rows get pruned; the queue
    # numbers come from posts, which is the live state.
    totals = await db.fetch_one(
        "SELECT COALESCE(SUM(published), 0) AS published, COALESCE(SUM(filtered), 0) AS filtered, "
        "COALESCE(SUM(duplicates), 0) AS duplicates FROM stats_daily WHERE channel_id = ?",
        (channel_id,),
    )
    counts = await db.fetch_one(
        "SELECT "
        " COUNT(*) FILTER (WHERE status IN ('approved', 'pending', 'digest', 'new')) AS queued,"
        " COUNT(*) FILTER (WHERE status = 'pending') AS pending,"
        " COUNT(*) FILTER (WHERE status IN ('approved', 'pending', 'digest') "
        " AND NULLIF(trim(text_out), '') IS NOT NULL) AS ready"
        " FROM posts WHERE channel_id = ?",
        (channel_id,),
    )
    sources = await db.fetch_one(
        "SELECT COUNT(*) FILTER(WHERE enabled=1) AS n, COUNT(*) FILTER(WHERE enabled=1 AND error IS NOT NULL) AS errors, "
        "MAX(checked_at) AS checked_at FROM sources WHERE channel_id = ?", (channel_id,)
    )
    today_row = await db.fetch_one(
        "SELECT published, ai_requests FROM stats_daily WHERE channel_id = ? AND day = CURRENT_DATE",
        (channel_id,),
    )
    latest = await db.fetch_one(
        "SELECT subscribers, avg_views FROM stats_daily WHERE channel_id = ? "
        "AND (subscribers > 0 OR avg_views > 0) ORDER BY day DESC LIMIT 1",
        (channel_id,),
    )

    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    published_rows = await db.fetch_all(
        "SELECT day, published AS n FROM stats_daily "
        "WHERE channel_id = ? AND day >= CURRENT_DATE - 6",
        (channel_id,),
    )
    by_day = {r["day"].isoformat(): r["n"] for r in published_rows}
    subs_rows = await db.fetch_all(
        "SELECT day, subscribers FROM stats_daily WHERE channel_id = ? "
        "AND day >= CURRENT_DATE - 6 AND subscribers > 0 ORDER BY day",
        (channel_id,),
    )

    blockers = []
    if channel['paused']:
        blockers.append('Канал на паузе — снимите паузу в настройках.')
    if channel.get('business_mode'):
        blockers.append('Режим бизнеса: публикация только после вашего согласования во вкладке «Посты».')
        if not channel.get('business_auto'):
            blockers.append('Ежедневная подготовка выключена. Подготовьте пост вручную или включите предложения в досье.')
    else:
        if not channel['autopost']:
            blockers.append('Автопостинг выключен — материалы требуют ручного согласования.')
        if not sources['n']:
            blockers.append('Нет включённых источников. Добавьте канал или RSS во вкладке «Источники».')
        elif sources['errors']:
            blockers.append(f"Источники с ошибками: {sources['errors']} из {sources['n']}. Проверьте вкладку «Источники».")
        if channel['digest_enabled']:
            blockers.append(f"Включён дайджест: выпуск в {channel['digest_time']} ({channel['tz']}), а не отдельные посты.")
    if not scheduler.news_policy.window_open(channel):
        blockers.append(f"Сейчас вне окна работы {channel['window_start']}:00–{channel['window_end']}:00 ({channel['tz']}).")

    return {
        'blockers': blockers,
        'sources_checked_at': sources['checked_at'],
        'waiting': waiting,
        'last_rejection': last_rejection,
        "published": (totals or {}).get("published") or 0,
        "today": (today_row or {}).get("published") or 0,
        "queued": (counts or {}).get("queued") or 0,
        "pending": (counts or {}).get("pending") or 0,
        "ready": (counts or {}).get("ready") or 0,
        "duplicates": (totals or {}).get("duplicates") or 0,
        "filtered": (totals or {}).get("filtered") or 0,
        "sources": sources["n"],
        "subscribers": (latest or {}).get("subscribers") or 0,
        "avg_views": (latest or {}).get("avg_views") or 0,
        "ai_requests": (today_row or {}).get("ai_requests") or 0,
        "daily_limit": user["daily_limit"],
        "remaining": await publisher.quota_left(user["tg_id"]),
        "posts_chart": [{"day": d, "count": by_day.get(d, 0)} for d in days],
        "subscribers_chart": [
            {"day": r["day"].isoformat(), "count": r["subscribers"]} for r in subs_rows
        ],
        "mode": "автопостинг" if channel["autopost"] else "модерация",
        "paused": bool(channel["paused"]),
        # Нагрузка сервера и состояние воркера — внутренняя кухня, клиенту она ни о чём не
        # говорит и лишний раз выдаёт устройство системы. Показываем только администратору.
        "system": None,
    }


async def _system_health() -> dict:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram_used": round(memory.used / 1024**3, 1),
        "ram_total": round(memory.total / 1024**3, 1),
        "ram_percent": memory.percent,
        "disk_used": round(disk.used / 1024**3, 1),
        "disk_total": round(disk.total / 1024**3, 1),
        "disk_percent": disk.percent,
        "process_mb": round(psutil.Process().memory_info().rss / 1024**2),
        "activity": scheduler.state["activity"],
        "polling": scheduler.state["polling"],
        "last_publish_at": await db.get_kv("last_publish_at"),
        "model_cooldown": await gemini.cooldown_left(),
        "last_error": scheduler.state["last_error"],
    }


@router.post('/channels/{channel_id}/logo')
async def upload_logo(channel_id: int, request: Request, user: dict = Depends(active_user)) -> dict:
    await owned_channel(channel_id, user)
    if request.headers.get('content-type', '').split(';')[0] != 'image/png':
        raise HTTPException(422, 'Загрузите PNG-файл, чтобы сохранить прозрачность')
    content = await request.body()
    if len(content) > 4 * 1024 * 1024:
        raise HTTPException(413, 'Максимальный размер PNG — 4 МБ')
    path = LOGO_DIR / f'{channel_id}.png'
    async with runtime.channel_lock(channel_id):
        try:
            await asyncio.to_thread(watermark.save_logo, content, path)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, 'Не удалось прочитать PNG: максимум 4 МБ / 4 миллиона пикселей, не полностью прозрачный') from exc
        await db.execute('UPDATE channels SET logo_path=?, watermark=1 WHERE id=?', (str(path), channel_id))
    return public_channel(await owned_channel(channel_id, user))


@router.get("/admin/monitoring")
async def admin_monitoring(user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    queue = await db.fetch_one(
        "SELECT COUNT(*) FILTER (WHERE status = 'new') AS new, "
        "COUNT(*) FILTER (WHERE status = 'pending') AS pending, "
        "COUNT(*) FILTER (WHERE status IN ('approved', 'digest')) AS ready, "
        "COUNT(*) FILTER (WHERE status = 'publishing') AS publishing, "
        "COUNT(*) FILTER (WHERE status IN ('uncertain', 'partial')) AS attention FROM posts"
    )
    capacity = await db.fetch_one(
        "SELECT (SELECT COUNT(*) FROM users) AS users, "
        "(SELECT COUNT(*) FROM channels) AS channels, "
        "(SELECT COUNT(*) FROM channels WHERE paused = 0) AS active_channels, "
        "pg_database_size(current_database()) AS database_bytes"
    )
    from app.core.operations import recent_events
    channels = await db.fetch_all("SELECT c.id,c.title,c.paused,c.autopost,c.business_mode,c.digest_enabled,c.window_start,c.window_end,c.tz, "
        "(SELECT count(*) FROM sources s WHERE s.channel_id=c.id AND enabled=1) AS sources, "
        "(SELECT max(checked_at) FROM sources s WHERE s.channel_id=c.id AND enabled=1) AS checked_at, "
        "(SELECT max(published_at) FROM posts p WHERE p.channel_id=c.id) AS last_published, "
        "(SELECT min(publish_at) FROM posts p WHERE p.channel_id=c.id AND p.status='approved') AS next_at, "
        "(SELECT count(*) FROM posts p WHERE p.channel_id=c.id AND p.status IN ('uncertain','partial')) AS uncertain "
        "FROM channels c ORDER BY c.id")
    for c in channels:
        if c['paused']: status = 'На паузе'
        elif c['business_mode']: status = 'Бизнес: требуется согласование'
        elif not c['autopost']: status = 'Автопостинг выключен'
        elif c['uncertain']: status = 'Требуется сверка предыдущей отправки'
        elif not c['sources']: status = 'Нет включённых источников'
        elif not scheduler.news_policy.window_open(c): status = 'Вне рабочего окна'
        elif c['digest_enabled']: status = 'Включён дайджест'
        elif c['next_at'] and c['next_at'] < db.utcnow()-timedelta(minutes=10): status = 'Очередь просрочена: проверьте квоту и темп'
        elif not c['checked_at'] or c['checked_at'] < db.utcnow()-timedelta(minutes=15): status = 'Источники давно не проверялись'
        else: status = 'Ожидает подходящий материал / время публикации'
        c['status'] = status
    return {"system": await _system_health(), "queue": queue, "capacity": capacity,
            'channels': channels,
            "events": await asyncio.to_thread(recent_events)}


@router.get("/channels/{channel_id}/feed")
async def channel_feed(channel_id: int, user: dict = Depends(active_user)) -> list[dict]:
    await owned_channel(channel_id, user)
    rows = await db.fetch_all(
        "SELECT id, url, source_title, text_out, raw_text, media, fact_check, reason, created_at, business_draft "
        "FROM posts WHERE channel_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 30",
        (channel_id,),
    )
    for row in rows:
        row["media"] = json.loads(row["media"] or "[]")
        row["fact_check"] = json.loads(row["fact_check"]) if row["fact_check"] else None
    return rows


@router.get("/channels/{channel_id}/history")
async def channel_history(channel_id: int, user: dict = Depends(active_user)) -> list[dict]:
    await owned_channel(channel_id, user)
    rows = await db.fetch_all(
        "SELECT id, status, reason, source_title, substr(COALESCE(text_out, raw_text), 1, 200) AS preview, "
        "created_at, published_at, (SELECT receipts FROM delivery_attempts d WHERE d.post_id=posts.id ORDER BY d.id DESC LIMIT 1) AS delivery_receipts FROM posts WHERE channel_id = ? AND status != 'pending' "
        "ORDER BY id DESC LIMIT 50",
        (channel_id,),
    )

    for row in rows:
        value = row['delivery_receipts']
        row['delivery_receipts'] = json.loads(value) if isinstance(value, str) else (value or [])
    return rows


@router.post("/posts/{post_id}/{action}")
async def post_action(
    post_id: int, action: str, request: Request, user: dict = Depends(active_user)
) -> dict:
    post = await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post_id,))
    if not post:
        raise HTTPException(404, "пост не найден")
    channel = await owned_channel(post["channel_id"], user)
    bot = request.app.state.bot

    if action in ("confirm_sent", "confirm_absent"):
        try:
            await publisher.reconcile_delivery(post_id, channel, delivered=action == "confirm_sent")
        except publisher.AlreadyPublished as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    if action == "reject":
        if not await publisher.reject_post(post_id):
            raise HTTPException(409, "статус поста изменился; обновите ленту")
        return {"ok": True}

    if action == 'edit' and post.get('business_draft'):
        payload = await request.json()
        text = payload.get('text') if isinstance(payload, dict) else None
        if not isinstance(text, str) or not 40 <= len(text.strip()) <= 3000:
            raise HTTPException(422, 'Текст поста: от 40 до 3000 символов')
        if post['status'] != 'pending':
            raise HTTPException(409, 'Пост уже не ожидает согласования')
        previous_check = json.loads(post['fact_check']) if post.get('fact_check') else {}
        evidence = {'research':previous_check['research']} if previous_check.get('research') else None
        if not await publisher.replace_draft(post, text.strip(), evidence):
            raise HTTPException(409, 'Пост изменился — обновите ленту')
        return {'ok': True}

    if action == "regen" and post.get('business_draft'):
        if post['status'] != 'pending':
            raise HTTPException(409, 'Можно изменить только ожидающий согласования черновик')
        raise HTTPException(409, 'Отклоните этот вариант и подготовьте новый с уточнённой темой в досье компании')

    if action == "regen":
        if post["status"] not in publisher.CLAIMABLE:
            raise HTTPException(409, "пост недоступен для редактирования")
        from app.ai import pipeline

        try:
            result = await pipeline.process(
                post["raw_text"],
                quality=channel["quality"],
                instructions=channel["instructions"],
                lang=channel["lang"],
                api_key=channel["gemini_key"] or None,
                voice_sample=channel["voice_sample"] or "" if channel["channel_voice"] else "",
            )
        except gemini.AIError as exc:
            raise HTTPException(502, str(exc)) from exc
        if not result.ok:
            raise HTTPException(400, result.reason or "не прошло проверку")
        if not await publisher.replace_draft(post, result.text, result.fact_check, needs_review=result.needs_review):
            raise HTTPException(409, "пост уже изменён или опубликован; обновите ленту")
        return {"ok": True, "text": result.text}

    if action == "approve":
        approved_text = None
        if channel.get('business_mode') or post.get('business_draft'):
            try:
                payload = await request.json()
            except ValueError:
                payload = {}
            approved_text = payload.get('text') if isinstance(payload, dict) else None
        if await publisher.quota_left(user["tg_id"]) <= 0:
            raise HTTPException(429, "исчерпан дневной лимит")
        try:
            await publisher.publish_post(bot, post, channel, approved_by_user=True, approved_text=approved_text)
        except publisher.QuotaExceeded as exc:
            raise HTTPException(429, str(exc)) from exc
        except publisher.DeliveryUncertain as exc:
            raise HTTPException(409, str(exc)) from exc
        except publisher.AlreadyPublished as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            log.warning('Не удалось опубликовать пост %s', post_id, exc_info=True)
            raise HTTPException(502, f"не удалось опубликовать: {exc}") from exc
        return {"ok": True}

    raise HTTPException(400, "неизвестное действие")


@router.post("/channels/{channel_id}/publish_now")
async def publish_now(
    channel_id: int, request: Request, user: dict = Depends(active_user)
) -> dict:
    channel = await owned_channel(channel_id, user)
    if channel.get('business_mode'):
        raise HTTPException(409, 'В бизнес-режиме сначала подготовьте и согласуйте черновик во вкладке «Посты»')
    if await publisher.quota_left(channel["owner_id"]) <= 0:
        raise HTTPException(429, "исчерпан дневной лимит")
    try:
        return await scheduler.publish_fresh_once(request.app.state.bot, channel)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except publisher.QuotaExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    except (publisher.DeliveryUncertain, publisher.AlreadyPublished) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        log.warning('Публикация сейчас не выполнена: канал %s', channel_id, exc_info=True)
        raise HTTPException(502, "Не удалось подготовить или отправить свежий пост. Попробуйте позже.") from exc


@router.post('/channels/{channel_id}/business/draft')
async def business_draft(channel_id: int, payload: dict = Body(...), user: dict = Depends(active_user)) -> dict:
    await owned_channel(channel_id, user)
    if set(payload) - {'brief', 'url'}:
        raise HTTPException(422, 'Недопустимые поля запроса')
    try:
        return await business.generate(channel_id, payload.get('brief', ''), payload.get('url', ''))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        log.exception('Ошибка подготовки бизнес-черновика: канал %s', channel_id)
        raise HTTPException(502, 'Не удалось подготовить черновик. Проверьте доступность сайта и повторите позже; пост не опубликован.') from exc


@router.get("/channels/{channel_id}/sources")
async def list_sources(channel_id: int, user: dict = Depends(active_user)) -> list[dict]:
    await owned_channel(channel_id, user)
    return await db.fetch_all(
        "SELECT * FROM sources WHERE channel_id = ? ORDER BY title, ref", (channel_id,)
    )


@router.post("/channels/{channel_id}/sources")
async def add_source(
    channel_id: int, payload: dict = Body(...), user: dict = Depends(active_user)
) -> dict:
    await owned_channel(channel_id, user)
    ref = str(payload.get("ref", "")).strip()
    if not ref:
        raise HTTPException(400, "укажите канал или ссылку на ленту")

    is_rss = ref.startswith("http") and "t.me/" not in ref
    kind = "rss" if is_rss else "tg"
    ref = ref if is_rss else telegram_web.normalize_ref(ref)

    if len(ref) > 2048 or (not is_rss and not re.fullmatch(r"[A-Za-z0-9_]{4,32}", ref)):
        raise HTTPException(422, "Некорректный адрес источника")
    if not is_rss:
        ref = ref.lower()
    existing = await db.fetch_one("SELECT * FROM sources WHERE channel_id=? AND kind=? AND ref=?", (channel_id, kind, ref))
    if existing:
        return {**existing, "already_exists": True}
    title = None
    try:
        if is_rss:
            _, title = await rss.fetch(ref)
        else:
            async with httpx.AsyncClient() as client:
                _, title = await telegram_web.fetch(client, ref)
    except Exception as exc:
        log.warning('Источник недоступен при добавлении', exc_info=True)
        raise HTTPException(400, f"источник недоступен: {exc}") from exc

    source_id = await db.insert(
        "INSERT INTO sources (channel_id, kind, ref, title, created_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(channel_id,kind,ref) DO NOTHING RETURNING id",
        (channel_id, kind, ref, title, db.utcnow()),
    )
    result = await db.fetch_one("SELECT * FROM sources WHERE channel_id=? AND kind=? AND ref=?", (channel_id,kind,ref))
    return {**result, "already_exists": source_id is None}


@router.post("/channels/{channel_id}/sources/copy")
async def copy_channel_sources(channel_id: int, payload: dict = Body(...), user: dict = Depends(active_user)) -> dict:
    from app.sources.manage import copy_sources
    origin = payload.get("from_channel_id")
    ids = payload.get("source_ids")
    if type(origin) is not int or not isinstance(ids, list):
        raise HTTPException(422, "Укажите канал и выбранные источники")
    try:
        return await copy_sources(user["tg_id"], channel_id, origin, ids)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, user: dict = Depends(active_user)) -> dict:
    source = await db.fetch_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if not source:
        raise HTTPException(404, "источник не найден")
    await owned_channel(source["channel_id"], user)
    await db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return {"ok": True}


@router.get("/channels/{channel_id}/ads")
async def list_ads(channel_id: int, user: dict = Depends(active_user), view: str = "active") -> list[dict]:
    await owned_channel(channel_id, user)
    if view not in ("active", "archive"):
        raise HTTPException(422, "неизвестный раздел рекламы")
    condition = "status IN ('archived', 'false_positive')" if view == "archive" else "status IN ('new', 'contacted')"
    rows = await db.fetch_all(
        f"SELECT * FROM ad_offers WHERE channel_id = ? AND {condition} "
        "ORDER BY seen_count DESC, last_seen_at DESC LIMIT 100",
        (channel_id,),
    )
    for row in rows:
        row["contacts"] = json.loads(row["contacts"] or "[]")
        row["reasons"] = json.loads(row["reasons"] or "[]")
    return rows


@router.post("/ads/{ad_id}/{status}")
async def set_ad_status(ad_id: int, status: str, user: dict = Depends(active_user)) -> dict:
    if status not in ("new", "contacted", "archived", "false_positive"):
        raise HTTPException(400, "неизвестный статус")
    offer = await db.fetch_one("SELECT * FROM ad_offers WHERE id = ?", (ad_id,))
    if not offer:
        raise HTTPException(404, "предложение не найдено")
    await owned_channel(offer["channel_id"], user)
    await db.execute("UPDATE ad_offers SET status = ? WHERE id = ?", (status, ad_id))
    return {"ok": True}


@router.get("/admin/promo")
async def list_promo(user: dict = Depends(current_user)) -> list[dict]:
    await require_admin(user)
    return await db.fetch_all(
        "SELECT p.*, u.first_name, u.username FROM promo_codes p "
        "LEFT JOIN users u ON u.tg_id = p.used_by ORDER BY p.created_at DESC LIMIT 200"
    )


@router.post("/admin/promo")
async def create_promo(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    try:
        codes = await promo.create_codes(
            count=int(payload.get("count", 1)),
            plan=str(payload.get("plan", "pro")),
            note=str(payload.get("note", "")) or None,
        )
    except (promo.PromoError, ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"codes": codes, "plans": promo.PLANS}


@router.delete("/admin/promo/{code}")
async def delete_promo(code: str, user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    removed = await db.update(
        "DELETE FROM promo_codes WHERE code = ? AND used_by IS NULL", (promo.formatted(code),)
    )
    if not removed:
        raise HTTPException(400, "код не найден или уже активирован")
    return {"ok": True}


@router.get("/admin/overview")
async def admin_overview(user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    users = await db.fetch_all(
        "SELECT u.*, (SELECT COUNT(*) FROM channels c WHERE c.owner_id = u.tg_id) AS channels "
        "FROM users u ORDER BY u.created_at DESC LIMIT 200"
    )
    totals = await db.fetch_one(
        "SELECT COUNT(*) AS posts, COUNT(*) FILTER (WHERE status = 'published') AS published FROM posts"
    )
    channels = await db.fetch_all(
        "SELECT c.id, c.title, c.username, c.owner_id, c.autopost, c.paused, "
        "(SELECT COUNT(*) FROM sources s WHERE s.channel_id = c.id) AS sources "
        "FROM channels c ORDER BY c.id DESC LIMIT 200"
    )
    ai_today = await db.fetch_one(
        "SELECT SUM(ai_requests) AS n FROM stats_daily WHERE day = CURRENT_DATE"
    )
    return {
        "users": users,
        "channels": channels,
        "totals": {
            "posts": (totals or {}).get("posts") or 0,
            "published": (totals or {}).get("published") or 0,
            "ai_today": (ai_today or {}).get("n") or 0,
            "model_cooldown": await gemini.cooldown_left(),
        },
    }


@router.get('/admin/api-keys')
async def list_api_keys(user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    return {'keys': await key_pool.overview(), 'server_key_configured': bool(gemini.GEMINI_API_KEY), 'limit': key_pool.MAX_KEYS}


@router.post('/admin/api-keys')
async def add_api_keys(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    raw = payload.get('keys')
    if not isinstance(raw, str) or len(raw) > 4000:
        raise HTTPException(422, 'Передайте ключи текстом, каждый с новой строки')
    try:
        added = await key_pool.add_many(raw)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'added': added}


@router.patch('/admin/api-keys/{key_id}')
async def change_api_key(key_id: int, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    enabled = payload.get('enabled')
    if type(enabled) is not bool or set(payload) != {'enabled'}:
        raise HTTPException(422, 'Передайте enabled: true или false')
    changed = await db.update('UPDATE service_api_keys SET enabled=? WHERE id=?', (enabled, key_id))
    if not changed:
        raise HTTPException(404, 'Ключ не найден')
    return {'ok': True}


@router.delete('/admin/api-keys/{key_id}')
async def remove_api_key(key_id: int, user: dict = Depends(current_user)) -> dict:
    await require_admin(user)
    if not await db.update('DELETE FROM service_api_keys WHERE id=?', (key_id,)):
        raise HTTPException(404, 'Ключ не найден')
    return {'ok': True}


@router.post("/admin/users/{tg_id}")
async def admin_update_user(
    tg_id: int, payload: dict = Body(...), user: dict = Depends(current_user)
) -> dict:
    await require_admin(user)
    fields = {}
    if "daily_limit" in payload:
        value = payload['daily_limit']
        if type(value) is not int or not 0 <= value <= 100000:
            raise HTTPException(422, 'Дневной лимит: целое число от 0 до 100000')
        fields["daily_limit"] = value
    if "blocked" in payload:
        if type(payload['blocked']) is not bool:
            raise HTTPException(422, 'blocked: ожидается true/false')
        fields["blocked"] = int(payload["blocked"])
    if not fields:
        raise HTTPException(400, "нечего менять")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    await db.execute(f"UPDATE users SET {assignments} WHERE tg_id = ?", (*fields.values(), tg_id))
    return await db.fetch_one("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
