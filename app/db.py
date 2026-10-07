from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, returns timezone-aware UTC. SQLite keeps no timezone of its own."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        kwargs = {}
        if ":memory:" in url:
            # One shared connection, otherwise every session sees its own empty database.
            kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        elif url.startswith("sqlite"):
            # SQLite allows one writer at a time. An agent run can hold its transaction for several
            # seconds while the model thinks, so other writers wait for it instead of failing.
            kwargs = {"connect_args": {"timeout": 30}}
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create_all(self) -> None:
        from app import models  # noqa: F401  registers the tables on Base

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
