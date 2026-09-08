from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import MINIAPP_DIR


def create_app(bot, bot_username: str = "") -> FastAPI:
    app = FastAPI(title="Нейропостинг", docs_url=None, redoc_url=None)
    app.state.bot = bot
    app.state.bot_username = bot_username
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=MINIAPP_DIR), name="static")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(MINIAPP_DIR / "index.html")

    return app
