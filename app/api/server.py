import hashlib
import asyncio
import re
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import MINIAPP_DIR


class ErrorLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        async def checked_send(message):
            if message['type'] == 'http.response.start' and message['status'] >= 500:
                logging.getLogger('api').error('HTTP %s: %s %s', message['status'], scope['method'], scope['path'])
            await send(message)
        try:
            await self.app(scope, receive, checked_send)
        except Exception:
            logging.getLogger('api').exception('Ошибка запроса %s %s', scope['method'], scope['path'])
            raise


class RequestBudgetMiddleware:
    """Reject oversized or stalled JSON requests before parsing them into objects."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/') or scope['method'] not in ('POST','PATCH','PUT'):
            return await self.app(scope, receive, send)
        body = bytearray()
        limit = 4 * 1024 * 1024 if re.fullmatch(r'/api/channels/\d+/logo', scope['path']) else 65536
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    body.extend(message.get('body',b''))
                    if len(body)>limit:
                        return await JSONResponse({'detail':'Слишком большой запрос'}, status_code=413)(scope, receive, send)
                    if not message.get('more_body'):
                        break
        except TimeoutError:
            return await JSONResponse({'detail':'Истекло время получения запроса'}, status_code=408)(scope, receive, send)
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope, replay, send)


def _asset_version() -> str:
    """Отпечаток статики: подмешивается в ссылки на app.js и style.css.

    Telegram и браузеры держат мини-приложение в кеше агрессивно, и после деплоя
    пользователь легко получает свежий HTML со старым скриптом. Версия в адресе делает
    каждую сборку новым файлом с точки зрения кеша.
    """
    digest = hashlib.sha256()
    for name in ("app.js", "style.css", "index.html"):
        path = MINIAPP_DIR / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def create_app(bot, bot_username: str = "") -> FastAPI:
    app = FastAPI(title="Нейропостинг", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestBudgetMiddleware)
    app.add_middleware(ErrorLoggingMiddleware)
    app.state.bot = bot
    app.state.bot_username = bot_username
    app.state.background_tasks = []
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=MINIAPP_DIR), name="static")

    version = _asset_version()
    page = (
        (MINIAPP_DIR / "index.html")
        .read_text(encoding="utf-8")
        .replace("/static/style.css", f"/static/style.css?v={version}")
        .replace("/static/app.js", f"/static/app.js?v={version}")
    )

    @app.get("/healthz")
    async def healthz() -> dict:
        from app import db
        try:
            async with asyncio.timeout(3):
                await db.fetch_one('SELECT 1 AS ok')
            if any(task.done() for task in app.state.background_tasks):
                raise RuntimeError('worker stopped')
        except Exception as exc:
            raise HTTPException(503, 'Сервис временно недоступен') from exc
        return {"ok": True}

    @app.get("/")
    async def index() -> HTMLResponse:
        # Сам HTML не кешируем: он маленький, а внутри лежит версия ассетов.
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    return app
