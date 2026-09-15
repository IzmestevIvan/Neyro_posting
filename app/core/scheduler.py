import asyncio
import html
import hashlib
import json
import logging
import random
from collections import OrderedDict
from time import monotonic
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot

from app import db
from app.ai import gemini, pipeline
from app.config import ADMIN_IDS, DELAY_MODES, PACE_MODES, POLL_INTERVAL
from app.core import access, adbook, publisher, news_policy, runtime
from app.core.operations import channel_scope
from app.core.filters import find_duplicate, fingerprint, stopword_hit
from app.sources import rss, telegram_web

log = logging.getLogger("scheduler")

DIGEST_MAX_LEN = 350
DUP_WINDOW = 200

# Rows the panel no longer shows are pruned so the table cannot grow without bound; the
# per-day counters in stats_daily survive, so the lifetime numbers stay correct.
KEEP_TERMINAL_DAYS = 14
KEEP_PUBLISHED_DAYS = 90
KEEP_DELIVERY_DAYS = 30
KEEP_AD_DAYS = 90
_alert_times = {}
_source_cache = OrderedDict()


def source_uid(source, item):
    # RSS GUIDs are unique only within a feed. Two feeds may both use "123".
    if source['kind'] == 'rss':
        return 'rss:' + hashlib.sha256((source['ref']+'\0'+item.uid).encode()).hexdigest()
    return item.uid


async def fetch_source(client, source):
    """Shared donors are downloaded once per minute, with bounded cache size."""
    key = (source['kind'], source['ref'])
    lock = runtime.channel_lock(('source', *key))
    async with lock:
        cached = _source_cache.get(key)
        if cached and monotonic()-cached[0] < 60:
            _source_cache.move_to_end(key)
            return cached[1]
        async with runtime.source_slots:
            result = await (rss.fetch(source['ref']) if source['kind']=='rss' else telegram_web.fetch(client, source['ref']))
        _source_cache[key] = (monotonic(),result)
        _source_cache.move_to_end(key)
        while len(_source_cache)>128:
            _source_cache.popitem(last=False)
        return result


async def alert_admins(bot: Bot, key: str, text: str, *, cooldown: timedelta = timedelta(hours=6)) -> None:
    """Report an actionable operational failure once, without turning Telegram into a log sink."""
    from app.core.operations import ErrorJournal
    if any(isinstance(handler, ErrorJournal) for handler in logging.getLogger().handlers):
        logging.getLogger(f'operations.{key}').error(text)
        return
    if not ADMIN_IDS:
        return
    now = now_utc()
    last = _alert_times.get(key)
    try:
        last = await db.get_kv(f"alert:{key}") or last
    except Exception:
        pass  # A DB outage is precisely when an alert must still work.
    try:
        if last and now - datetime.fromisoformat(last) < cooldown:
            return
    except (TypeError, ValueError):
        pass
    # Mark before sending: a broken Telegram connection must not cause a retry storm.
    if len(_alert_times) >= 512:
        _alert_times.clear()
    _alert_times[key] = now.isoformat()
    try:
        await db.set_kv(f"alert:{key}", now.isoformat())
    except Exception:
        pass
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"⚠️ <b>Нейропостинг: требуется проверка</b>\n\n{html.escape(text)}", parse_mode="HTML")
        except Exception:
            log.warning("cannot send operational alert to admin %s", admin_id, exc_info=True)

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


async def expire_news() -> int:
    return await db.update(
        "UPDATE posts p SET status='expired',reason='Новость устарела или в канале уже вышел более свежий материал' "
        "FROM channels c WHERE c.id=p.channel_id "
        "AND p.is_manual=0 AND p.status IN ('new','pending','approved','failed','digest') "
        "AND (p.created_at < now()-CASE WHEN c.delay_mode='instant' AND c.digest_enabled=0 "
        "THEN interval '1 hour' ELSE interval '24 hours' END OR "
        "(c.delay_mode='instant' AND c.digest_enabled=0 AND EXISTS (SELECT 1 FROM posts newer "
        "WHERE newer.channel_id=p.channel_id AND newer.status='published' AND newer.is_manual=0 "
        "AND newer.created_at>p.created_at))) "
        "AND NOT EXISTS(SELECT 1 FROM delivery_attempts d WHERE d.post_id=p.id AND (d.status!='failed' OR d.receipts!='[]'::jsonb))")


