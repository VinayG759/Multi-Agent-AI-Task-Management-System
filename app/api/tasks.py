from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_task_service
from app.models import Task
from app.schemas import TaskCreate, TaskDetailOut, TaskFilter, TaskListOut, TaskOut, TaskUpdate
from app.services.tasks import TaskNotFound, TaskService

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

DASHBOARD_ACTOR = "user:dashboard"


def _out(task: Task, service: TaskService) -> TaskOut:
    out = TaskOut.model_validate(task)
    out.overdue = bool(task.is_open and task.due_at and task.due_at < service.now())
    return out


@router.get("", response_model=TaskListOut)
async def list_tasks(
    filters: Annotated[TaskFilter, Query()],
    service: TaskService = Depends(get_task_service),
):
    tasks = await service.search(filters)
    return TaskListOut(items=[_out(t, service) for t in tasks], total=await service.count(filters))


@router.post("", response_model=TaskOut, status_code=201)
async def create_task(data: TaskCreate, service: TaskService = Depends(get_task_service)):
    return _out(await service.create(data, actor=DASHBOARD_ACTOR), service)


@router.get("/{task_id}", response_model=TaskDetailOut)
async def get_task(task_id: int, service: TaskService = Depends(get_task_service)):
    task = await service.get(task_id)
    if task is None:
        raise TaskNotFound(task_id)
    detail = TaskDetailOut(**_out(task, service).model_dump(), events=await service.events(task_id))
    return detail


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(task_id: int, data: TaskUpdate, service: TaskService = Depends(get_task_service)):
    return _out(await service.update(task_id, data, actor=DASHBOARD_ACTOR), service)
