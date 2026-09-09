import html
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
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


async def publish_post(bot: Bot, post: dict, channel: dict) -> int:
    if not await access.owner_has_access(channel["owner_id"]):
        raise PermissionError("доступ владельца закрыт")
    pool = await db.connect()
    async with pool.acquire() as conn:
        async with conn.transaction():
            fresh = await conn.fetchrow("SELECT * FROM posts WHERE id=$1 FOR UPDATE", post["id"])
            if not fresh or fresh["channel_id"] != channel["id"] or fresh["status"] not in CLAIMABLE:
                raise AlreadyPublished("пост недоступен для публикации или уже отправляется")
            if not (fresh["text_out"] or "").strip():
                raise AlreadyPublished("нет готового текста — повторите обработку")
            post = dict(fresh)
            await conn.execute("UPDATE posts SET status='publishing' WHERE id=$1", post["id"])

    try:
        media = json.loads(post.get("media") or "[]")
        message = await publish(bot, channel, post["text_out"], media)
    except Exception as exc:
        await db.execute(
            "UPDATE posts SET attempts = attempts + 1, reason = ?, "
            "status = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE ? END WHERE id = ?",
            (str(exc)[:200], MAX_ATTEMPTS, post["status"], post["id"]),
        )
        raise

    now = datetime.now(timezone.utc)
    await db.execute(
        "UPDATE posts SET status = 'published', message_id = ?, published_at = ? WHERE id = ?",
        (message.message_id, now, post["id"]),
    )
    await db.bump_stat(channel["id"], now.date(), "published")
    await db.set_kv("last_publish_at", now.isoformat(timespec="seconds"))
    log.info("published post %s to channel %s", post["id"], channel["id"])
    return message.message_id


async def used_today(owner_id: int) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM posts p JOIN channels c ON c.id = p.channel_id "
        "WHERE c.owner_id = ? AND p.status = 'published' AND p.published_at::date = CURRENT_DATE",
        (owner_id,),
    )
    return row["n"] if row else 0


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