async def _plan_publish_at(channel: dict) -> datetime:
    low, high = DELAY_MODES.get(channel["delay_mode"], (0, 0))
    delay = random.randint(low, high) if high else 0
    candidate = now_utc() + timedelta(seconds=delay)

    pace = PACE_MODES.get(channel["pace"], 0)
    if pace:
        previous = await db.fetch_one('SELECT max(published_at) AS sent FROM posts WHERE channel_id=? AND status=\'published\'', (channel['id'],))
        window_hours = (channel["window_end"] - channel["window_start"]) % 24 or 24
        interval = timedelta(seconds=window_hours * 3600 / pace)
        if previous and previous['sent']:
            earliest = previous['sent'] + interval
            candidate = max(candidate, earliest)

    tz = channel_tz(channel)
    local = candidate.astimezone(tz)
    if not in_window(local, channel):
        local = next_window_start(local, channel)
    return local.astimezone(timezone.utc)


async def _known_fingerprints(channel_id: int, exclude_id: int) -> list[tuple[int, str]]:
    rows = await db.fetch_all(
        "SELECT id, fingerprint FROM posts WHERE channel_id = ? AND id != ? AND fingerprint IS NOT NULL "
        "AND status IN ('pending','approved','digest','published','publishing','uncertain','partial') "
        "ORDER BY id DESC LIMIT ?",
        (channel_id, exclude_id, DUP_WINDOW),
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


def _chronological(items: list) -> list:
    """Feeds disagree about their order; normalize known timestamps before moving a cursor."""
    indexed = list(enumerate(items))
    indexed.sort(key=lambda pair: (news_policy.source_date(pair[1].date) is not None,
                                   news_policy.source_date(pair[1].date) or datetime.min.replace(tzinfo=timezone.utc),
                                   pair[0]))
    return [item for _, item in indexed]


async def poll_source(client: httpx.AsyncClient, channel: dict, source: dict) -> int:
    items, title = await fetch_source(client, source)
    median = 0 if source['kind']=='rss' else telegram_web.median_views(items)

    items = _chronological(items)
    fresh = _pick_new(items, source["last_uid"])
    created = 0
    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Serialize concurrent readers of this source; commit cursor and rows together.
            current_channel = await conn.fetchrow('SELECT business_mode,paused FROM channels WHERE id=$1 FOR UPDATE', channel['id'])
            if not current_channel or current_channel['business_mode'] or current_channel['paused']:
                return 0
            locked = await conn.fetchrow("SELECT last_uid FROM sources WHERE id=$1 FOR UPDATE", source["id"])
            if not locked or locked["last_uid"] != source["last_uid"]:
                return 0  # A newer poll has committed; never rewind its cursor.
            for item in fresh:
                uid = source_uid(source, item)
                item_date = news_policy.source_date(item.date)
                if item_date and item_date > now_utc()+timedelta(minutes=1):
                    continue
                if news_policy.realtime(channel):
                    if channel['paused'] or not news_policy.window_open(channel, now_utc()):
                        continue
                    if item_date and (item_date < now_utc() - news_policy.FRESH_FOR or not news_policy.window_open(channel, item_date)):
                        continue
                exists = await conn.fetchval(
                    "SELECT id FROM posts WHERE channel_id=$1 AND (uid=$2 OR (uid=$3 AND source_id=$4))",
                    channel["id"], uid, item.uid, source['id']
                )
                if exists:
                    continue
                await conn.execute(
                    "INSERT INTO posts (channel_id, source_id, uid, url, source_title, raw_text, media, "
                    "views, status, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'new',$9)",
                    channel["id"], source["id"], uid, item.url,
                    item.source_title or source["title"], item.text[:16000],
                    json.dumps(item.media[:10], ensure_ascii=False), item.views, min(item_date, now_utc()) if item_date else db.utcnow(),
                )
                created += 1
            if items:
                await conn.execute(
                    "UPDATE sources SET last_uid=$1, checked_at=$2, error=NULL, "
                    "title=COALESCE(title,$3), median_views=$4 WHERE id=$5",
                    items[-1].uid, db.utcnow(), title, median or source["median_views"], source["id"],
                )
            else:
                await conn.execute('UPDATE sources SET checked_at=$1,error=NULL WHERE id=$2', db.utcnow(), source['id'])

    return created


async def poll_channel(client: httpx.AsyncClient, channel: dict, bot: Bot = None) -> None:
    if channel.get('business_mode') or channel.get('paused'):
        return
    if not await access.owner_has_access(channel["owner_id"]):
        return
    sources = await db.fetch_all(
        "SELECT * FROM sources WHERE channel_id = ? AND enabled = 1", (channel["id"],)
    )
    async def poll_one(source):
        state["polling"] = source["ref"]
        try:
            async with asyncio.timeout(45):
                await poll_source(client, channel, source)
        except Exception as exc:
            log.warning("source %s failed: %s", source["ref"], type(exc).__name__, exc_info=True)
            await db.execute(
                "UPDATE sources SET error = ?, checked_at = ? WHERE id = ?",
                ((str(exc) or type(exc).__name__)[:200], db.utcnow(), source["id"]),
            )
            if bot and source.get('error'):
                await alert_admins(bot, "sources", "Повторяются ошибки источников. Недоступные источники отмечены в панели.")
    await runtime.bounded_map(sources, poll_one, 2)
    state["polling"] = None


async def _reject(post: dict, status: str, reason: str, stat_field: str) -> None:
    changed = await db.update(
        "UPDATE posts SET status = ?, reason = ? WHERE id = ? AND status='new'", (status, reason, post["id"])
    )
    if changed:
        await db.bump_stat(post["channel_id"], now_utc().date(), stat_field)


async def defer_processing(post: dict, reason: str) -> None:
    # A provider outage is not an editorial rejection. Retry without filling the
    # filtered counter or starving other channels. No unchecked text becomes ready.
    await db.execute("UPDATE posts SET status='new', reason=?, publish_at=? WHERE id=? AND status='new'",
                     (reason[:200], now_utc() + timedelta(minutes=15), post["id"]))


async def reconsider_filtered(channel_id: int) -> int:
    """Recheck recent filter rejects after that filter is disabled; never resend deliveries."""
    return await db.update(
        "UPDATE posts p SET status='new',reason=NULL,publish_at=NULL WHERE p.status='filtered' AND p.id IN ("
        "SELECT old.id FROM posts old JOIN channels c ON c.id=old.channel_id WHERE c.id=? "
        "AND old.status='filtered' AND old.is_manual=0 "
        "AND ((c.hits_only=0 AND old.reason LIKE 'ниже медианы источника%') "
        "OR (c.media_only=0 AND old.reason='нет медиа')) "
        "AND old.created_at>=now()-CASE WHEN c.delay_mode='instant' AND c.digest_enabled=0 "
        "THEN interval '1 hour' ELSE interval '24 hours' END "
        "AND NOT EXISTS(SELECT 1 FROM delivery_attempts d WHERE d.post_id=old.id) "
        "ORDER BY old.created_at DESC,old.id DESC LIMIT 20)", (channel_id,))


async def process_post(bot: Bot, post: dict, channel: dict, *, force_once: bool = False) -> None:
    if not await access.owner_has_access(channel['owner_id']):
        return
    lock = runtime.channel_lock(channel['id'])
    if lock.locked():
        return
    async with lock:
        fresh = await db.fetch_one("SELECT * FROM posts WHERE id=? AND status='new'", (post['id'],))
        if fresh:
            current = await db.fetch_one('SELECT * FROM channels WHERE id=?', (channel['id'],))
            if not current:
                return
            if current.get('business_mode') and not fresh['is_manual']:
                return
            if current.get('business_mode') and fresh['is_manual']:
                from app.core.business import draft_text
                try:
                    research = {}
                    text, reason, url = await draft_text(current, fresh['raw_text'] or '', research_out=research)
                except ValueError as exc:
                    await db.execute("UPDATE posts SET status='pending',text_out=NULL,business_draft=1,reason=? WHERE id=? AND status='new'",
                                     (str(exc)[:1500], fresh['id']))
                    return
                latest = await db.fetch_one('SELECT business_profile,business_mode FROM channels WHERE id=?', (channel['id'],))
                if not latest or latest['business_profile'] != current['business_profile'] or not latest['business_mode']:
                    return await defer_processing(fresh, 'Досье или режим изменились — подготовка будет повторена')
                await db.execute("UPDATE posts SET status='pending',text_out=?,reason=?,fact_check=?,business_draft=1,publish_at=NULL "
                                 "WHERE id=? AND status='new'", (text, reason, json.dumps({'research':research},ensure_ascii=False), fresh['id']))
                return
            with channel_scope(channel):
                await _process_post(bot, fresh, channel, force_once=force_once)


async def _process_post(bot: Bot, post: dict, channel: dict, *, force_once: bool = False) -> None:
    if force_once:
        channel = dict(channel, paused=0, window_start=0, window_end=24, delay_mode="instant", pace="as_they_come", digest_enabled=0)
    if news_policy.stale(post, channel):
        await db.execute("UPDATE posts SET status='expired',reason='Новость устарела' WHERE id=? AND status='new'", (post['id'],))
        return
    if channel['paused'] or (news_policy.realtime(channel) and not news_policy.window_open(channel, now_utc())):
        return
    if not await access.owner_has_access(channel["owner_id"]):
        return
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
    duplicate_of = find_duplicate(prints, await _known_fingerprints(channel["id"], post["id"]))
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
        return await defer_processing(post, "Не задан ключ Gemini — повтор через 15 минут")
    except gemini.AIError as exc:
        state["last_error"] = str(exc)[:150]
        await alert_admins(bot, "ai", "Доступные маршруты ИИ не завершили обработку. Материал сохранён, повтор через 15 минут. Проверьте квоты ключей, если сбой повторяется.")
        return await defer_processing(post, "ИИ недоступен — повторная обработка через 15 минут")

    if result.ai_requests:
        await db.bump_stat(channel["id"], day, "ai_requests", result.ai_requests)

    if not result.ok:
        if result.retryable:
            await alert_admins(bot, "ai", f"Проверка материала не завершена: {result.reason}. Материал сохранён, повтор через 15 минут.")
            return await defer_processing(post, result.reason or "ИИ временно недоступен")
        if result.is_ad:
            # A recurring advertiser in your sources is a buyer already paying for reach in
            # your niche — keep the offer instead of dropping it.
            await adbook.record(post, result.ad_score, result.ad_reasons)
        return await _reject(post, "filtered", result.reason or "не прошёл проверку", "filtered")

    fresh_channel = await db.fetch_one("SELECT * FROM channels WHERE id=?", (channel['id'],))
    if not fresh_channel:
        return
    channel = dict(fresh_channel, paused=0, window_start=0, window_end=24, delay_mode="instant", pace="as_they_come", digest_enabled=0) if force_once else fresh_channel
    if news_policy.stale(post, channel):
        await db.execute("UPDATE posts SET status='expired',reason='Новость устарела во время обработки' WHERE id=? AND status='new'", (post['id'],))
        return
    if news_policy.realtime(channel) and (channel['paused'] or not news_policy.window_open(channel, now_utc())):
        await db.execute("UPDATE posts SET status='expired',reason='Рабочее окно завершилось; новость не переносится на завтра' WHERE id=? AND status='new'", (post['id'],))
        return
    if not result.needs_review:
        await db.execute("UPDATE posts SET reason=NULL WHERE id=? AND status='new'", (post['id'],))
    if result.needs_review:
        await db.execute("UPDATE posts SET reason = ? WHERE id = ? AND status='new'",
                         ("Не удалось уверенно исключить рекламу. Требуется ручная проверка.", post["id"]))
    fact_check = json.dumps(result.fact_check, ensure_ascii=False) if result.fact_check else None
    short = len(result.text or "") <= DIGEST_MAX_LEN

    if channel["digest_enabled"] and not channel.get('business_mode') and short and not post["is_manual"] and not result.needs_review:
        await db.execute(
            "UPDATE posts SET status = 'digest', text_out = ?, fact_check = ?, publish_at=NULL, reason=NULL WHERE id = ? AND status='new'",
            (result.text, fact_check, post["id"]),
        )
        return

    if channel["autopost"] and not channel.get('business_mode') and not result.needs_review and not force_once:
        publish_at = await _plan_publish_at(channel)
        await db.execute(
            "UPDATE posts SET status = 'approved', text_out = ?, fact_check = ?, publish_at = ?, reason=NULL WHERE id = ? AND status='new'",
            (result.text, fact_check, publish_at, post["id"]),
        )
    else:
        await db.execute(
            "UPDATE posts SET status = 'pending', text_out = ?, fact_check = ?, publish_at=NULL WHERE id = ? AND status='new'",
            (result.text, fact_check, post["id"]),
        )
        # All review candidates, including manual drafts, stay in the Mini App.


async def publish_due(bot: Bot) -> None:
    if await publisher.recover_stale_deliveries():
        await alert_admins(bot, 'delivery', 'Прерванная отправка требует сверки с каналом. Автоматический повтор отключён.')
    await expire_news()
    eligible = []
    for channel in await db.fetch_all('SELECT * FROM channels WHERE paused=0 AND autopost=1'):
        if news_policy.window_open(channel, now_utc()) and await access.owner_has_access(channel['owner_id']) and await publisher.quota_left(channel['owner_id']) > 0:
            eligible.append(channel['id'])
    rows = await db.fetch_all(
        "SELECT * FROM (SELECT DISTINCT ON (p.channel_id) p.*,c.owner_id FROM posts p "
        "JOIN channels c ON c.id=p.channel_id WHERE p.status='approved' AND p.channel_id=ANY(?::bigint[]) "
        "AND (p.publish_at IS NULL OR p.publish_at<=?) "
        "ORDER BY p.channel_id,p.created_at DESC,p.id DESC) candidates ORDER BY created_at LIMIT 120",
        (eligible, db.utcnow()),
    )
    async def send(post):
        channel = await db.fetch_one("SELECT * FROM channels WHERE id = ?", (post["channel_id"],))
        if not channel:
            return
        if channel['paused'] or not news_policy.window_open(channel, now_utc()):
            return
        if await publisher.quota_left(post["owner_id"]) <= 0:
            # The quota frees up at midnight UTC, so keep the post queued instead of
            # burning it — marking it failed would silently lose the day's backlog.
            return
        try:
            await publisher.publish_post(bot, post, channel, background=True)
        except publisher.AlreadyPublished as exc:
            if 'новость уже' in str(exc):
                await db.execute("UPDATE posts SET status='duplicate',reason=? WHERE id=? AND status='approved'", (str(exc),post['id']))
            return
        except Exception:
            # publish_post already recorded the attempt and the reason.
            log.warning("публикация поста %s не удалась", post["id"], exc_info=True)
    await runtime.bounded_map(rows, send, 2)


async def run_digest(bot: Bot, channel: dict) -> None:
    fresh_channel = await db.fetch_one('SELECT * FROM channels WHERE id=?', (channel['id'],))
    if not fresh_channel or fresh_channel.get('business_mode'):
        return
    if not await access.owner_has_access(channel["owner_id"]):
        return
    # Until digest moderation is implemented, never auto-send in manual mode.
    if not channel["autopost"] or await publisher.quota_left(channel["owner_id"]) <= 0:
        return
    rows = await db.fetch_all(
        "SELECT * FROM posts WHERE channel_id = ? AND status = 'digest' "
        "AND created_at>=now()-interval '24 hours' ORDER BY created_at DESC,id DESC LIMIT 100", (channel["id"],)
    )
    if not rows:
        return
    # Keep every original inside the verifier's 5000-character input limit.
    selected, original_size = [], 0
    for row in rows:
        original = row['raw_text'] or ''
        if row['text_out'] and original and original_size + len(original) + 2 <= 5000:
            selected.append(row)
            original_size += len(original) + 2
        if len(selected) == 5:
            break
    rows = selected
    if not rows:
        return
    originals = "\n\n".join(r['raw_text'] for r in rows)
    texts = [r['text_out'] for r in rows]
    try:
        summary = await pipeline.make_digest(
            texts, channel["instructions"], channel["lang"], channel["gemini_key"] or None
        )
    except gemini.AIError as exc:
        log.warning("digest failed: %s", exc)
        return

    # A digest is one publication, not N independently published source rows.
    check = await pipeline._factcheck(originals, summary, channel['gemini_key'] or None)
    if not check or not check['ok']:
        return
    if not await access.owner_has_access(channel['owner_id']):
        return
    now = now_utc()
    local_day = now.astimezone(channel_tz(channel)).date()
    ids = [r['id'] for r in rows]
    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            fresh = await conn.fetchrow('SELECT * FROM channels WHERE id=$1 FOR UPDATE', channel['id'])
            if not fresh or fresh['paused'] or not fresh['autopost'] or fresh['digest_sent_on'] == local_day:
                return
            members = await conn.fetch('SELECT id,status FROM posts WHERE id=ANY($1::bigint[]) ORDER BY id FOR UPDATE', ids)
            if len(members) != len(ids) or any(r['status'] != 'digest' for r in members):
                return
            digest = await conn.fetchrow(
                "INSERT INTO posts (channel_id, uid, source_title, raw_text, text_out, fact_check, status, is_manual, created_at) "
                "VALUES ($1,$2,'Дайджест',$3,$4,$5,'approved',1,now()) RETURNING *",
                channel['id'], f'digest:{channel["id"]}:{local_day}', originals, summary, json.dumps(check, ensure_ascii=False))
            await conn.execute("UPDATE posts SET status='digest_item', reason=$1 WHERE id=ANY($2::bigint[])",
                               f'Включён в дайджест #{digest["id"]}', ids)
            # Reserve this day's digest even if delivery must wait for quota/retry.
            await conn.execute('UPDATE channels SET digest_sent_on=$1 WHERE id=$2', local_day, channel['id'])
    try:
        await publisher.publish_post(bot, dict(digest), dict(fresh), background=True)
    except (publisher.QuotaExceeded, publisher.AlreadyPublished, publisher.DeliveryUncertain):
        return


async def digest_due(bot: Bot) -> None:
    channels = await db.fetch_all("SELECT * FROM channels WHERE digest_enabled = 1 AND paused = 0")
    async def digest(channel):
        local = now_utc().astimezone(channel_tz(channel))
        try:
            hour, minute = (int(x) for x in channel["digest_time"].split(":"))
        except ValueError:
            return
        if channel["digest_sent_on"] == local.date():
            return
        if (local.hour, local.minute) >= (hour, minute):
            try:
                async with asyncio.timeout(240):
                    with channel_scope(channel):
                        await run_digest(bot, channel)
            except Exception:
                log.exception('digest failed: channel=%s', channel['id'])
                await alert_admins(bot, 'digest', 'Не удалось подготовить дайджест. Проверьте журнал приложения.')
    await runtime.bounded_map(channels, digest, 2)


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
                log.warning("member count failed for %s: %s", channel["id"], type(exc).__name__, exc_info=True)
            if channel["username"]:
                try:
                    items, _ = await telegram_web.fetch(client, channel["username"])
                    views = [i.views for i in items[-10:] if i.views]
                    avg_views = int(sum(views) / len(views)) if views else 0
                except Exception as exc:
                    log.warning("own channel views failed for %s: %s", channel["id"], type(exc).__name__, exc_info=True)
            if subscribers or avg_views:
                await save_snapshot(channel["id"], day, subscribers, avg_views)


async def prune_posts() -> None:
    today = now_utc().date().isoformat()
    if await db.get_kv("pruned_on") == today:
        return
    await expire_news()
    # Small commits let the 1-vCPU server continue serving requests during cleanup.
    policies = [
        ("posts", "status IN ('filtered','duplicate','rejected','failed','expired','digest_item') "
         "AND created_at < now()-interval '14 days' AND NOT EXISTS (SELECT 1 FROM delivery_attempts d "
         "WHERE d.post_id=posts.id AND d.status IN ('sending','uncertain','partial'))"),
        ("posts", "status='published' AND published_at < now()-interval '90 days'"),
        ("delivery_attempts", "started_at < now()-interval '30 days' AND status IN ('sent','failed') "
         "AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.id=delivery_attempts.post_id "
         "AND p.status IN ('publishing','uncertain','partial'))"),
        ("ad_offers", "last_seen_at < now()-interval '180 days' OR "
         "(status!='new' AND last_seen_at < now()-interval '90 days')"),
    ]
    for table, where in policies:
        while True:
            deleted = await db.update(f"DELETE FROM {table} WHERE id IN "
                                      f"(SELECT id FROM {table} WHERE {where} ORDER BY id LIMIT 500)")
            if deleted < 500:
                break
            await asyncio.sleep(0.05)
    await db.execute("DELETE FROM kv WHERE k LIKE 'forward:%' AND CASE WHEN jsonb_typeof(v->'expires')='number' "
                     "THEN (v->>'expires')::numeric < extract(epoch FROM now()) ELSE false END")
    # Failed cleanup is retried on the next cycle, not skipped for the whole day.
    await db.set_kv("pruned_on", today)


async def weekly_report(bot: Bot) -> None:
    """Legacy entry point: statistics are available in the app, never pushed to chat."""


async def poll_loop(bot: Bot) -> None:
    while True:
        try:
            channels = await db.fetch_all("SELECT * FROM channels WHERE paused=0 AND business_mode=0 ORDER BY id")
            state["activity"] = "проверяю источники" if channels else "нет активных каналов"
            async with httpx.AsyncClient() as client:
                async def poll(channel):
                    if not channel['paused']:
                        try:
                            with channel_scope(channel):
                                await poll_channel(client, channel, bot)
                        except Exception:
                            log.exception("channel poll failed: %s", channel['id'])
                await runtime.bounded_map(channels, poll, 6)
        except Exception as exc:
            log.exception("poll loop error")
            state["last_error"] = str(exc)[:150]
            await alert_admins(bot, "poll-loop", "Сбой цикла опроса источников. Проверьте журнал приложения.")
        state["activity"] = "простой — жду новые посты"
        await asyncio.sleep(POLL_INTERVAL)


async def process_round(bot: Bot, after_channel: int = 0) -> int:
    """One candidate per channel, rotating the starting point to prevent starvation."""
    eligible = []
    for channel in await db.fetch_all("SELECT * FROM channels WHERE paused=0"):
        if (not news_policy.realtime(channel) or news_policy.window_open(channel, now_utc())) and await access.owner_has_access(channel['owner_id']):
            eligible.append(channel['id'])
    rows = await db.fetch_all(
        "SELECT * FROM (SELECT DISTINCT ON (channel_id) * FROM posts WHERE status='new' "
        "AND channel_id=ANY(?::bigint[]) AND (is_manual=1 OR NOT EXISTS "
        "(SELECT 1 FROM channels c WHERE c.id=posts.channel_id AND c.business_mode=1)) "
        "AND (publish_at IS NULL OR publish_at<=now()) "
        "ORDER BY channel_id,created_at DESC,id DESC) candidates "
        "ORDER BY (channel_id<=?),channel_id LIMIT 12", (eligible, after_channel))
    state['processing'] = len(rows)

    async def process(post):
        try:
            channel = await db.fetch_one('SELECT * FROM channels WHERE id=?', (post['channel_id'],))
            if channel:
                state['activity'] = 'переписываю пост'
                async with asyncio.timeout(240):
                    await process_post(bot, post, channel)
        except Exception:
            log.exception('processing failed: post_id=%s', post['id'])
            await defer_processing(post, 'Ошибка обработки — повтор через 15 минут')
            await alert_admins(bot, 'process-loop', 'Сбой обработки материалов. Проверьте журнал приложения.')

    await runtime.bounded_map(rows, process, 3)
    return rows[-1]['channel_id'] if rows else after_channel


async def process_loop(bot: Bot) -> None:
    """Refill free slots without waiting for the slowest channel in a batch."""
    cursor = 0
    active = {}

    async def run(post, channel):
        try:
            async with asyncio.timeout(240):
                await process_post(bot, post, channel)
        except Exception:
            log.exception('processing failed: post_id=%s', post['id'])
            await defer_processing(post, 'Ошибка обработки — повтор через 15 минут')

    try:
        while True:
            for cid, task in list(active.items()):
                if task.done():
                    try:
                        task.result()
                    except Exception:
                        log.exception('processing task failed: channel=%s', cid)
                    del active[cid]
            try:
                if len(active) < 3:
                    channels = await db.fetch_all('SELECT * FROM channels WHERE paused=0 ORDER BY (id<=?),id', (cursor,))
                    for channel in channels:
                        cid = channel['id']
                        if cid in active or runtime.channel_lock(cid).locked():
                            continue
                        if not await access.owner_has_access(channel['owner_id']):
                            continue
                        if news_policy.realtime(channel) and not news_policy.window_open(channel, now_utc()):
                            continue
                        post = await db.fetch_one("SELECT * FROM posts WHERE channel_id=? AND status='new' "
                            "AND (?=0 OR is_manual=1) AND (publish_at IS NULL OR publish_at<=now()) "
                            "ORDER BY created_at DESC,id DESC LIMIT 1", (cid, channel.get('business_mode',0)))
                        if post:
                            active[cid] = asyncio.create_task(run(post, channel))
                            cursor = cid
                            if len(active) == 3:
                                break
                state['processing'] = len(active)
            except Exception as exc:
                log.exception('process loop error')
                state['last_error'] = str(exc)[:150]
                await alert_admins(bot, 'process-loop', 'Сбой цикла обработки материалов. Проверьте журнал приложения.')
            await asyncio.sleep(1 if active else 10)
    finally:
        for task in active.values():
            task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)


