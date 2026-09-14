"""Private support inbox: messages go to admins; only mapped replies go to customers."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message

from app import db
from app.config import ADMIN_IDS
from app.core import rate_limit, runtime
from app.core.operations import SafeFormatter

log = logging.getLogger('support')
router = Router()


@router.message(F.chat.type == 'private')
async def handle_message(message: Message, bot: Bot):
    if not message.from_user or message.from_user.is_bot:
        return
    sender = message.from_user.id
    if message.text and message.text.split()[0].split('@')[0] in ('/start', '/help'):
        text = ('Вы в поддержке NeuroPost. Ответьте на пересланное обращение, чтобы отправить ответ пользователю.'
                if sender in ADMIN_IDS else
                'Поддержка NeuroPost. Опишите проблему одним сообщением, укажите канал и приложите скриншот при необходимости. '
                'Обращение и ваш Telegram ID будут переданы администратору. Не отправляйте пароли и API-ключи. '
                'Ответ придёт сюда. Это не автоматический ИИ-консультант.')
        return await message.answer(text)
    if not rate_limit.admit(('support', sender), 15):
        return await message.answer('Слишком много сообщений. Подождите минуту.')
    if sender in ADMIN_IDS:
        reply = message.reply_to_message
        if not reply:
            return await message.answer('Ответьте именно на сообщение обращения. Сообщение без ответа никому не отправляется.')
        target = await db.fetch_one('SELECT user_id FROM support_routes WHERE admin_id=? AND message_id=? '
                                    "AND created_at>now()-interval '30 days'", (sender, reply.message_id))
        if not target:
            return await message.answer('Обращение не найдено или старше 30 дней. Ответьте на актуальное сообщение пользователя.')
        try:
            await runtime.wait_telegram(target['user_id'])
            await bot.copy_message(target['user_id'], sender, message.message_id)
        except Exception:
            log.warning('Ответ поддержки не доставлен', exc_info=True)
            return await message.answer('Ответ не доставлен. Возможно, пользователь заблокировал бота. Проверьте перед повтором.')
        return await message.answer('Ответ отправлен пользователю.')
    if not ADMIN_IDS:
        return await message.answer('Поддержка пока не настроена. Попробуйте позже.')
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await runtime.wait_telegram(admin_id)
            header = await bot.send_message(admin_id, f'Обращение NeuroPost\nПользователь: {message.from_user.full_name[:100]}'
                                            f'\nTelegram ID: {sender}\nОтветьте на это сообщение или на вложение ниже.')
            await db.execute('INSERT INTO support_routes(admin_id,message_id,user_id) VALUES(?,?,?) ON CONFLICT DO NOTHING',
                             (admin_id, header.message_id, sender))
            await runtime.wait_telegram(admin_id)
            copied = await bot.copy_message(admin_id, sender, message.message_id)
            await db.execute('INSERT INTO support_routes(admin_id,message_id,user_id) VALUES(?,?,?) ON CONFLICT DO NOTHING',
                             (admin_id, copied.message_id, sender))
            delivered += 1
        except Exception:
            log.warning('Обращение не доставлено администратору поддержки', exc_info=True)
    if delivered:
        await message.answer('Обращение передано в поддержку. Ответ придёт в этот чат.')
    else:
        await message.answer('Не удалось передать обращение. Администратору нужно открыть этого бота и нажать /start. Попробуйте позже.')


async def cleanup():
    while True:
        await db.execute("DELETE FROM support_routes WHERE created_at<now()-interval '30 days'")
        await asyncio.sleep(3600)


async def main():
    token = os.environ.get('SUPPORT_BOT_TOKEN', '').strip()
    if not token:
        raise RuntimeError('SUPPORT_BOT_TOKEN не настроен')
    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(SafeFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    await db.connect()
    bot = Bot(token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    janitor = asyncio.create_task(cleanup())
    try:
        me = await bot.get_me()
        await db.set_kv('support_username', me.username)
        log.info('Бот поддержки @%s запущен', me.username)
        await dispatcher.start_polling(bot, tasks_concurrency_limit=8, close_bot_session=False)
    finally:
        janitor.cancel()
        await asyncio.gather(janitor, return_exceptions=True)
        await bot.session.close()
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
