"""Configure the user-authorized support bot without printing its credential."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aiogram import Bot
from aiogram.types import FSInputFile, InputProfilePhotoStatic, BotCommand
from dotenv import load_dotenv

from app.config import ROOT


async def main():
    load_dotenv(ROOT / '.support.env')
    bot = Bot(os.environ['SUPPORT_BOT_TOKEN'])
    try:
        me = await bot.get_me()
        if me.id != 8925420682:
            raise RuntimeError('Unexpected bot identity; stopped')
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError('Bot already has a webhook; cannot replace existing integration automatically')
        await bot.set_my_name(name='NeuroPost · Поддержка')
        await bot.set_my_description(description='Поддержка NeuroPost. Напишите о проблеме, укажите канал и приложите скриншот. Администратор ответит в этом чате. Не отправляйте пароли и API-ключи.')
        await bot.set_my_short_description(short_description='Помощь с NeuroPost: настройки, публикации и доступ.')
        await bot.set_my_commands(commands=[BotCommand(command='start', description='Написать в поддержку'),
                                            BotCommand(command='help', description='Как работает поддержка')])
        await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(ROOT/'app/assets/support-avatar.jpg')))
        print(f'SUPPORT_BOT=@{me.username}; profile and avatar configured')
    finally:
        await bot.session.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f'Support setup failed: {type(exc).__name__}; credentials withheld', file=sys.stderr)
        raise SystemExit(1)
