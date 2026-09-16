"""Admin-initiated announcements; durable audience and no blind retries."""
import asyncio
import logging
import secrets

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app import db
from app.config import ADMIN_IDS
from app.core import runtime

router = Router()
log = logging.getLogger('broadcasts')
HELP = ('Рассылка клиентам\n\nОтправьте /broadcast и текст сообщения в той же строке. '
        'До 3500 символов, без HTML-разметки. Сначала покажу предпросмотр и число получателей. '
        'Отправка начнётся только после подтверждения.\n\n'
        'Получатели — зарегистрированные пользователи, не заблокированные в сервисе, включая пользователей с истёкшим доступом. '
        'Каналы не затрагиваются. Не используйте рассылку для регулярных новостей.\n'
        '/broadcast_status — состояние последних рассылок.')


async def is_admin(user_id):
    row = await db.fetch_one('SELECT is_admin,blocked FROM users WHERE tg_id=?', (user_id,))
    return bool((row and not row['blocked'] and row['is_admin']) or user_id in ADMIN_IDS)


@router.callback_query(F.data == 'broadcast:help')
async def help_callback(callback):
    if callback.message.chat.type != 'private' or not await is_admin(callback.from_user.id):
        return await callback.answer('Только для администратора', show_alert=True)
    await callback.answer()
    await callback.message.answer(HELP, parse_mode=None)


@router.message(Command('broadcast'))
async def prepare(message):
    if message.chat.type != 'private' or not await is_admin(message.from_user.id):
        return await message.answer('Только для администратора')
    parts = (message.text or '').split(maxsplit=1)
    if len(parts) != 2:
        return await message.answer(HELP, parse_mode=None)
    body = parts[1].strip()
    if not 1 <= len(body.encode('utf-16-le')) // 2 <= 3500:
        return await message.answer('Текст рассылки: от 1 до 3500 символов')
    from app.core.rate_limit import admit
    if not admit((message.from_user.id, 'broadcast'), 3):
        return await message.answer('Подождите минуту перед созданием новой рассылки')
    token = secrets.token_hex(8)
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute('INSERT INTO broadcasts(id,admin_id,body) VALUES($1,$2,$3)', token, message.from_user.id, body)
        await conn.execute('INSERT INTO broadcast_recipients(broadcast_id,user_id) SELECT $1,tg_id FROM users WHERE blocked=0', token)
        count = await conn.fetchval('SELECT count(*) FROM broadcast_recipients WHERE broadcast_id=$1', token)
    await message.answer('Предпросмотр сообщения:', parse_mode=None)
    await message.answer(body, parse_mode=None)
    await message.answer(f'Получателей: {count}. Отправить именно этот текст?\nПодтверждение действует 30 минут.',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f'Отправить · {count}', callback_data=f'broadcast:send:{token}')],
            [InlineKeyboardButton(text='Отмена', callback_data=f'broadcast:cancel:{token}')]]))


@router.callback_query(F.data.startswith('broadcast:send:') | F.data.startswith('broadcast:cancel:'))
async def confirm(callback):
    if callback.message.chat.type != 'private' or not await is_admin(callback.from_user.id):
        return await callback.answer('Только для администратора', show_alert=True)
    _, action, token = callback.data.split(':', 2)
    pool = await db.connect()
    busy = False
    changed = None
    async with pool.acquire() as conn, conn.transaction():
        # Serialize confirmation globally, not just for a single button.
        await conn.execute('SELECT pg_advisory_xact_lock(734619280146::bigint)')
        if action == 'send' and await conn.fetchval("SELECT 1 FROM broadcasts WHERE status='running' LIMIT 1"):
            busy = True
        else:
            changed = await conn.fetchval("UPDATE broadcasts SET status=$1 WHERE id=$2 AND admin_id=$3 AND status='draft' AND created_at>now()-interval '30 minutes' RETURNING id",
                'running' if action == 'send' else 'cancelled', token, callback.from_user.id)
    if busy:
        return await callback.answer('Дождитесь завершения текущей рассылки', show_alert=True)
    if not changed:
        return await callback.answer('Уже обработано или время подтверждения истекло', show_alert=True)
    await callback.answer('Рассылка запущена' if action == 'send' else 'Отменено')
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('Статус доступен по /broadcast_status. Повторное подтверждение не отправит копию.' if action == 'send' else 'Рассылка отменена.')


