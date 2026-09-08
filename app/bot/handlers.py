import html
import json
import logging
import re
from typing import Optional

import httpx
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app import db
from app.ai import gemini, pipeline
from app.bot.cards import card_text, moderation_keyboard
from app.api.auth import has_access
from app.config import ADMIN_IDS, LOGO_DIR, PUBLIC_URL
from app.core import publisher
from app.sources import telegram_web, web

log = logging.getLogger("bot")
esc_html = html.escape
router = Router()

URL_RE = re.compile(r"(?:https?://|(?:www\.)?t\.me/)\S+", re.I)
LOGO_WORDS = ("логотип", "logo", "вотермарк", "водяной знак")


async def ensure_user(message: Message) -> dict:
    user = message.from_user
    existing = await db.fetch_one("SELECT * FROM users WHERE tg_id = ?", (user.id,))
    if existing:
        return existing
    await db.execute(
        "INSERT INTO users (tg_id, username, first_name, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
        (user.id, user.username, user.first_name, int(user.id in ADMIN_IDS), db.utcnow()),
    )
    return await db.fetch_one("SELECT * FROM users WHERE tg_id = ?", (user.id,))


async def active_channel(user_id: int) -> Optional[dict]:
    channel_id = await db.get_kv(f"active:{user_id}")
    if channel_id:
        channel = await db.fetch_one(
            "SELECT * FROM channels WHERE id = ? AND owner_id = ?", (channel_id, user_id)
        )
        if channel:
            return channel
    return await db.fetch_one(
        "SELECT * FROM channels WHERE owner_id = ? ORDER BY id LIMIT 1", (user_id,)
    )


def miniapp_markup() -> Optional[InlineKeyboardMarkup]:
    if not PUBLIC_URL.startswith("https://"):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть панель", web_app=WebAppInfo(url=PUBLIC_URL))]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = await ensure_user(message)
    markup = miniapp_markup()

    if not has_access(user):
        text = (
            "<b>Нейропостинг</b>\n\n"
            "Наполняю Telegram-канал за вас: беру посты из выбранных источников, переписываю "
            "нейросетью до неузнаваемости, отсеиваю рекламу и повторы и публикую по расписанию.\n\n"
            "Панель работает по промокоду. Откройте её кнопкой ниже и введите свой код — "
            "он задаёт лимит постов в день, число каналов и срок доступа.\n\n"
            f"Кода нет? Напишите администратору. Ваш ID: <code>{message.from_user.id}</code>"
        )
        return await message.answer(text, parse_mode="HTML", reply_markup=markup)

    channel = await active_channel(user["tg_id"])
    if not channel:
        text = (
            "<b>Доступ активен.</b> Осталось подключить канал.\n\n"
            "<b>1.</b> Добавьте меня администратором вашего канала и разрешите публиковать сообщения.\n"
            "<b>2.</b> Откройте панель и введите @username канала.\n"
            "<b>3.</b> На вкладке «Источники» добавьте каналы-доноры или RSS-ленты.\n\n"
            "Дальше я всё делаю сам."
        )
        return await message.answer(text, parse_mode="HTML", reply_markup=markup)

    stats = await db.fetch_one(
        "SELECT COUNT(*) FILTER (WHERE status = 'pending') AS pending, "
        "COUNT(*) FILTER (WHERE status = 'approved') AS queued FROM posts WHERE channel_id = ?",
        (channel["id"],),
    )
    sources = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM sources WHERE channel_id = ?", (channel["id"],)
    )
    mode = "пауза" if channel["paused"] else ("автопостинг" if channel["autopost"] else "модерация")

    text = (
        f"<b>{esc_html(channel['title'] or channel['username'])}</b> · {mode}\n"
        f"Источников: {sources['n']} · на одобрение: {stats['pending']} · в очереди: {stats['queued']}\n\n"
        "<b>Что можно прямо в этом чате</b>\n"
        "• Прислать ссылку на пост или свой текст — сделаю из него публикацию\n"
        "• Прислать фото или видео с подписью — опубликую вместе с медиа\n"
        "• Ответить на карточку поста правкой («короче», «без эмодзи», «убери цифры») — перепишу\n"
        "• Прислать картинку с подписью «логотип» — поставлю водяной знак на фото\n\n"
        "Всё остальное — источники, стиль, расписание, рекламодатели — в панели."
    )
    if markup is None:
        text += "\n\n<i>PUBLIC_URL не задан — панель откроется после настройки HTTPS-адреса.</i>"
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"Ваш ID: <code>{message.from_user.id}</code>", parse_mode="HTML")


