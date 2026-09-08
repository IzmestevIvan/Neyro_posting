import html
import json
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import db

log = logging.getLogger("cards")

PREVIEW_LIMIT = 3000


def moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅", callback_data=f"mod:approve:{post_id}"),
        InlineKeyboardButton(text="🔄", callback_data=f"mod:regen:{post_id}"),
        InlineKeyboardButton(text="❌", callback_data=f"mod:reject:{post_id}"),
    )
    return kb.as_markup()


def card_text(post: dict, channel: dict) -> str:
    source = html.escape(post.get("source_title") or "ручной пост")
    header = f"<b>{source}</b>"
    if post.get("url"):
        header += f' · <a href="{html.escape(post["url"], quote=True)}">оригинал</a>'

    body = html.escape((post.get("text_out") or post.get("raw_text") or "")[:PREVIEW_LIMIT])
    parts = [header, "", body]

    media = json.loads(post.get("media") or "[]")
    marks = []
    if media:
        marks.append(f"{len(media)} медиа")
    check = post.get("fact_check")
    if check:
        try:
            verdict = json.loads(check)
            marks.append("фактчек пройден" if verdict.get("ok") else "⚠️ фактчек с замечаниями")
        except json.JSONDecodeError:
            pass
    if marks:
        parts += ["", f"<i>{' · '.join(marks)}</i>"]
    return "\n".join(parts)


async def send_moderation_card(bot: Bot, channel: dict, post_id: int) -> None:
    post = await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post_id,))
    if not post:
        return
    try:
        message = await bot.send_message(
            channel["owner_id"],
            card_text(post, channel),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=moderation_keyboard(post_id),
        )
        await db.execute(
            "UPDATE posts SET mod_message_id = ? WHERE id = ?", (message.message_id, post_id)
        )
    except Exception as exc:
        log.warning("cannot send moderation card to %s: %s", channel["owner_id"], exc)