async def publish_loop(bot: Bot) -> None:
    while True:
        try:
            await publish_due(bot)
        except Exception as exc:
            log.exception("publish loop error")
            state["last_error"] = str(exc)[:150]
            await alert_admins(bot, "publish-loop", "Сбой цикла публикации. Проверьте журнал приложения.")
        await asyncio.sleep(20)


async def digest_loop(bot: Bot) -> None:
    while True:
        try:
            await digest_due(bot)
        except Exception:
            log.exception('digest loop error')
            await alert_admins(bot, 'digest', 'Сбой цикла дайджестов. Проверьте журнал приложения.')
        await asyncio.sleep(300)


async def stats_loop(bot: Bot) -> None:
    while True:
        try:
            await prune_posts()
            await refresh_stats(bot)
        except Exception:
            log.exception("ошибка в цикле статистики")
            await alert_admins(bot, "stats-loop", "Сбой цикла статистики и очистки. Проверьте журнал приложения.")
        await asyncio.sleep(1800)


def start(bot: Bot) -> list[asyncio.Task]:
    from app.core.business import proposal_loop
    return [
        asyncio.create_task(proposal_loop()),
        asyncio.create_task(poll_loop(bot)),
        asyncio.create_task(process_loop(bot)),
        asyncio.create_task(publish_loop(bot)),
        asyncio.create_task(digest_loop(bot)),
        asyncio.create_task(stats_loop(bot)),
    ]


