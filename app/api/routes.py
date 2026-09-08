import json
import re
from datetime import date, timedelta
from typing import Any, Optional

import httpx
import psutil
from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app import db
from app.ai import gemini
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
    DELAY_MODES,
    LANGUAGES,
    PACE_MODES,
    TIMEZONES,
)
from app.core import promo, publisher, scheduler
from app.sources import rss, telegram_web

router = APIRouter(prefix="/api")

BOOL_FIELDS = {
    "autopost", "paused", "channel_voice", "hits_only", "media_only", "digest_enabled", "watermark",
}
TEXT_FIELDS = {"instructions", "stopwords", "signature_text", "signature_url", "gemini_key"}
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
QUALITY = {"fast", "balanced", "super"}


def _clean_settings(payload: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in BOOL_FIELDS:
            out[key] = int(bool(value))
        elif key in TEXT_FIELDS:
            out[key] = str(value)[:4000]
        elif key == "delay_mode" and value in DELAY_MODES:
            out[key] = value
        elif key == "pace" and value in PACE_MODES:
            out[key] = value
        elif key == "tz" and value in TIMEZONES:
            out[key] = value
        elif key == "lang" and value in LANGUAGES:
            out[key] = value
        elif key == "quality" and value in QUALITY:
            out[key] = value
        elif key == "digest_time" and TIME_RE.match(str(value)):
            out[key] = str(value)
        elif key == "window_start":
            out[key] = max(0, min(23, int(value)))
        elif key == "window_end":
            out[key] = max(1, min(24, int(value)))
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
        "channels": channels,
        "languages": LANGUAGES,
        "timezones": TIMEZONES,
        "delay_modes": list(DELAY_MODES),
        "pace_modes": list(PACE_MODES),
    }


@router.post("/promo/redeem")
async def redeem_promo(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict:
    try:
        result = await promo.redeem(str(payload.get("code", "")), user["tg_id"])
    except promo.PromoError as exc:
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
        raise HTTPException(502, "не удалось проверить права на канал; повторите позже") from exc

    existing = await db.fetch_one(
        "SELECT id FROM channels WHERE owner_id = ? AND chat_id = ?", (user["tg_id"], chat_id)
    )
    if existing:
        raise HTTPException(400, "канал уже добавлен")

    channel_id = await db.insert(
        "INSERT INTO channels (owner_id, chat_id, username, title, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["tg_id"], chat_id, resolved or username, title, db.utcnow()),
    )
    await db.set_kv(f"active:{user['tg_id']}", channel_id)
    return await db.fetch_one("SELECT * FROM channels WHERE id = ?", (channel_id,))


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

    assignments = ", ".join(f"{key} = ?" for key in fields)
    await db.execute(
        f"UPDATE channels SET {assignments} WHERE id = ?", (*fields.values(), channel_id)
    )
    await db.set_kv(f"active:{user['tg_id']}", channel_id)
    return await db.fetch_one("SELECT * FROM channels WHERE id = ?", (channel_id,))


@router.get("/channels/{channel_id}/stats")
async def channel_stats(channel_id: int, user: dict = Depends(active_user)) -> dict:
    channel = await owned_channel(channel_id, user)

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
        " COUNT(*) FILTER (WHERE status = 'pending') AS pending"
        " FROM posts WHERE channel_id = ?",
        (channel_id,),
    )
    sources = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM sources WHERE channel_id = ?", (channel_id,)
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

    return {
        "published": (totals or {}).get("published") or 0,
        "today": (today_row or {}).get("published") or 0,
        "queued": (counts or {}).get("queued") or 0,
        "pending": (counts or {}).get("pending") or 0,
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
        "system": await _system_health() if is_admin(user) else None,
    }


async def _system_health() -> dict:
    memory = psutil.virtual_memory()
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram_used": round(memory.used / 1024**3, 1),
        "ram_total": round(memory.total / 1024**3, 1),
        "activity": scheduler.state["activity"],
        "polling": scheduler.state["polling"],
        "last_publish_at": await db.get_kv("last_publish_at"),
        "model_cooldown": await gemini.cooldown_left(),
        "last_error": scheduler.state["last_error"],
    }


