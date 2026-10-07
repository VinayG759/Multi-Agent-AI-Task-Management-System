from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.tasks import TaskService
from app.services.users import UserService


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One database session per request: commit if the route succeeds, roll back if it raises."""
    async with request.app.state.db.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_task_service(request: Request, session: AsyncSession = Depends(get_session)) -> TaskService:
    return TaskService(session, tz=request.app.state.settings.tz)


def get_user_service(session: AsyncSession = Depends(get_session)) -> UserService:
    return UserService(session)
