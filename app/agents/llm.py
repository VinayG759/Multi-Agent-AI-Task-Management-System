"""The one place that talks to the LLM provider. Agents depend on LLMClient, never on the SDK."""

from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

REQUEST_TIMEOUT_SECONDS = 90.0
FALLBACK_BETA = "server-side-fallback-2026-07-01"
REFUSAL_TEXT = "I'm not able to help with that request."


class LLMUnavailable(Exception):
    """The model could not be reached or refused to run. The user gets an apology; nothing is saved."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The provider's own content blocks, sent back unchanged on the next turn.
    raw_content: list[Any] = field(default_factory=list)


class LLMClient(Protocol):
    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 16000,
    ) -> LLMResponse: ...


class AnthropicLLM:
    def __init__(self, api_key: str, effort: str | None = "low", fallbacks: bool = True):
        # The SDK itself retries rate limits, timeouts and server errors (twice, with backoff).
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=2)
        self.effort = effort
        self.fallbacks = fallbacks

    async def complete(self, *, model, system, messages, tools=None, max_tokens=16000) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": model, "system": system, "messages": messages, "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = tools
        if self.effort and not model.startswith("claude-haiku"):  # Haiku 4.5 rejects the effort setting
            kwargs["output_config"] = {"effort": self.effort}

        try:
            if self.fallbacks and not model.startswith("claude-haiku"):
                response = await self.client.beta.messages.create(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailable("The Anthropic API key was rejected. Check ANTHROPIC_API_KEY in .env.") from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable("The AI service is rate-limiting requests. Try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"The AI service returned an error ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable("Could not reach the AI service. Check the internet connection.") from exc

        if response.stop_reason == "refusal":
            return LLMResponse(text=REFUSAL_TEXT)

        text = "".join(block.text for block in response.content if block.type == "text").strip()
        calls = [
            ToolCall(id=block.id, name=block.name, input=dict(block.input))
            for block in response.content
            if block.type == "tool_use"
        ]
        return LLMResponse(text=text, tool_calls=calls, raw_content=list(response.content))


class MissingKeyLLM:
    """Stands in when no API key is configured, so the rest of the app still starts."""

    async def complete(self, **kwargs) -> LLMResponse:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set. Add it to .env and restart the server.")


class FakeLLM:
    """Test double: replays a scripted list of responses and records every request it receives."""

    def __init__(self, script: list[LLMResponse | Exception]):
        self.script = list(script)
        self.calls: list[dict] = []

    @staticmethod
    def says(text: str) -> LLMResponse:
        return LLMResponse(text=text, raw_content=[{"type": "text", "text": text}])

    @staticmethod
    def calls_tool(name: str, input: dict, call_id: str | None = None) -> LLMResponse:
        call_id = call_id or f"call_{name}"
        block = {"type": "tool_use", "id": call_id, "name": name, "input": input}
        return LLMResponse(tool_calls=[ToolCall(id=call_id, name=name, input=input)], raw_content=[block])

    async def complete(self, *, model, system, messages, tools=None, max_tokens=16000) -> LLMResponse:
        self.calls.append({"model": model, "system": system, "messages": list(messages), "tools": tools})
        if not self.script:
            raise AssertionError("FakeLLM ran out of scripted responses")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step