@router.get("/channels/{channel_id}/feed")
async def channel_feed(channel_id: int, user: dict = Depends(active_user)) -> list[dict]:
    await owned_channel(channel_id, user)
    rows = await db.fetch_all(
        "SELECT id, url, source_title, text_out, raw_text, media, fact_check, created_at "
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
    return await db.fetch_all(
        "SELECT id, status, reason, source_title, substr(COALESCE(text_out, raw_text), 1, 200) AS preview, "
        "created_at, published_at FROM posts WHERE channel_id = ? AND status != 'pending' "
        "ORDER BY id DESC LIMIT 50",
        (channel_id,),
    )


@router.post("/posts/{post_id}/{action}")
async def post_action(
    post_id: int, action: str, request: Request, user: dict = Depends(active_user)
) -> dict:
    post = await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post_id,))
    if not post:
        raise HTTPException(404, "пост не найден")
    channel = await owned_channel(post["channel_id"], user)
    bot = request.app.state.bot

    if action == "reject":
        if not await publisher.reject_post(post_id):
            raise HTTPException(409, "статус поста изменился; обновите ленту")
        return {"ok": True}

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
        if not await publisher.replace_draft(post, result.text, result.fact_check):
            raise HTTPException(409, "пост уже изменён или опубликован; обновите ленту")
        return {"ok": True, "text": result.text}

    if action == "approve":
        if await publisher.quota_left(user["tg_id"]) <= 0:
            raise HTTPException(429, "исчерпан дневной лимит")
        try:
            await publisher.publish_post(bot, post, channel)
        except publisher.AlreadyPublished as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"не удалось опубликовать: {exc}") from exc
        return {"ok": True}

    raise HTTPException(400, "неизвестное действие")


@router.post("/channels/{channel_id}/publish_now")
async def publish_now(
    channel_id: int, request: Request, user: dict = Depends(active_user)
) -> dict:
    channel = await owned_channel(channel_id, user)
    # Кнопка «Опубликовать сейчас» намеренно игнорирует паузу канала, окно публикации,
    # задержку, темп и отложенный дайджест: пользователь нажал её сам и ждёт пост немедленно.
    # Не игнорируется только дневной лимит — это условие тарифа, а не расписания.
    post = await db.fetch_one(
        "SELECT * FROM posts WHERE channel_id = ? AND status IN ('approved', 'pending', 'digest', 'failed') "
        "AND NULLIF(trim(text_out), '') IS NOT NULL "
        "ORDER BY status = 'approved' DESC, status = 'pending' DESC, id LIMIT 1",
        (channel_id,),
    )
    if not post:
        raise HTTPException(404, "нет готовых постов — дождитесь новых из источников")
    if await publisher.quota_left(user["tg_id"]) <= 0:
        raise HTTPException(429, "исчерпан дневной лимит")
    try:
        await publisher.publish_post(request.app.state.bot, post, channel)
    except publisher.AlreadyPublished as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"не удалось опубликовать: {exc}") from exc
    return {"ok": True, "post_id": post["id"]}


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

    title = None
    try:
        if is_rss:
            _, title = await rss.fetch(ref)
        else:
            async with httpx.AsyncClient() as client:
                _, title = await telegram_web.fetch(client, ref)
    except Exception as exc:
        raise HTTPException(400, f"источник недоступен: {exc}") from exc

    try:
        source_id = await db.insert(
            "INSERT INTO sources (channel_id, kind, ref, title, created_at) VALUES (?, ?, ?, ?, ?)",
            (channel_id, kind, ref, title, db.utcnow()),
        )
    except Exception as exc:
        raise HTTPException(400, "источник уже добавлен") from exc
    return await db.fetch_one("SELECT * FROM sources WHERE id = ?", (source_id,))


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, user: dict = Depends(active_user)) -> dict:
    source = await db.fetch_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if not source:
        raise HTTPException(404, "источник не найден")
    await owned_channel(source["channel_id"], user)
    await db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return {"ok": True}


@router.get("/channels/{channel_id}/ads")
async def list_ads(channel_id: int, user: dict = Depends(active_user)) -> list[dict]:
    await owned_channel(channel_id, user)
    rows = await db.fetch_all(
        "SELECT * FROM ad_offers WHERE channel_id = ? AND status != 'archived' "
        "ORDER BY seen_count DESC, last_seen_at DESC LIMIT 100",
        (channel_id,),
    )
    for row in rows:
        row["contacts"] = json.loads(row["contacts"] or "[]")
        row["reasons"] = json.loads(row["reasons"] or "[]")
    return rows


@router.post("/ads/{ad_id}/{status}")
async def set_ad_status(ad_id: int, status: str, user: dict = Depends(active_user)) -> dict:
    if status not in ("new", "contacted", "archived"):
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
    except promo.PromoError as exc:
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


@router.post("/admin/users/{tg_id}")
async def admin_update_user(
    tg_id: int, payload: dict = Body(...), user: dict = Depends(current_user)
) -> dict:
    await require_admin(user)
    fields = {}
    if "daily_limit" in payload:
        fields["daily_limit"] = max(0, int(payload["daily_limit"]))
    if "blocked" in payload:
        fields["blocked"] = int(bool(payload["blocked"]))
    if not fields:
        raise HTTPException(400, "нечего менять")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    await db.execute(f"UPDATE users SET {assignments} WHERE tg_id = ?", (*fields.values(), tg_id))
    return await db.fetch_one("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
