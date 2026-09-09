import asyncio
import uuid
import html
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.enums import ParseMode
from aiogram.types import (
    BufferedInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from app import db
from app.core import access, watermark

log = logging.getLogger("publisher")

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


def signature_html(channel: dict) -> str:
    text = (channel.get("signature_text") or "").strip()
    if not text:
        return ""
    url = (channel.get("signature_url") or "").strip()
    if not url and channel.get("username"):
        url = f"https://t.me/{channel['username'].lstrip('@')}"
    safe = html.escape(text)
    return f'<a href="{html.escape(url, quote=True)}">{safe}</a>' if url else safe


def _fit_escaped(text: str, room: int) -> str:
    """Escape `text` so the result never exceeds `room`.

    Slicing already-escaped text would cut entities in half ("&lt;b" instead of
    "&lt;b&gt;") and Telegram rejects the whole message, so the raw text is trimmed
    first and escaped afterwards.
    """
    escaped = html.escape(text)
    if len(escaped) <= room:
        return escaped

    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(html.escape(text[:mid])) <= room - 1:
            low = mid
        else:
            high = mid - 1
    trimmed = text[:low].rstrip()
    if " " in trimmed[-40:]:
        trimmed = trimmed[: trimmed.rfind(" ")].rstrip(" ,.;:—-")
    return html.escape(trimmed) + "…"


def render(channel: dict, text: str, limit: int = TEXT_LIMIT) -> str:
    sign = signature_html(channel)
    room = limit - (len(sign) + 2 if sign else 0)
    body = _fit_escaped(text.strip(), max(room, 1))
    return f"{body}\n\n{sign}" if sign else body


def fits_caption(channel: dict, text: str) -> bool:
    sign = signature_html(channel)
    overhead = len(sign) + 2 if sign else 0
    return len(html.escape(text.strip())) + overhead <= CAPTION_LIMIT


async def _prepare_media(media: list[dict], channel: dict) -> list[dict]:
    if not channel.get("watermark") or not channel.get("logo_path"):
        return media
    prepared = []
    for index, item in enumerate(media):
        url = item.get("url") or ""
        # Photos attached in the bot chat are stored as file_id and cannot be downloaded
        # for stamping without round-tripping through the Bot API; they go through as-is.
        if item.get("type") == "photo" and url.startswith("http"):
            stamped = await watermark.apply(url, channel.get("logo_path"))
            if stamped:
                prepared.append({"type": "photo", "file": BufferedInputFile(stamped, f"p{index}.jpg")})
                continue
        prepared.append(item)
    return prepared


def _as_input(item: dict):
    return item.get("file") or item.get("file_id") or item.get("url")


async def publish(bot: Bot, channel: dict, text: str, media: list[dict]) -> Message:
    chat_id = channel["chat_id"] or f"@{(channel.get('username') or '').lstrip('@')}"
    media = await _prepare_media(media[:10], channel)

    if not media:
        return await bot.send_message(
            chat_id, render(channel, text), parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )

    fits = fits_caption(channel, text)
    body = render(channel, text, CAPTION_LIMIT) if fits else None

    if len(media) == 1:
        item = media[0]
        sender = bot.send_video if item.get("type") == "video" else bot.send_photo
        message = await sender(chat_id, _as_input(item), caption=body, parse_mode=ParseMode.HTML)
    else:
        group = []
        for index, item in enumerate(media):
            cls = InputMediaVideo if item.get("type") == "video" else InputMediaPhoto
            group.append(
                cls(
                    media=_as_input(item),
                    caption=body if index == 0 else None,
                    parse_mode=ParseMode.HTML if index == 0 else None,
                )
            )
        message = (await bot.send_media_group(chat_id, group))[0]

    if not fits:
        await bot.send_message(
            chat_id, render(channel, text), parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    return message


class AlreadyPublished(RuntimeError):
    pass


CLAIMABLE = ("pending", "approved", "digest", "failed")
MAX_ATTEMPTS = 3


WORKER_ID = str(uuid.uuid4())


class DeliveryUncertain(RuntimeError):
    pass


class QuotaExceeded(RuntimeError):
    pass


async def _used_on_connection(conn, owner_id: int) -> int:
    # Reservations and uncertain attempts consume a slot. Legacy published rows
    # without a ledger entry remain counted during the transition.
    return await conn.fetchval(
        "SELECT (SELECT COUNT(*) FROM delivery_attempts WHERE owner_id=$1 AND status!='failed' "
        "AND (started_at AT TIME ZONE 'UTC')::date=(now() AT TIME ZONE 'UTC')::date) + "
        "(SELECT COUNT(*) FROM posts p JOIN channels c ON c.id=p.channel_id "
        "WHERE c.owner_id=$1 AND p.status='published' "
        "AND (p.published_at AT TIME ZONE 'UTC')::date=(now() AT TIME ZONE 'UTC')::date "
        "AND NOT EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.post_id=p.id AND d.status!='failed'))",
        owner_id)


class RecordedBot:
    """Persist every acknowledged Telegram step before starting the next step."""
    def __init__(self, bot, attempt_id):
        self.bot, self.attempt_id = bot, attempt_id
        self.receipts = []
        self.in_flight = False

    def __getattr__(self, name):
        if name not in ('send_message', 'send_photo', 'send_video', 'send_media_group'):
            return getattr(self.bot, name)

        async def send(*args, **kwargs):
            self.in_flight = True
            result = await getattr(self.bot, name)(*args, **kwargs)
            messages = result if isinstance(result, list) else [result]
            self.receipts.append({'method': name, 'message_ids': [m.message_id for m in messages]})
            await db.execute("UPDATE delivery_attempts SET receipts=?::jsonb WHERE id=?",
                             (json.dumps(self.receipts), self.attempt_id))
            self.in_flight = False
            return result
        return send


async def publish_post(bot: Bot, post: dict, channel: dict) -> int:
    if not await access.owner_has_access(channel['owner_id']):
        raise PermissionError('доступ владельца закрыт')
    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            owner = await conn.fetchrow('SELECT * FROM users WHERE tg_id=$1 FOR UPDATE', channel['owner_id'])
            from app.api.auth import has_access
            if not owner or not has_access(dict(owner)):
                raise PermissionError('доступ владельца закрыт')
            fresh = await conn.fetchrow('SELECT * FROM posts WHERE id=$1 FOR UPDATE', post['id'])
            if not fresh or fresh['channel_id'] != channel['id'] or fresh['status'] not in CLAIMABLE:
                raise AlreadyPublished('пост недоступен для публикации или уже отправляется')
            if not (fresh['text_out'] or '').strip():
                raise AlreadyPublished('нет готового текста — повторите обработку')
            if await _used_on_connection(conn, channel['owner_id']) >= owner['daily_limit']:
                raise QuotaExceeded('исчерпан дневной лимит, включая отправляемые посты')
            post = dict(fresh)
            attempt_id = await conn.fetchval(
                'INSERT INTO delivery_attempts (post_id, channel_id, owner_id, worker_id) VALUES ($1,$2,$3,$4) RETURNING id',
                post['id'], channel['id'], channel['owner_id'], WORKER_ID)
            await conn.execute("UPDATE posts SET status='publishing' WHERE id=$1", post['id'])

    recorded = RecordedBot(bot, attempt_id)
    try:
        async with asyncio.timeout(180):
            media = json.loads(post.get('media') or '[]')
            message = await publish(recorded, channel, post['text_out'], media)
    except Exception as exc:
        definite = isinstance(exc, (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter)) or not recorded.in_flight
        status = 'partial' if recorded.receipts else ('failed' if definite else 'uncertain')
        reason = ('Telegram подтвердил только часть отправки. Проверьте канал; автоматический повтор отключён.'
                  if status == 'partial' else 'Результат отправки неизвестен. Проверьте канал; автоматический повтор отключён.'
                  if status == 'uncertain' else f'Отправка отклонена: {type(exc).__name__}')
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute('UPDATE delivery_attempts SET status=$1, receipts=$2::jsonb, error_type=$3, finished_at=now() WHERE id=$4',
                                   status, json.dumps(recorded.receipts), type(exc).__name__, attempt_id)
                await conn.execute(
                    "UPDATE posts SET attempts=attempts+1, reason=$1, status=$2 WHERE id=$3 AND status='publishing'",
                    reason, ('failed' if post['attempts'] + 1 >= MAX_ATTEMPTS else post['status']) if status == 'failed' else status,
                    post['id'])
        if status != 'failed':
            raise DeliveryUncertain(reason) from exc
        raise

    # Telegram success and DB bookkeeping are distinct. A DB failure here leaves
    # publishing + receipts for recovery; it NEVER makes the post retryable.
    async with pool.acquire() as conn:
        async with conn.transaction():
            attempt_status = await conn.fetchval('SELECT status FROM delivery_attempts WHERE id=$1 FOR UPDATE', attempt_id)
            if attempt_status != 'sending':
                raise DeliveryUncertain('Попытка уже передана на сверку; автоматическое завершение остановлено')
            await conn.execute("UPDATE posts SET status='published', message_id=$1, published_at=now(), reason=NULL WHERE id=$2", message.message_id, post['id'])
            await conn.execute("UPDATE delivery_attempts SET status='sent', finished_at=now() WHERE id=$1", attempt_id)
            await conn.execute("INSERT INTO stats_daily (channel_id, day, published) VALUES ($1,(now() AT TIME ZONE 'UTC')::date,1) "
                               "ON CONFLICT (channel_id,day) DO UPDATE SET published=stats_daily.published+1", channel['id'])
    try:
        await db.set_kv('last_publish_at', datetime.now(timezone.utc).isoformat(timespec='seconds'))
    except Exception:
        log.warning('cannot update last publication display timestamp')
    return message.message_id


async def recover_stale_deliveries() -> int:
    """Ten-minute quarantine exceeds the three-minute send deadline. No replay."""
    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch("UPDATE delivery_attempts SET status='uncertain', finished_at=now(), error_type='WorkerInterrupted' "
                                    "WHERE status='sending' AND started_at < now()-interval '10 minutes' RETURNING post_id")
            for row in rows:
                await conn.execute("UPDATE posts SET status='uncertain', reason='Отправка прервана. Проверьте канал; автоматический повтор отключён.' "
                                   "WHERE id=$1 AND status='publishing'", row['post_id'])
            legacy = await conn.fetch(
                "SELECT p.id, p.channel_id, c.owner_id FROM posts p JOIN channels c ON c.id=p.channel_id "
                "WHERE p.status='publishing' AND p.created_at < now()-interval '10 minutes' "
                "AND NOT EXISTS (SELECT 1 FROM delivery_attempts d WHERE d.post_id=p.id) FOR UPDATE OF p SKIP LOCKED")
            for post in legacy:
                await conn.execute("INSERT INTO delivery_attempts (post_id,channel_id,owner_id,worker_id,status,error_type) "
                                   "VALUES ($1,$2,$3,$4,'uncertain','LegacyAttempt')", post['id'],post['channel_id'],post['owner_id'],WORKER_ID)
                await conn.execute("UPDATE posts SET status='uncertain',reason='Старая отправка без журнала. Проверьте канал вручную.' WHERE id=$1",post['id'])
            return len(rows) + len(legacy)


async def used_today(owner_id: int) -> int:
    pool = await db.connect()
    async with pool.acquire() as conn:
        return await _used_on_connection(conn, owner_id)


async def limit_for(owner_id: int) -> int:
    row = await db.fetch_one("SELECT daily_limit FROM users WHERE tg_id = ?", (owner_id,))
    return row["daily_limit"] if row else 0


async def quota_left(owner_id: int) -> int:
    return max(0, await limit_for(owner_id) - await used_today(owner_id))


async def resolve_channel(bot: Bot, ref: str) -> tuple[int, str, Optional[str]]:
    chat = await bot.get_chat(ref)
    return chat.id, chat.title or ref, chat.username


async def verify_channel_permissions(bot: Bot, chat_id: int, user_id: int) -> None:
    """A public channel being visible does not authorize its management."""
    member = await bot.get_chat_member(chat_id, user_id)
    if member.status not in ("creator", "administrator"):
        raise PermissionError("подключать канал может только его владелец или администратор")
    me = await bot.get_me()
    bot_member = await bot.get_chat_member(chat_id, me.id)
    if bot_member.status != "administrator" or not bot_member.can_post_messages:
        raise PermissionError("боту нужны права администратора с разрешением публикации")


async def reject_post(post_id: int) -> bool:
    return bool(await db.update(
        "UPDATE posts SET status='rejected' WHERE id=? AND status IN ('new','pending','approved','digest','failed')",
        (post_id,),
    ))


async def replace_draft(post: dict, text: str, fact_check, *, needs_review: bool = False) -> bool:
    """Optimistic version check: never modify a sent post or overwrite a newer edit."""
    return bool(await db.update(
        "UPDATE posts SET text_out=?, fact_check=?, "
        "status=CASE WHEN ? THEN 'pending' ELSE status END, "
        "reason=CASE WHEN ? THEN 'Возможная реклама: требуется ручная проверка' ELSE reason END WHERE id=? "
        "AND status IN ('pending','approved','digest','failed') "
        "AND text_out IS NOT DISTINCT FROM ?",
        (text, json.dumps(fact_check, ensure_ascii=False) if fact_check else None, needs_review, needs_review, post['id'], post['text_out']),
    ))


async def reconcile_delivery(post_id: int, channel: dict, *, delivered: bool) -> None:
    """Owner explicitly checks the channel. This action never sends a message."""
    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.fetchrow('SELECT tg_id FROM users WHERE tg_id=$1 FOR UPDATE', channel['owner_id'])
            post = await conn.fetchrow('SELECT * FROM posts WHERE id=$1 FOR UPDATE', post_id)
            if not post or post['channel_id'] != channel['id'] or post['status'] not in ('partial', 'uncertain'):
                raise AlreadyPublished('результат уже сверён или отправка ещё выполняется')
            attempt = await conn.fetchrow('SELECT * FROM delivery_attempts WHERE post_id=$1 ORDER BY id DESC LIMIT 1 FOR UPDATE', post_id)
            receipts = json.loads(attempt['receipts']) if attempt and isinstance(attempt['receipts'], str) else (attempt['receipts'] if attempt else [])
            if not delivered and attempt and attempt['started_at'] > datetime.now(timezone.utc) - timedelta(minutes=10):
                raise AlreadyPublished('Сверка отсутствия доступна через 10 минут после начала попытки: ответ Telegram мог задержаться')
            if not delivered and receipts:
                raise AlreadyPublished('есть подтверждённые сообщения: нельзя повторно отправлять весь пост')
            if delivered:
                sent_at = attempt['started_at'] if attempt else post['created_at']
                message_id = receipts[0]['message_ids'][0] if receipts else None
                await conn.execute("UPDATE posts SET status='published', published_at=$1, message_id=$2, reason='Публикация подтверждена владельцем вручную' WHERE id=$3", sent_at, message_id, post_id)
                await conn.execute("INSERT INTO stats_daily (channel_id,day,published) VALUES ($1,$2,1) ON CONFLICT (channel_id,day) DO UPDATE SET published=stats_daily.published+1", channel['id'], sent_at.astimezone(timezone.utc).date())
            else:
                await conn.execute("UPDATE posts SET status='pending', reason='Владелец подтвердил отсутствие публикации. Повтор требует отдельного одобрения.' WHERE id=$1", post_id)
            if attempt:
                await conn.execute('UPDATE delivery_attempts SET status=$1, error_type=$2, finished_at=now() WHERE id=$3',
                                   'sent' if delivered else 'failed', 'OwnerConfirmedSent' if delivered else 'OwnerConfirmedAbsent', attempt['id'])
