"""Records what each agent did during one run, so the dashboard can replay it step by step."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import utcnow
from app.models import AgentRun, AgentStep

SUMMARY_MAX = 500


class Tracer:
    """Collects steps in memory and writes them in one go at the end.

    The steps are saved in their own transaction, after the work is committed or rolled back,
    so a failed run still leaves a record of what happened.
    """

    def __init__(self, run_id: str, user_id: int | None, trigger: str, input: str):
        self.run_id = run_id
        self.user_id = user_id
        self.trigger = trigger
        self.input = input
        self.started_at: datetime = utcnow()
        self.steps: list[AgentStep] = []

    def step(self, agent: str, kind: str, summary: str, payload: dict | None = None) -> None:
        self.steps.append(
            AgentStep(
                run_id=self.run_id,
                seq=len(self.steps) + 1,
                agent=agent,
                kind=kind,
                summary=summary[:SUMMARY_MAX],
                payload=payload or {},
            )
        )

    def kinds(self) -> list[str]:
        return [s.kind for s in self.steps]

    async def save(self, session_factory: async_sessionmaker[AsyncSession], output: str, status: str) -> None:
        async with session_factory() as session:
            session.add(
                AgentRun(
                    id=self.run_id,
                    user_id=self.user_id,
                    trigger=self.trigger,
                    input=self.input,
                    output=output,
                    status=status,
                    started_at=self.started_at,
                    finished_at=utcnow(),
                )
            )
            await session.flush()
            session.add_all(self.steps)
            await session.commit()
