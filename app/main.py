import asyncio
import logging
import os

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app import db
from app.ai import gemini
from app.api.server import create_app
from app.bot.handlers import router as bot_router
from app.bot import broadcasts
from app.config import BOT_TOKEN, HOST, PORT, DEV_AUTH
from app.core import scheduler, operations
from app.config import ROOT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")


async def run_service(bot, reporter) -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")
    if DEV_AUTH and os.getenv('APP_ENV') == 'production':
        raise SystemExit('DEV_AUTH запрещён в production')

    pool = await db.connect()
    # Runtime channel locks require exactly one bot worker. A second deployment
    # must fail before starting polling, AI or delivery loops.
    guard = await pool.acquire()
    if not await guard.fetchval('SELECT pg_try_advisory_lock(734619280145::bigint)'):
        await pool.release(guard)
        await db.close()
        raise SystemExit('Другой экземпляр приложения уже работает с этой базой')

    async def check_guard():
        while True:
            await guard.fetchval('SELECT 1')
            await asyncio.sleep(10)

    dispatcher = Dispatcher()
    dispatcher.include_router(broadcasts.router)
    dispatcher.include_router(bot_router)

    me = await bot.get_me()
    log.info("бот @%s запущен", me.username)

    # Фоном: три запроса к Gemini по 30 секунд таймаута задержали бы старт бота и
    # завалили healthcheck контейнера, а знать про мёртвую модель надо не в первую секунду.
    async def report_models() -> None:
        for role, verdict in (await gemini.check_models()).items():
            (log.info if "— ок" in verdict else log.error)("модель %s: %s", role, verdict)

    model_check = asyncio.create_task(report_models())

    tasks = [*scheduler.start(bot), asyncio.create_task(check_guard()), asyncio.create_task(broadcasts.run(bot)), reporter]
    application = create_app(bot, me.username)
    application.state.background_tasks = tasks
    server = uvicorn.Server(
        uvicorn.Config(application, host=HOST, port=PORT, log_level="warning", limit_concurrency=100)
    )
    # Uvicorn config uses its own non-propagating logger.
    logging.getLogger('uvicorn').propagate = True
    for handler in logging.getLogger('uvicorn').handlers:
        handler.setFormatter(operations.SafeFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    tasks += [asyncio.create_task(dispatcher.start_polling(bot, handle_signals=False)),
              asyncio.create_task(server.serve())]

    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in [*tasks, model_check]:
            task.cancel()
        await asyncio.gather(*tasks, model_check, return_exceptions=True)
        await pool.release(guard)
        await db.close()


async def main() -> None:
    journal = operations.install(ROOT / 'data/logs/errors.log')
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    reporter = asyncio.create_task(journal.run(bot))
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    def unhandled(loop, context):
        error = context.get('exception')
        log.error('Необработанная фоновая ошибка: %s', context.get('message', ''),
                  exc_info=(type(error), error, error.__traceback__) if error else None)
    loop.set_exception_handler(unhandled)
    try:
        await run_service(bot, reporter)
    except Exception:
        log.critical('Приложение аварийно остановлено', exc_info=True)
        raise
    finally:
        reporter.cancel()
        await asyncio.gather(reporter, return_exceptions=True)
        await journal.flush_alerts(bot)
        loop.set_exception_handler(previous_handler)
        logging.getLogger().removeHandler(journal)
        journal.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("остановлено")
