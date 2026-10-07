from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Priority, Status

TITLE_MAX = 200
DESCRIPTION_MAX = 2000
LIST_LIMIT_MAX = 200


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class _TaskFields(BaseModel):
    @field_validator("title", mode="before", check_fields=False)
    @classmethod
    def _strip_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_at", check_fields=False)
    @classmethod
    def _due_utc(cls, value):
        return _to_utc(value)


class TaskCreate(_TaskFields):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str = Field(default="", max_length=DESCRIPTION_MAX)
    status: Status = Status.todo
    priority: Priority = Priority.medium
    assignee_id: int | None = None
    creator_id: int | None = None
    due_at: datetime | None = None


class TaskUpdate(_TaskFields):
    """Only fields that were actually sent are applied (see model_fields_set)."""

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    status: Status | None = None
    priority: Priority | None = None
    assignee_id: int | None = None
    due_at: datetime | None = None


class TaskFilter(BaseModel):
    status: list[Status] | None = None
    priority: list[Priority] | None = None
    assignee_id: int | None = None
    q: str | None = None
    overdue: bool | None = None
    due_before: datetime | None = None
    due_after: datetime | None = None
    sort: Literal["due_at", "priority", "created_at", "score"] = "due_at"
    limit: int = Field(default=50, ge=1)
    offset: int = Field(default=0, ge=0)

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value):
        return min(value, LIST_LIMIT_MAX)

    @field_validator("due_before", "due_after")
    @classmethod
    def _utc(cls, value):
        return _to_utc(value)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    linked: bool = False


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    status: Status
    priority: Priority
    assignee: UserOut | None
    creator: UserOut | None
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    overdue: bool = False


class TaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    changes: dict
    created_at: datetime


class TaskDetailOut(TaskOut):
    events: list[TaskEventOut] = []


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int


class TaskStats(BaseModel):
    open: int
    overdue: int
    due_today: int
    completed_recent: int
    by_status: dict[str, int]
    by_priority: dict[str, int]


class ChatIn(BaseModel):
    user_id: int
    text: str = Field(min_length=1, max_length=4000)


class ChatOut(BaseModel):
    reply: str
    run_id: str
    status: str


class AgentStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    agent: str
    kind: str
    summary: str
    payload: dict
    created_at: datetime


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: int | None
    trigger: str
    input: str
    output: str
    status: str
    started_at: datetime
    finished_at: datetime | None


class AgentRunDetailOut(AgentRunOut):
    steps: list[AgentStepOut] = []
