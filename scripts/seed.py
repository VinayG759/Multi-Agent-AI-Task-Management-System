"""Fill the database with a small demo team and a spread of tasks.

    uv run python scripts/seed.py           add demo data if the database is empty
    uv run python scripts/seed.py --reset   wipe everything first
"""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.config import get_settings
from app.db import Base, Database, utcnow
from app.models import Priority, Status, Task, User
from app.schemas import TaskCreate
from app.services.tasks import TaskService

TEAM = ["Vinay", "John", "Rahul", "Priya"]

# (title, assignee, priority, status, due in days from now or None, description)
TASKS = [
    ("Deploy the frontend to production", "John", Priority.high, Status.in_progress, 2, "Release 1.4. Needs the API change merged first."),
    ("Test the payments API", "Rahul", Priority.high, Status.todo, 1, "Cover refunds and failed card retries."),
    ("Deploy CRM to production", "Vinay", Priority.urgent, Status.todo, -2, "Blocked last week by the migration; now unblocked."),
    ("Fix login redirect bug", "Priya", Priority.urgent, Status.in_progress, 0, "Users land on a blank page after signing in on mobile."),
    ("Write onboarding guide", "Priya", Priority.medium, Status.todo, 6, ""),
    ("Review Q4 roadmap draft", "Vinay", Priority.medium, Status.todo, 3, "Comments are due before Friday's planning call."),
    ("Update API documentation", "Rahul", Priority.low, Status.todo, 10, ""),
    ("Set up error monitoring", "John", Priority.medium, Status.todo, -1, "Alerts should go to the on-call channel."),
    ("Prepare investor demo", "Vinay", Priority.high, Status.todo, 4, "Ten-minute walkthrough of the agent workflow."),
    ("Clean up unused feature flags", "John", Priority.low, Status.todo, None, ""),
    ("Migrate database to Postgres", "Rahul", Priority.medium, Status.todo, 14, ""),
    ("Send weekly status update", "Vinay", Priority.medium, Status.done, -1, ""),
    ("Renew SSL certificate", "John", Priority.high, Status.done, -3, ""),
    ("Interview backend candidate", "Priya", Priority.medium, Status.done, -2, ""),
    ("Evaluate new analytics vendor", "Priya", Priority.low, Status.cancelled, None, "Dropped: budget moved to next quarter."),
]


async def main() -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    if "--reset" in sys.argv:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await db.create_all()

    async with db.session_factory() as session:
        if await session.scalar(select(func.count()).select_from(Task)):
            print("The database already has tasks. Run with --reset to start again.")
            return

        people = {}
        for name in TEAM:
            existing = (await session.scalars(select(User).where(User.name == name))).first()
            people[name] = existing or User(name=name)
            session.add(people[name])
        await session.flush()

        service = TaskService(session, tz=settings.tz)
        now = utcnow()
        for title, who, priority, status, days, description in TASKS:
            due = None if days is None else (now + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
            await service.create(
                TaskCreate(
                    title=title, description=description, assignee_id=people[who].id, creator_id=people["Vinay"].id,
                    priority=priority, status=status, due_at=due,
                ),
                actor="system:seed",
            )
        await session.commit()
    await db.dispose()
    print(f"Added {len(TEAM)} people and {len(TASKS)} tasks to {settings.database_url}")


if __name__ == "__main__":
    asyncio.run(main())
