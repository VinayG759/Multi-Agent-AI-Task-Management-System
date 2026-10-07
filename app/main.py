from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.agents.llm import AnthropicLLM, LLMClient, MissingKeyLLM
from app.agents.runtime import AgentRuntime
from app.api import chat as chat_api
from app.api import tasks as tasks_api
from app.api import users as users_api
from app.config import Settings, get_settings
from app.db import Database
from app.services.tasks import TaskNotFound


def build_llm(settings: Settings) -> LLMClient:
    if not settings.anthropic_api_key:
        return MissingKeyLLM()
    return AnthropicLLM(settings.anthropic_api_key, effort=settings.llm_effort, fallbacks=settings.llm_fallbacks)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        await db.create_all()
        app.state.db = db
        app.state.runtime = AgentRuntime(db.session_factory, llm or build_llm(settings), settings)
        yield
        await db.dispose()

    app = FastAPI(title="TaskPilot", description="Multi-agent AI task management", lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(TaskNotFound)
    async def task_not_found(request: Request, exc: TaskNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok"}

    app.include_router(tasks_api.router)
    app.include_router(users_api.router)
    app.include_router(chat_api.router)
    return app


app = create_app()