@router.message((F.photo | F.video), F.caption)
async def on_logo(message: Message, bot: Bot) -> None:
    if not any(word in (message.caption or "").lower() for word in LOGO_WORDS):
        return await on_manual(message, bot)
    if not message.photo:
        return await message.answer("Для водяного знака пришлите картинку, а не видео.")
    channel = await active_channel(message.from_user.id)
    if not channel:
        return await message.answer("Сначала добавьте канал в панели.")
    path = LOGO_DIR / f"{channel['id']}.png"
    await bot.download(message.photo[-1], destination=path)
    await db.execute(
        "UPDATE channels SET logo_path = ?, watermark = 1 WHERE id = ?", (str(path), channel["id"])
    )
    await message.answer("Логотип сохранён, водяной знак включён.")


@router.message(F.photo | F.video)
async def on_media_without_caption(message: Message) -> None:
    await ensure_user(message)
    await message.answer(
        "К медиа нужна подпись — из неё я сделаю пост.\n\n"
        "• подпись «логотип» на картинке — сохраню её как водяной знак\n"
        "• любой другой текст — опубликую его вместе с этим медиа"
    )


@router.message(F.document | F.voice | F.audio | F.sticker | F.animation)
async def on_unsupported(message: Message) -> None:
    await ensure_user(message)
    await message.answer(
        "Пока принимаю текст, ссылки, фото и видео. "
        "Пришлите ссылку на пост или свой текст — сделаю из него публикацию."
    )


async def _build_manual_post(text: str) -> tuple[str, list[dict], Optional[str], Optional[str]]:
    match = URL_RE.search(text)
    if not match:
        return text, [], None, None

    url = match.group(0).rstrip(".,;)")
    async with httpx.AsyncClient() as client:
        link = telegram_web.parse_post_link(url)
        if not url.lower().startswith("http"):
            url = f"https://{url}"
        if link:
            item = await telegram_web.fetch_single(client, *link)
            if not item:
                raise ValueError("не удалось прочитать пост по ссылке")
        else:
            item = await web.fetch_article(client, url)
    return item.text, item.media, item.url, item.source_title


@router.message(F.text | F.caption)
async def on_manual(message: Message, bot: Bot) -> None:
    await ensure_user(message)
    if message.reply_to_message:
        return await on_edit_reply(message, bot)

    raw = (message.text or message.caption or "").strip()
    if not raw or raw.startswith("/"):
        return

    channel = await active_channel(message.from_user.id)
    if not channel:
        return await message.answer("Сначала добавьте канал в панели.")

    notice = await message.answer("Обрабатываю…")
    try:
        text, media, url, source_title = await _build_manual_post(raw)
    except Exception as exc:
        return await notice.edit_text(f"Не получилось: {exc}")

    # file_id, not the download URL — that URL embeds the bot token and would be
    # persisted in the database and shipped to the panel.
    if message.photo:
        media = [{"type": "photo", "file_id": message.photo[-1].file_id}]
    elif message.video:
        media = [{"type": "video", "file_id": message.video.file_id}]

    try:
        result = await pipeline.process(
            text,
            quality=channel["quality"],
            instructions=channel["instructions"],
            lang=channel["lang"],
            api_key=channel["gemini_key"] or None,
            voice_sample=channel["voice_sample"] or "" if channel["channel_voice"] else "",
        )
    except gemini.AIError as exc:
        return await notice.edit_text(f"Нейросеть недоступна: {exc}")

    if not result.ok:
        return await notice.edit_text(f"Пост отклонён: {result.reason}")

    post_id = await db.insert(
        "INSERT INTO posts (channel_id, uid, url, source_title, raw_text, media, status, text_out, "
        "fact_check, is_manual, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, 1, ?)",
        (
            channel["id"],
            f"manual:{message.message_id}",
            url,
            source_title or "ручной пост",
            text,
            json.dumps(media, ensure_ascii=False),
            result.text,
            json.dumps(result.fact_check, ensure_ascii=False) if result.fact_check else None,
            db.utcnow(),
        ),
    )
    if result.ai_requests:
        await db.bump_stat(channel["id"], db.utcnow().date(), "ai_requests", result.ai_requests)

    post = await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post_id,))
    await notice.edit_text(
        card_text(post, channel),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=moderation_keyboard(post_id),
    )
    await db.execute(
        "UPDATE posts SET mod_message_id = ? WHERE id = ?", (notice.message_id, post_id)
    )


