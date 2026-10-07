"""The single entry point into the agent system, for chat messages and for scheduled events alike."""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.advisor_agent import build_advisor_agent
from app.agents.base import RunContext
from app.agents.llm import LLMClient, LLMUnavailable
from app.agents.orchestrator import build_orchestrator
from app.agents.trace import Tracer
from app.config import Settings
from app.db import utcnow
from app.models import User
from app.services import memory

log = logging.getLogger(__name__)

APOLOGY = "Sorry, I couldn't process that just now, and nothing was changed. Please try again in a moment."

EventKind = Literal["daily_plan", "overdue_followup", "weekly_summary"]

EVENT_INSTRUCTIONS: dict[str, str] = {
    "daily_plan": (
        "Write {name}'s plan for today as a morning message. Use get_ranked_tasks. Open with one line "
        "on how the day looks, then the three tasks to do first with a short reason each, then anything "
        "overdue that needs a decision. Keep it under 120 words."
    ),
    "overdue_followup": (
        "Write a follow-up message to {name} about their overdue tasks. Use list_tasks with overdue set "
        "and assignee_name 'me'. List each overdue task with its id and how late it is, then ask for one "
        "of three things for each: is it done, does it need a new date, or is it blocked. Be brief and "
        "matter-of-fact, not scolding."
    ),
    "weekly_summary": (
        "Write {name}'s weekly summary. Use get_stats with days 7 and get_ranked_tasks. Say how many tasks "
        "were completed this week, how many are open and how many are overdue, name what deserves "
        "attention first next week, and end with one concrete suggestion. Keep it under 120 words."
    ),
}


class UnknownUser(Exception):
    pass


@dataclass
class RunResult:
    reply: str
    run_id: str
    status: Literal["completed", "failed"]

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class AgentRuntime:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMClient,
        settings: Settings,
        now: Callable[[], datetime] = utcnow,
    ):
        self.session_factory = session_factory
        self.llm = llm
        self.settings = settings
        self.now = now
        # One lock per user: two quick messages from the same person are handled in order.
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle_chat(self, user_id: int, text: str) -> RunResult:
        async def work(ctx: RunContext) -> str:
            ctx.history = await memory.load_history(ctx.session, user_id)
            reply = await build_orchestrator(ctx).run(text, ctx, history=ctx.history)
            await memory.save_message(ctx.session, user_id, "user", text)
            await memory.save_message(ctx.session, user_id, "assistant", reply)
            return reply

        return await self._run(user_id, trigger="chat", input=text, work=work)

    async def handle_event(self, user_id: int, kind: EventKind) -> RunResult:
        """A scheduled event goes to the same Advisor that answers chat questions."""

        async def work(ctx: RunContext) -> str:
            instruction = EVENT_INSTRUCTIONS[kind].format(name=ctx.user.name)
            ctx.tracer.step("scheduler", "handoff", "to Advisor Agent", {"to": "advisor", "event": kind})
            return await build_advisor_agent(ctx).run(instruction, ctx)

        return await self._run(user_id, trigger="scheduler", input=kind, work=work)

    async def _run(self, user_id: int, trigger: str, input: str, work) -> RunResult:
        async with self._locks[user_id]:
            run_id = str(uuid.uuid4())
            tracer = Tracer(run_id, user_id, trigger, input)
            status: Literal["completed", "failed"] = "completed"
            try:
                # One transaction per run: if anything fails, no half-finished change is kept.
                async with self.session_factory() as session:
                    user = await session.get(User, user_id)
                    if user is None:
                        raise UnknownUser(f"No user with id {user_id}")
                    ctx = RunContext(
                        run_id=run_id,
                        user=user,
                        now=self.now(),
                        original_message=input,
                        session=session,
                        tracer=tracer,
                        llm=self.llm,
                        settings=self.settings,
                    )
                    reply = await work(ctx)
                    await session.commit()
            except UnknownUser:
                raise
            except LLMUnavailable as exc:
                tracer.step("system", "error", str(exc))
                reply, status = APOLOGY, "failed"
            except Exception as exc:
                log.exception("Agent run %s failed", run_id)
                tracer.step("system", "error", f"{type(exc).__name__}: {exc}")
                reply, status = APOLOGY, "failed"

            await tracer.save(self.session_factory, output=reply, status=status)
            return RunResult(reply=reply, run_id=run_id, status=status)
