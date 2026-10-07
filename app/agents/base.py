"""The engine every agent shares: ask the model, run the tools it asks for, repeat until it answers."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import LLMClient, LLMUnavailable
from app.agents.trace import Tracer
from app.config import Settings
from app.models import User

GAVE_UP = "I could not finish that request. Please try rephrasing it."


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Awaitable[Any]]

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class RunContext:
    """Everything one request carries with it as it moves between agents."""

    run_id: str
    user: User
    now: datetime
    original_message: str
    session: AsyncSession
    tracer: Tracer
    llm: LLMClient
    settings: Settings
    history: list[dict] = field(default_factory=list)

    def date_line(self) -> str:
        local = self.now.astimezone(self.settings.tz)
        return (
            f"Today is {local.strftime('%A %d %B %Y')}, the time is {local.strftime('%H:%M')} "
            f"({self.settings.timezone})."
        )


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


class Agent:
    def __init__(self, name: str, model: str, system_prompt: str, tools: list[Tool], max_steps: int = 6):
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.tools = {t.name: t for t in tools}
        self.max_steps = max_steps

    async def run(self, instruction: str, ctx: RunContext, history: list[dict] | None = None) -> str:
        messages = [*(history or []), {"role": "user", "content": instruction}]
        schemas = [t.schema() for t in self.tools.values()] or None

        for _ in range(self.max_steps):
            response = await ctx.llm.complete(
                model=self.model, system=self.system_prompt, messages=messages, tools=schemas
            )
            if not response.tool_calls:
                ctx.tracer.step(self.name, "llm", response.text, {"final": True})
                return response.text

            wanted = ", ".join(c.name for c in response.tool_calls)
            ctx.tracer.step(self.name, "llm", f"decided to call: {wanted}", {"note": response.text})
            messages.append({"role": "assistant", "content": response.raw_content})

            # Every tool result goes back in a single message, as the API expects.
            results = []
            for call in response.tool_calls:
                output, failed = await self._call(call.name, call.input)
                ctx.tracer.step(
                    self.name,
                    "tool",
                    f"{call.name} {'failed' if failed else 'returned'}",
                    {"tool": call.name, "input": call.input, "output": output, "error": failed},
                )
                result = {"type": "tool_result", "tool_use_id": call.id, "content": _as_text(output)}
                if failed:
                    result["is_error"] = True
                results.append(result)
            messages.append({"role": "user", "content": results})

        ctx.tracer.step(self.name, "error", f"stopped after {self.max_steps} steps without an answer")
        return GAVE_UP

    async def _call(self, name: str, arguments: dict) -> tuple[Any, bool]:
        """Run one tool. A failure is reported back to the model so it can correct itself."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: there is no tool named '{name}'.", True
        try:
            return await tool.handler(arguments), False
        except LLMUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001  the model needs to see any tool failure
            return f"Error: {exc}", True
