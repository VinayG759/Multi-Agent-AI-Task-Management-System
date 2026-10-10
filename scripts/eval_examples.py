"""Send the assignment's six example sentences through the real agent system and print what happened.

This calls the real LLM, so it needs ANTHROPIC_API_KEY in .env and costs a few cents per run.
It uses its own throwaway database, so it never touches your real tasks.

    uv run python scripts/eval_examples.py
"""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.agents.runtime import AgentRuntime
from app.config import get_settings
from app.db import Database, utcnow
from app.main import build_llm
from app.models import AgentStep, Priority, User
from app.schemas import TaskCreate
from app.services.tasks import TaskService

EXAMPLES = [
    "Create a high priority task for John to deploy the frontend by Friday.",
    "Assign API testing to Rahul.",
    "Show my pending tasks.",
    "What should I focus on today?",
    "Mark the CRM deployment task as completed.",
    "What are my overdue tasks?",
    # Not from the brief: one message that changes two tasks. Each change is reviewed separately.
    "Move the roadmap review to 6 Nov and the investor demo to the next day.",
]


async def main() -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env first.")

    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_all()
    async with db.session_factory() as session:
        me = User(name="Vinay", telegram_chat_id="eval")
        session.add_all([me, User(name="John")])
        await session.flush()
        service = TaskService(session, tz=settings.tz)
        now = utcnow()
        await service.create(TaskCreate(title="Deploy CRM to production", assignee_id=me.id, priority=Priority.urgent, due_at=now - timedelta(days=2)), actor="system:seed")
        await service.create(TaskCreate(title="Review Q4 roadmap draft", assignee_id=me.id, due_at=now + timedelta(days=3)), actor="system:seed")
        await service.create(TaskCreate(title="Prepare investor demo", assignee_id=me.id, priority=Priority.high, due_at=now + timedelta(days=1)), actor="system:seed")
        await session.commit()
        my_id = me.id

    runtime = AgentRuntime(db.session_factory, build_llm(settings), settings)
    for text in EXAMPLES:
        print("=" * 90)
        print(f"USER   {text}")
        started = utcnow()
        result = await runtime.handle_chat(my_id, text)
        seconds = (utcnow() - started).total_seconds()
        print(f"REPLY  [{result.status}, {seconds:.1f}s]\n{result.reply}\n")
        async with db.session_factory() as session:
            steps = await session.scalars(select(AgentStep).where(AgentStep.run_id == result.run_id).order_by(AgentStep.seq))
            for step in steps.all():
                print(f"   {step.seq:>2}. {step.agent:<12} {step.kind:<8} {step.summary[:110]}")
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
