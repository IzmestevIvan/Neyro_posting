import html
import json
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app import db
from app.core import news_policy

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
    target = html.escape(channel.get("title") or channel.get("username") or str(channel["id"]))
    header = f"<b>Канал: {target}</b> · на одобрение\n<b>{source}</b>"
    if post.get("url"):
        header += f' · <a href="{html.escape(post["url"], quote=True)}">оригинал</a>'

    body = html.escape((post.get("text_out") or post.get("raw_text") or "")[:PREVIEW_LIMIT])
    parts = [header, "", body]
    if post.get("reason"):
        parts += ["", html.escape(post["reason"][:300])]

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
    """Legacy entry point: background review notifications are disabled.

    Keep this a no-op so any old caller cannot start a private news feed again.
    Drafts are already persisted and displayed in the Mini App.
    """