async def on_edit_reply(message: Message, bot: Bot) -> None:
    target = message.reply_to_message.message_id
    post = await db.fetch_one(
        "SELECT p.* FROM posts p JOIN channels c ON c.id = p.channel_id "
        "WHERE p.mod_message_id = ? AND c.owner_id = ?",
        (target, message.from_user.id),
    )
    if not post:
        return
    channel = await db.fetch_one("SELECT * FROM channels WHERE id = ?", (post["channel_id"],))
    instruction = (message.text or "").strip()
    if not instruction:
        return

    try:
        updated = await pipeline.rewrite_with_instruction(
            post["text_out"] or post["raw_text"], instruction, channel["lang"], channel["gemini_key"] or None
        )
    except gemini.AIError as exc:
        return await message.reply(f"Нейросеть недоступна: {exc}")

    await db.execute("UPDATE posts SET text_out = ? WHERE id = ?", (updated, post["id"]))
    post = await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post["id"],))
    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=target,
        text=card_text(post, channel),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=moderation_keyboard(post["id"]),
    )
    await message.reply("Переписал.")


@router.callback_query(F.data.startswith("mod:"))
async def on_moderation(callback: CallbackQuery, bot: Bot) -> None:
    _, action, raw_id = callback.data.split(":")
    post_id = int(raw_id)

    post = await db.fetch_one(
        "SELECT p.*, c.owner_id FROM posts p JOIN channels c ON c.id = p.channel_id WHERE p.id = ?",
        (post_id,),
    )
    if not post or post["owner_id"] != callback.from_user.id:
        return await callback.answer("Пост недоступен", show_alert=True)
    channel = await db.fetch_one("SELECT * FROM channels WHERE id = ?", (post["channel_id"],))

    if action == "reject":
        await db.execute("UPDATE posts SET status = 'rejected' WHERE id = ?", (post_id,))
        await callback.message.edit_text("❌ Отклонено")
        return await callback.answer()

    if action == "regen":
        await callback.answer("Переписываю…")
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
            return await callback.message.answer(f"Нейросеть недоступна: {exc}")
        if not result.ok:
            return await callback.message.answer(f"Не прошло проверку: {result.reason}")
        await db.execute("UPDATE posts SET text_out = ? WHERE id = ?", (result.text, post_id))
        post = await db.fetch_one("SELECT * FROM posts WHERE id = ?", (post_id,))
        return await callback.message.edit_text(
            card_text(post, channel),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=moderation_keyboard(post_id),
        )

    if await publisher.quota_left(post["owner_id"]) <= 0:
        return await callback.answer("Исчерпан дневной лимит", show_alert=True)

    await callback.answer("Публикую…")
    try:
        await publisher.publish_post(bot, post, channel)
    except publisher.AlreadyPublished:
        return await callback.message.edit_text("✅ Опубликовано")
    except Exception as exc:
        log.warning("не удалось опубликовать пост %s", post_id, exc_info=True)
        return await callback.message.answer(f"Не удалось опубликовать: {exc}")
    await callback.message.edit_text("✅ Опубликовано")
