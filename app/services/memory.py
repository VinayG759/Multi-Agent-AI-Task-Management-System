"""Conversation memory: the last few messages between one user and the assistant."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message

HISTORY_LIMIT = 10


async def save_message(session: AsyncSession, user_id: int, role: str, content: str) -> None:
    session.add(Message(user_id=user_id, role=role, content=content))
    await session.flush()


async def load_history(session: AsyncSession, user_id: int, limit: int = HISTORY_LIMIT) -> list[dict]:
    """The user's most recent messages, oldest first, in the shape the LLM expects.

    The assistant can speak twice in a row (a reminder, then a reply), so neighbouring messages
    from the same side are joined, and the history always opens with the user's side.
    """
    stmt = select(Message).where(Message.user_id == user_id).order_by(Message.id.desc()).limit(limit)
    rows = list(reversed((await session.scalars(stmt)).all()))

    history: list[dict] = []
    for row in rows:
        if history and history[-1]["role"] == row.role:
            history[-1]["content"] += "\n\n" + row.content
        else:
            history.append({"role": row.role, "content": row.content})
    if history and history[0]["role"] == "assistant":
        history.insert(0, {"role": "user", "content": "(earlier conversation not shown)"})
    return history