@router.message(Command('broadcast_status'))
async def status(message):
    if message.chat.type != 'private' or not await is_admin(message.from_user.id):
        return await message.answer('Только для администратора')
    rows = await db.fetch_all("SELECT b.id,b.status,b.created_at, count(r.*) AS total, "
        "count(*) FILTER(WHERE r.status='sent') AS sent, count(*) FILTER(WHERE r.status='failed') AS failed, "
        "count(*) FILTER(WHERE r.status='uncertain') AS uncertain FROM broadcasts b "
        "LEFT JOIN broadcast_recipients r ON r.broadcast_id=b.id GROUP BY b.id ORDER BY b.created_at DESC LIMIT 5")
    labels={'draft':'ждёт подтверждения','running':'отправляется','done':'завершена','cancelled':'отменена'}
    text = '\n\n'.join(f"{r['created_at']:%d.%m %H:%M} UTC · {labels.get(r['status'],r['status'])}\n"
        f"Всего: {r['total']} · отправлено: {r['sent']} · недоступны: {r['failed']} · требуют сверки: {r['uncertain']}" for r in rows)
    await message.answer(text or 'Рассылок пока нет.', parse_mode=None)


async def deliver_one(bot):
    pool = await db.connect()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("SELECT r.broadcast_id,r.user_id,b.body FROM broadcast_recipients r JOIN broadcasts b ON b.id=r.broadcast_id "
            "WHERE b.status='running' AND r.status='pending' ORDER BY b.created_at,r.user_id LIMIT 1 FOR UPDATE OF r SKIP LOCKED")
        if not row:
            await conn.execute("UPDATE broadcasts b SET status='done' WHERE status='running' AND NOT EXISTS "
                "(SELECT 1 FROM broadcast_recipients r WHERE r.broadcast_id=b.id AND r.status IN ('pending','sending'))")
            return False
        await conn.execute("UPDATE broadcast_recipients SET status='sending' WHERE broadcast_id=$1 AND user_id=$2", row['broadcast_id'], row['user_id'])
    state, error, message_id = 'sent', None, None
    try:
        user = await db.fetch_one('SELECT blocked FROM users WHERE tg_id=?', (row['user_id'],))
        if not user or user['blocked']:
            state, error = 'failed', 'Доступ пользователя закрыт'
        else:
            await runtime.wait_telegram(row['user_id'])
            async with asyncio.timeout(30):
                sent = await bot.send_message(row['user_id'], row['body'], parse_mode=None)
            message_id = sent.message_id
    except TelegramRetryAfter as exc:
        # Telegram explicitly rejected this call; retry only after the cooldown.
        await asyncio.sleep(max(exc.retry_after, 1))
        state, error = 'pending', 'TelegramRetryAfter'
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        state, error = 'failed', type(exc).__name__
    except Exception as exc:
        state, error = 'uncertain', type(exc).__name__
        log.warning('Неизвестный результат рассылки %s для %s: %s', row['broadcast_id'], row['user_id'], error)
    await db.execute('UPDATE broadcast_recipients SET status=?,error=?,message_id=? WHERE broadcast_id=? AND user_id=?',
        (state, error, message_id, row['broadcast_id'], row['user_id']))
    return True


async def recover_interrupted():
    # Only the singleton broadcast worker calls this, with no active HTTP send.
    await db.execute("UPDATE broadcast_recipients SET status='uncertain',error='WorkerInterrupted' WHERE status='sending'")


async def run(bot):
    while True:
        try:
            # Also recover a DB bookkeeping failure without waiting for restart.
            await recover_interrupted()
            await db.execute("DELETE FROM broadcasts WHERE status!='running' AND created_at<now()-interval '30 days'")
            worked = await deliver_one(bot)
        except Exception:
            log.exception('Ошибка обработки рассылки')
            worked = False
        await asyncio.sleep(1 if worked else 5)
