"""The Reviewer: a second opinion on every change before it reaches the database.

It is called from inside the write tools, in code, so the Task Agent cannot skip it.
"""

import json
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.agents.base import RunContext

NAME = "reviewer"
RECENT_TURNS = 4
UNSURE = "I want to double-check before changing anything. Can you confirm exactly what you'd like me to do?"

PROMPT = """You are the Reviewer in a team task-management assistant.

Another agent wants to change the task database. You are given the user's request, the recent \
conversation, the proposed change, and findings that were computed by code. Decide whether the \
change should go ahead, then call submit_verdict exactly once.

You review one change to one task. A request that covers several tasks reaches you as several \
separate proposals, one after another, and `already_approved_in_this_request` lists the ones you \
have approved so far. Judge only whether this one change is something the user asked for. Never \
hold a change because another part of the request is not included in it: the other parts are \
separate proposals, either already approved or still to come.

- approve: the change matches what the user asked for and no finding needs the user's decision.
  A finding that a new team member will be added is information only. Approve it.
- clarify: the user should decide something first. Examples: a likely duplicate of an existing task,
  a due date in the past, a task that is already completed, or a request too vague to be sure the
  right task or person was chosen. Put one short question for the user in `message`.
- reject: the proposal contradicts the request (wrong person, wrong task, invented details).
  Say why in `message`.

If the recent conversation shows the user has already answered the question a finding raises \
(for example they confirmed they do want the duplicate), approve.

Do not be fussy. A missing due date, priority or description is normal and is not a reason to hold a change."""

VERDICT_TOOL = {
    "name": "submit_verdict",
    "description": "Record your decision about the proposed change.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approve", "clarify", "reject"]},
            "message": {
                "type": "string",
                "description": "For clarify: the question for the user. For reject: the reason. For approve: a few words.",
            },
        },
        "required": ["decision", "message"],
        "additionalProperties": False,
    },
}


class ProposedAction(BaseModel):
    kind: Literal["create", "update", "complete"]
    args: dict
    findings: list[str] = []


class Verdict(BaseModel):
    decision: Literal["approve", "clarify", "reject"]
    message: str = ""

    @property
    def approved(self) -> bool:
        return self.decision == "approve"


def _parse(response) -> Verdict:
    """Anything that is not a clear verdict becomes 'clarify': when unsure, do not write."""
    candidates = [c.input for c in response.tool_calls if c.name == VERDICT_TOOL["name"]]
    if not candidates and response.text:
        start, end = response.text.find("{"), response.text.rfind("}")
        if start != -1 and end > start:
            try:
                candidates.append(json.loads(response.text[start : end + 1]))
            except json.JSONDecodeError:
                pass
    for candidate in candidates:
        try:
            return Verdict.model_validate(candidate)
        except ValidationError:
            continue
    return Verdict(decision="clarify", message=UNSURE)


class ReviewerAgent:
    def __init__(self, model: str):
        self.model = model

    async def review(self, proposal: ProposedAction, ctx: RunContext) -> Verdict:
        recent = [m for m in ctx.history[-RECENT_TURNS:] if isinstance(m.get("content"), str)]
        brief = {
            "user_request": ctx.original_message,
            "recent_conversation": recent,
            "proposed_change": {"kind": proposal.kind, **proposal.args},
            "findings": proposal.findings or ["none"],
            "already_approved_in_this_request": list(ctx.approved_changes),
        }
        response = await ctx.llm.complete(
            model=self.model,
            system=f"{PROMPT}\n\n{ctx.date_line()}",
            messages=[{"role": "user", "content": json.dumps(brief, indent=2, default=str)}],
            tools=[VERDICT_TOOL],
        )
        verdict = _parse(response)
        if verdict.approved:
            ctx.approved_changes.append(brief["proposed_change"])
        ctx.tracer.step(
            NAME,
            "review",
            f"{verdict.decision}: {verdict.message}",
            {"proposal": brief["proposed_change"], "findings": proposal.findings, "decision": verdict.decision},
        )
        return verdict
