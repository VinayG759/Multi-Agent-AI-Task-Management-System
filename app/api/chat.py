from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import UnknownUser
from app.api.deps import get_session
from app.models import AgentRun, AgentStep
from app.schemas import AgentRunDetailOut, AgentRunOut, ChatIn, ChatOut

router = APIRouter(prefix="/api", tags=["agents"])


@router.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn, request: Request):
    """The web channel: the same agent system Telegram uses, reachable over HTTP."""
    try:
        result = await request.app.state.runtime.handle_chat(body.user_id, body.text.strip())
    except UnknownUser as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatOut(reply=result.reply, run_id=result.run_id, status=result.status)


@router.get("/runs", response_model=list[AgentRunOut])
async def list_runs(limit: int = 30, session: AsyncSession = Depends(get_session)):
    stmt = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(min(max(limit, 1), 100))
    return list((await session.scalars(stmt)).all())


@router.get("/runs/{run_id}", response_model=AgentRunDetailOut)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    steps = await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.seq))
    return AgentRunDetailOut(**AgentRunOut.model_validate(run).model_dump(), steps=list(steps.all()))
