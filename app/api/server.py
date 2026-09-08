import hashlib

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import MINIAPP_DIR


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
    app = FastAPI(title="Нейропостинг", docs_url=None, redoc_url=None)
    app.state.bot = bot
    app.state.bot_username = bot_username
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
        return {"ok": True}

    @app.get("/")
    async def index() -> HTMLResponse:
        # Сам HTML не кешируем: он маленький, а внутри лежит версия ассетов.
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    return app
