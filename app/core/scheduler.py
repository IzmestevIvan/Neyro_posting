import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot

from app import db
from app.ai import gemini, pipeline
from app.config import DELAY_MODES, PACE_MODES, POLL_INTERVAL
from app.core import adbook, publisher
from app.core.filters import find_duplicate, fingerprint, stopword_hit
from app.sources import rss, telegram_web

log = logging.getLogger("scheduler")

DIGEST_MAX_LEN = 350
DUP_WINDOW = 200

# Rows the panel no longer shows are pruned so the table cannot grow without bound; the
# per-day counters in stats_daily survive, so the lifetime numbers stay correct.
KEEP_TERMINAL_DAYS = 14
KEEP_PUBLISHED_DAYS = 90

state = {
    "activity": "запуск",
    "polling": None,
    "processing": 0,
    "last_error": None,
    "blocked_until": None,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def channel_tz(channel: dict) -> ZoneInfo:
    try:
        return ZoneInfo(channel.get("tz") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def in_window(local: datetime, channel: dict) -> bool:
    start, end = channel["window_start"], channel["window_end"]
    return start <= local.hour < end if start < end else local.hour >= start or local.hour < end


def next_window_start(local: datetime, channel: dict) -> datetime:
    # window_end may legitimately be 24 ("до полуночи"); as a start hour that is midnight.
    start = channel["window_start"] % 24
    candidate = local.replace(hour=start, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


async def _plan_publish_at(channel: dict) -> datetime:
    low, high = DELAY_MODES.get(channel["delay_mode"], (0, 0))
    delay = random.randint(low, high) if high else 0
    candidate = now_utc() + timedelta(seconds=delay)

    pace = PACE_MODES.get(channel["pace"], 0)
    if pace:
        key = f"next_slot:{channel['id']}"
        previous = await db.get_kv(key)
        window_hours = (channel["window_end"] - channel["window_start"]) % 24 or 24
        interval = timedelta(seconds=window_hours * 3600 / pace)
        if previous:
            earliest = datetime.fromisoformat(previous) + interval
            candidate = max(candidate, earliest)
        await db.set_kv(key, candidate.isoformat())

    tz = channel_tz(channel)
    local = candidate.astimezone(tz)
    if not in_window(local, channel):
        local = next_window_start(local, channel)
    return local.astimezone(timezone.utc)


async def _known_fingerprints(channel_id: int) -> list[tuple[int, str]]:
    rows = await db.fetch_all(
        "SELECT id, fingerprint FROM posts WHERE channel_id = ? AND fingerprint IS NOT NULL "
        "ORDER BY id DESC LIMIT ?",
        (channel_id, DUP_WINDOW),
    )
    return [(r["id"], r["fingerprint"]) for r in rows]


def _pick_new(items: list, last_uid: Optional[str]) -> list:
    if not items:
        return []
    if not last_uid:
        return items[-1:]
    uids = [i.uid for i in items]
    if last_uid in uids:
        return items[uids.index(last_uid) + 1 :]
    return items[-5:]


async def poll_source(client: httpx.AsyncClient, channel: dict, source: dict) -> int:
    if source["kind"] == "rss":
        items, title = await rss.fetch(source["ref"])
        median = 0
    else:
        items, title = await telegram_web.fetch(client, source["ref"])
        median = telegram_web.median_views(items)

    fresh = _pick_new(items, source["last_uid"])
    if items:
        await db.execute(
            "UPDATE sources SET last_uid = ?, checked_at = ?, error = NULL, "
            "title = COALESCE(title, ?), median_views = ? WHERE id = ?",
            (items[-1].uid, db.utcnow(), title, median or source["median_views"], source["id"]),
        )

    created = 0
    for item in fresh:
        exists = await db.fetch_one(
            "SELECT id FROM posts WHERE channel_id = ? AND uid = ?", (channel["id"], item.uid)
        )
        if exists:
            continue
        await db.execute(
            "INSERT INTO posts (channel_id, source_id, uid, url, source_title, raw_text, media, "
            "views, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)",
            (
                channel["id"],
                source["id"],
                item.uid,
                item.url,
                item.source_title or source["title"],
                item.text,
                json.dumps(item.media, ensure_ascii=False),
                item.views,
                db.utcnow(),
            ),
        )
        created += 1
    return created


async def poll_channel(client: httpx.AsyncClient, channel: dict) -> None:
    sources = await db.fetch_all(
        "SELECT * FROM sources WHERE channel_id = ? AND enabled = 1", (channel["id"],)
    )
    for source in sources:
        state["polling"] = source["ref"]
        try:
            await poll_source(client, channel, source)
        except Exception as exc:
            log.warning("source %s failed: %s", source["ref"], exc)
            await db.execute(
                "UPDATE sources SET error = ?, checked_at = ? WHERE id = ?",
                (str(exc)[:200], db.utcnow(), source["id"]),
            )
        await asyncio.sleep(1.5)
    state["polling"] = None


async def _reject(post: dict, status: str, reason: str, stat_field: str) -> None:
    await db.execute(
        "UPDATE posts SET status = ?, reason = ? WHERE id = ?", (status, reason, post["id"])
    )
    await db.bump_stat(post["channel_id"], now_utc().date(), stat_field)


async def process_post(bot: Bot, post: dict, channel: dict) -> None:
    media = json.loads(post["media"] or "[]")
    day = now_utc().date()

    if channel["media_only"] and not media:
        return await _reject(post, "filtered", "нет медиа", "filtered")

    hit = stopword_hit(post["raw_text"] or "", channel["stopwords"])
    if hit:
        return await _reject(post, "filtered", f"стоп-слово «{hit}»", "filtered")

    if channel["hits_only"] and post["source_id"]:
        source = await db.fetch_one("SELECT median_views FROM sources WHERE id = ?", (post["source_id"],))
        median = (source or {}).get("median_views") or 0
        if median and post["views"] < median:
            return await _reject(post, "filtered", f"ниже медианы источника ({post['views']}<{median})", "filtered")

    prints = fingerprint(post["raw_text"] or "")
    duplicate_of = find_duplicate(prints, await _known_fingerprints(channel["id"]))
    await db.execute("UPDATE posts SET fingerprint = ? WHERE id = ?", (prints, post["id"]))
    if duplicate_of:
        return await _reject(post, "duplicate", f"дубль поста #{duplicate_of}", "duplicates")

    api_key = channel["gemini_key"] or None
    voice = channel["voice_sample"] or "" if channel["channel_voice"] else ""

    try:
        result = await pipeline.process(
            post["raw_text"] or "",
            quality=channel["quality"],
            instructions=channel["instructions"],
            lang=channel["lang"],
            api_key=api_key,
            voice_sample=voice,
        )
    except gemini.NoKeyError:
        # Leave the post as 'new' so it is picked up once a key is configured, but stop the
        # loop from re-reading the same rows every few seconds until then.
        state["last_error"] = "не задан ключ Gemini"
        state["blocked_until"] = now_utc() + timedelta(minutes=5)
        return
    except gemini.AIError as exc:
        state["last_error"] = str(exc)[:150]
        return await _reject(post, "failed", f"ИИ недоступен: {exc}"[:200], "filtered")

    if result.ai_requests:
        await db.bump_stat(channel["id"], day, "ai_requests", result.ai_requests)

    if not result.ok:
        if result.is_ad:
            # A recurring advertiser in your sources is a buyer already paying for reach in
            # your niche — keep the offer instead of dropping it.
            await adbook.record(post, result.ad_score, result.ad_reasons)
        return await _reject(post, "filtered", result.reason or "не прошёл проверку", "filtered")

    fact_check = json.dumps(result.fact_check, ensure_ascii=False) if result.fact_check else None
    short = len(result.text or "") <= DIGEST_MAX_LEN

    if channel["digest_enabled"] and short and not post["is_manual"]:
        await db.execute(
            "UPDATE posts SET status = 'digest', text_out = ?, fact_check = ? WHERE id = ?",
            (result.text, fact_check, post["id"]),
        )
        return

    if channel["autopost"]:
        publish_at = await _plan_publish_at(channel)
        await db.execute(
            "UPDATE posts SET status = 'approved', text_out = ?, fact_check = ?, publish_at = ? WHERE id = ?",
            (result.text, fact_check, publish_at, post["id"]),
        )
    else:
        await db.execute(
            "UPDATE posts SET status = 'pending', text_out = ?, fact_check = ? WHERE id = ?",
            (result.text, fact_check, post["id"]),
        )
        from app.bot.cards import send_moderation_card

        await send_moderation_card(bot, channel, post["id"])


async def publish_due(bot: Bot) -> None:
    rows = await db.fetch_all(
        "SELECT p.*, c.owner_id FROM posts p JOIN channels c ON c.id = p.channel_id "
        "WHERE p.status = 'approved' AND c.paused = 0 "
        "AND (p.publish_at IS NULL OR p.publish_at <= ?) ORDER BY p.publish_at LIMIT 10",
        (db.utcnow(),),
    )
    for post in rows:
        channel = await db.fetch_one("SELECT * FROM channels WHERE id = ?", (post["channel_id"],))
        if not channel:
            continue
        if await publisher.quota_left(post["owner_id"]) <= 0:
            # The quota frees up at midnight UTC, so keep the post queued instead of
            # burning it — marking it failed would silently lose the day's backlog.
            continue
        try:
            await publisher.publish_post(bot, post, channel)
        except publisher.AlreadyPublished:
            continue
        except Exception:
            # publish_post already recorded the attempt and the reason.
            log.warning("публикация поста %s не удалась", post["id"], exc_info=True)


async def run_digest(bot: Bot, channel: dict) -> None:
    rows = await db.fetch_all(
        "SELECT * FROM posts WHERE channel_id = ? AND status = 'digest' ORDER BY id", (channel["id"],)
    )
    if not rows:
        return
    texts = [r["text_out"] for r in rows if r["text_out"]]
    try:
        summary = await pipeline.make_digest(
            texts, channel["instructions"], channel["lang"], channel["gemini_key"] or None
        )
    except gemini.AIError as exc:
        log.warning("digest failed: %s", exc)
        return

    message = await publisher.publish(bot, channel, summary, [])
    now = now_utc()
    ids = [r["id"] for r in rows]
    await db.execute(
        "UPDATE posts SET status = 'published', message_id = ?, published_at = ? "
        f"WHERE id IN ({','.join('?' * len(ids))})",
        (message.message_id, now, *ids),
    )
    await db.bump_stat(channel["id"], now.date(), "published")
    await db.execute(
        "UPDATE channels SET digest_sent_on = ? WHERE id = ?",
        (now.astimezone(channel_tz(channel)).date(), channel["id"]),
    )


async def digest_due(bot: Bot) -> None:
    channels = await db.fetch_all("SELECT * FROM channels WHERE digest_enabled = 1 AND paused = 0")
    for channel in channels:
        local = now_utc().astimezone(channel_tz(channel))
        try:
            hour, minute = (int(x) for x in channel["digest_time"].split(":"))
        except ValueError:
            continue
        if channel["digest_sent_on"] == local.date():
            continue
        if (local.hour, local.minute) >= (hour, minute):
            await run_digest(bot, channel)


async def save_snapshot(channel_id: int, day, subscribers: int, avg_views: int) -> None:
    """Дневной снимок берётся по максимуму: опросов за сутки много, а провал одного из них
    (Telegram не ответил) не должен обнулять уже записанное число."""
    await db.execute(
        "INSERT INTO stats_daily (channel_id, day, subscribers, avg_views) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (channel_id, day) DO UPDATE SET "
        # GREATEST, а не MAX: в Postgres MAX существует только как агрегатная функция.
        "subscribers = GREATEST(excluded.subscribers, stats_daily.subscribers), "
        "avg_views = GREATEST(excluded.avg_views, stats_daily.avg_views)",
        (channel_id, day, subscribers, avg_views),
    )


async def refresh_stats(bot: Bot) -> None:
    day = now_utc().date()
    channels = await db.fetch_all("SELECT * FROM channels")
    async with httpx.AsyncClient() as client:
        for channel in channels:
            subscribers, avg_views = 0, 0
            try:
                target = channel["chat_id"] or f"@{(channel['username'] or '').lstrip('@')}"
                subscribers = await bot.get_chat_member_count(target)
            except Exception as exc:
                log.debug("member count failed for %s: %s", channel["id"], exc)
            if channel["username"]:
                try:
                    items, _ = await telegram_web.fetch(client, channel["username"])
                    views = [i.views for i in items[-10:] if i.views]
                    avg_views = int(sum(views) / len(views)) if views else 0
                except Exception as exc:
                    log.debug("own channel views failed for %s: %s", channel["id"], exc)
            if subscribers or avg_views:
                await save_snapshot(channel["id"], day, subscribers, avg_views)


async def prune_posts() -> None:
    today = now_utc().date().isoformat()
    if await db.get_kv("pruned_on") == today:
        return
    await db.set_kv("pruned_on", today)

    terminal = await db.update(
        "DELETE FROM posts WHERE status IN ('filtered', 'duplicate', 'rejected', 'failed') "
        "AND created_at < ?",
        (now_utc() - timedelta(days=KEEP_TERMINAL_DAYS),),
    )
    published = await db.update(
        "DELETE FROM posts WHERE status = 'published' AND published_at < ?",
        (now_utc() - timedelta(days=KEEP_PUBLISHED_DAYS),),
    )
    if terminal or published:
        log.info("очистка: удалено %s отсеянных и %s опубликованных", terminal, published)


async def weekly_report(bot: Bot) -> None:
    if now_utc().weekday() != 6:
        return
    today = now_utc().date().isoformat()
    if await db.get_kv("weekly_report_on") == today:
        return
    await db.set_kv("weekly_report_on", today)

    channels = await db.fetch_all("SELECT * FROM channels")
    for channel in channels:
        rows = await db.fetch_all(
            "SELECT * FROM stats_daily WHERE channel_id = ? AND day >= CURRENT_DATE - 7 ORDER BY day",
            (channel["id"],),
        )
        if not rows:
            continue
        published = sum(r["published"] for r in rows)
        subs = [r["subscribers"] for r in rows if r["subscribers"]]
        views = [r["avg_views"] for r in rows if r["avg_views"]]
        delta = subs[-1] - subs[0] if len(subs) > 1 else 0
        text = (
            f"<b>Отчёт за неделю — {channel['title'] or channel['username']}</b>\n\n"
            f"Опубликовано: {published}\n"
            f"Подписчики: {subs[-1] if subs else '—'} ({delta:+d})\n"
            f"Средние просмотры: {int(sum(views) / len(views)) if views else '—'}"
        )
        try:
            await bot.send_message(channel["owner_id"], text, parse_mode="HTML")
        except Exception as exc:
            log.debug("weekly report send failed: %s", exc)


async def poll_loop(bot: Bot) -> None:
    while True:
        try:
            channels = await db.fetch_all("SELECT * FROM channels WHERE paused = 0")
            state["activity"] = "проверяю источники" if channels else "нет активных каналов"
            async with httpx.AsyncClient() as client:
                for channel in channels:
                    await poll_channel(client, channel)
        except Exception as exc:
            log.exception("poll loop error")
            state["last_error"] = str(exc)[:150]
        state["activity"] = "простой — жду новые посты"
        await asyncio.sleep(POLL_INTERVAL)


async def process_loop(bot: Bot) -> None:
    while True:
        try:
            blocked = state["blocked_until"]
            if blocked and now_utc() < blocked:
                state["activity"] = "жду ключ Gemini"
                await asyncio.sleep(10)
                continue
            state["blocked_until"] = None
            rows = await db.fetch_all(
                "SELECT p.* FROM posts p JOIN channels c ON c.id = p.channel_id "
                "WHERE p.status = 'new' AND c.paused = 0 ORDER BY p.id LIMIT 5"
            )
            state["processing"] = len(rows)
            for post in rows:
                channel = await db.fetch_one(
                    "SELECT * FROM channels WHERE id = ?", (post["channel_id"],)
                )
                if channel:
                    state["activity"] = "переписываю пост"
                    await process_post(bot, post, channel)
        except Exception as exc:
            log.exception("process loop error")
            state["last_error"] = str(exc)[:150]
        state["processing"] = 0
        await asyncio.sleep(10)


async def publish_loop(bot: Bot) -> None:
    while True:
        try:
            await publish_due(bot)
            await digest_due(bot)
        except Exception as exc:
            log.exception("publish loop error")
            state["last_error"] = str(exc)[:150]
        await asyncio.sleep(20)


async def stats_loop(bot: Bot) -> None:
    while True:
        try:
            await refresh_stats(bot)
            await weekly_report(bot)
            await prune_posts()
        except Exception:
            log.exception("ошибка в цикле статистики")
        await asyncio.sleep(1800)


def start(bot: Bot) -> list[asyncio.Task]:
    return [
        asyncio.create_task(poll_loop(bot)),
        asyncio.create_task(process_loop(bot)),
        asyncio.create_task(publish_loop(bot)),
        asyncio.create_task(stats_loop(bot)),
    ]
