"""Fill the database with a small demo team and a spread of tasks.

    uv run python scripts/seed.py           add demo data if the database is empty
    uv run python scripts/seed.py --reset   wipe everything first
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.db import Base, Database
from app.demo import TASKS, TEAM, seed_if_empty


async def main() -> None:
    settings = get_settings()
    db = Database(settings.database_url)
    if "--reset" in sys.argv:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await db.create_all()

    if await seed_if_empty(db, settings):
        print(f"Added {len(TEAM)} people and {len(TASKS)} tasks to {settings.database_url}")
    else:
        print("The database already has tasks. Run with --reset to start again.")
    await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