async def publish_fresh_once(bot: Bot, channel: dict) -> dict:
    """One explicit request: refresh sources, validate fresh news, send at most one."""
    lock = runtime.channel_lock(channel['id'])
    if lock.locked() or runtime.fresh_slots.locked():
        raise ValueError('Подготовка постов уже идёт. Повторите через минуту.')
    async with runtime.fresh_slots, lock:
        try:
            async with asyncio.timeout(240):
                return await _publish_fresh_once(bot, channel)
        except TimeoutError as exc:
            raise ValueError('Подготовка заняла слишком много времени. Проверьте историю перед повтором.') from exc


async def _publish_fresh_once(bot: Bot, channel: dict) -> dict:
    fresh_channel = await db.fetch_one('SELECT * FROM channels WHERE id=?', (channel['id'],))
    if fresh_channel and fresh_channel.get('business_mode'):
        raise ValueError('Бизнес-режим: подготовьте черновик и подтвердите его в приложении')
    if not await access.owner_has_access(channel['owner_id']):
        raise ValueError('Доступ владельца закрыт')
    if await publisher.quota_left(channel['owner_id']) <= 0:
        raise publisher.QuotaExceeded('исчерпан дневной лимит')
    pool = await db.connect()
    sources = await db.fetch_all('SELECT * FROM sources WHERE channel_id=? AND enabled=1', (channel['id'],))
    candidates = []
    async with httpx.AsyncClient() as client:
        async def collect(source):
            try:
                async with runtime.source_slots:
                    items, _ = await (rss.fetch(source['ref']) if source['kind'] == 'rss' else telegram_web.fetch(client, source['ref']))
                for item in _chronological(items):
                    date = news_policy.source_date(item.date)
                    if date and now_utc() - news_policy.FRESH_FOR <= date <= now_utc():
                        candidates.append((date, source, item))
            except Exception:
                log.warning('fresh source unavailable: source_id=%s', source['id'])
        await runtime.bounded_map(sources, collect, 4)
    candidates.sort(key=lambda value: value[0], reverse=True)
    attempts = 0
    for date, source, item in candidates:
        uid = source_uid(source, item)
        async with pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval('SELECT id FROM sources WHERE id=$1 FOR UPDATE', source['id']):
                continue
            existing = await conn.fetchrow('SELECT * FROM posts WHERE channel_id=$1 AND (uid=$2 OR (uid=$3 AND source_id=$4))', channel['id'], uid, item.uid, source['id'])
            if existing:
                post = dict(existing)
                if post['status'] not in ('new', 'approved', 'pending') or post.get('reason') and post['status'] != 'new':
                    continue
                # Always regenerate from the current source; never send a cached draft.
                row = await conn.fetchrow(
                    "UPDATE posts SET status='new', raw_text=$1,media=$2,views=$3,created_at=$4, "
                    "text_out=NULL,fact_check=NULL,publish_at=NULL,reason=NULL WHERE id=$5 "
                    "AND status IN ('new','approved','pending') RETURNING *",
                    item.text,json.dumps(item.media),item.views,date,post['id'])
                if not row:
                    continue
                post = dict(row)
            else:
                row = await conn.fetchrow(
                    "INSERT INTO posts(channel_id,source_id,uid,url,source_title,raw_text,media,views,status,created_at) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,'new',$9) RETURNING *",
                    channel['id'],source['id'],uid,item.url,item.source_title or source['title'],item.text,
                    json.dumps(item.media),item.views,date)
                post = dict(row)
        attempts += 1
        await _process_post(bot, post, channel, force_once=True)
        post = await db.fetch_one('SELECT * FROM posts WHERE id=?', (post['id'],))
        if not post or post['status'] not in ('approved', 'pending') or post.get('reason') or not post.get('text_out'):
            if attempts >= 3:
                break
            continue
        # Apply the same one-hour rule even when the normal channel uses a delay.
        await publisher.publish_post(bot, post, dict(channel, delay_mode='instant', digest_enabled=0))
        return {'ok': True, 'post_id': post['id']}
    raise ValueError('Свежих материалов за последний час, прошедших проверку, пока нет. Старые новости не отправлены.')
