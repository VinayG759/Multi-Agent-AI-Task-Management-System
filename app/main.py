import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents.llm import AnthropicLLM, LLMClient, MissingKeyLLM
from app.agents.runtime import AgentRuntime
from app.api import chat as chat_api
from app.api import proactive as proactive_api
from app.api import tasks as tasks_api
from app.api import users as users_api
from app.auth import build_auth_router, require_sign_in
from app.channels.base import Channel
from app.channels.telegram import TelegramChannel
from app.config import Settings, get_settings
from app.db import Database
from app.demo import seed_if_empty
from app.proactive.jobs import ProactiveRunner
from app.proactive.scheduler import build_scheduler
from app.services.tasks import TaskNotFound

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class FreshStaticFiles(StaticFiles):
    """Dashboard files that browsers must re-check before reusing a saved copy.

    The check is cheap (the server answers "unchanged" when nothing is new), and it means an
    updated stylesheet or script is picked up straight away instead of hours later.
    """

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def build_llm(settings: Settings) -> LLMClient:
    if not settings.anthropic_api_key:
        log.warning("ANTHROPIC_API_KEY is not set: the task API and dashboard work, the AI assistant does not.")
        return MissingKeyLLM()
    return AnthropicLLM(settings.anthropic_api_key, effort=settings.llm_effort, fallbacks=settings.llm_fallbacks)


def create_app(
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    channels: list[Channel] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        await db.create_all()
        if settings.seed_demo_data and await seed_if_empty(db, settings):
            log.info("The database was empty: demo data loaded.")
        runtime = AgentRuntime(db.session_factory, llm or build_llm(settings), settings)

        active: list[Channel] = list(channels or [])
        telegram: TelegramChannel | None = None
        if settings.enable_telegram and settings.telegram_bot_token:
            telegram = TelegramChannel(runtime, db.session_factory, token=settings.telegram_bot_token)
            await telegram.start()
            active.append(telegram)
        elif settings.enable_telegram:
            log.warning("TELEGRAM_BOT_TOKEN is not set: the Telegram bot is not running.")

        proactive = ProactiveRunner(runtime, active, db.session_factory, settings)
        scheduler = build_scheduler(settings, proactive) if settings.enable_scheduler else None
        if scheduler:
            scheduler.start()

        app.state.db = db
        app.state.runtime = runtime
        app.state.channels = active
        app.state.proactive = proactive
        app.state.scheduler = scheduler
        yield

        if scheduler:
            scheduler.shutdown(wait=False)
        if telegram:
            await telegram.stop()
        await db.dispose()

    app = FastAPI(title="TaskPilot", description="Multi-agent AI task management", lifespan=lifespan)
    app.state.settings = settings
    # A blank username setting falls back to "admin", so a half-filled configuration cannot lock everyone out.
    username = settings.dashboard_username.strip() or "admin"
    if settings.dashboard_password:
        app.middleware("http")(require_sign_in(username, settings.dashboard_password))
    app.include_router(build_auth_router(username, settings.dashboard_password))

    @app.exception_handler(TaskNotFound)
    async def task_not_found(request: Request, exc: TaskNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    @app.get("/api/status", tags=["system"])
    async def status(request: Request):
        """What is configured, so the dashboard can say plainly why something is unavailable."""
        return {
            "assistant": bool(settings.anthropic_api_key) or llm is not None,
            "telegram": any(c.name == "telegram" for c in request.app.state.channels),
            "scheduler": request.app.state.scheduler is not None,
            "timezone": settings.timezone,
            "protected": bool(settings.dashboard_password),
        }

    app.include_router(tasks_api.router)
    app.include_router(users_api.router)
    app.include_router(chat_api.router)
    app.include_router(proactive_api.router)

    @app.get("/", include_in_schema=False)
    async def dashboard():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", FreshStaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
