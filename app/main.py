import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app import db
from app.api.server import create_app
from app.bot.handlers import router as bot_router
from app.config import BOT_TOKEN, HOST, PORT
from app.core import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")

    await db.connect()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher()
    dispatcher.include_router(bot_router)

    me = await bot.get_me()
    log.info("бот @%s запущен", me.username)

    server = uvicorn.Server(
        uvicorn.Config(create_app(bot, me.username), host=HOST, port=PORT, log_level="warning")
    )
    tasks = scheduler.start(bot)

    try:
        await asyncio.gather(
            dispatcher.start_polling(bot, handle_signals=False),
            server.serve(),
        )
    finally:
        for task in tasks:
            task.cancel()
        await bot.session.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("остановлено")
