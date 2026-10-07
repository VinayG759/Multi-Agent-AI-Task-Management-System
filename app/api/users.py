from fastapi import APIRouter, Depends

from app.api.deps import get_task_service, get_user_service
from app.schemas import TaskStats, UserOut
from app.services.tasks import TaskService
from app.services.users import UserService

router = APIRouter(prefix="/api", tags=["team"])


@router.get("/users", response_model=list[UserOut])
async def list_users(service: UserService = Depends(get_user_service)):
    return await service.list_all()


@router.get("/stats", response_model=TaskStats)
async def stats(user_id: int | None = None, service: TaskService = Depends(get_task_service)):
    return await service.stats(user_id=user_id)
