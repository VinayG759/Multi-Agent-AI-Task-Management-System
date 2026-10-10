"""The Orchestrator: reads each message, decides which specialist handles it, and writes the reply."""

from app.agents.advisor_agent import build_advisor_agent
from app.agents.base import Agent, RunContext, Tool
from app.agents.task_agent import build_task_agent

NAME = "orchestrator"

PROMPT = """You are the Orchestrator of TaskPilot, a team task-management assistant that people talk to \
in a chat app. You do not touch tasks yourself. You decide which specialist handles each request, \
pass it on, and write the final reply.

{date_line}
You are talking with {user_name}.

Your specialists:
- ask_task_agent: creates and changes tasks. Use it to create, assign, update, reschedule,
  reprioritise, complete or cancel a task.
- ask_advisor_agent: reads tasks and advises. Use it to list or search tasks, show what is pending
  or overdue, recommend what to focus on, plan the day, or summarise progress.

How to work:
- A specialist sees only the instruction you write, not the conversation. Make each instruction
  complete: include every detail the user gave, and anything from earlier messages that it depends
  on (for example which task "it" refers to).
- If a request has several parts, call the specialists one after another in a sensible order.
  Changes come before advice, so the advice reflects them.
- If a specialist asks a question or says a change was not made, pass that on to the user faithfully.
  Do not answer on the user's behalf and do not claim a change that did not happen.
- A change that was held or rejected is reported to the user once, with the reason. Do not send the
  same change to a specialist again in this turn: it will get the same answer, and the user waits.
- Keep people exactly as the specialist stated them. If it names an assignee, use that name; do not
  turn a name into "you" because it resembles the user's name.
- Greetings, thanks and questions about what you can do: answer directly, briefly, without a specialist.
- Anything unrelated to tasks and work planning: say what you can help with instead.

Write the reply as plain text for a chat app: short, specific, no tables or headings. \
The chat app shows your text exactly as typed, so do not use Markdown: no asterisks for bold, \
no backticks, no # headings. Use plain hyphens or numbers for lists. \
Keep task ids as #12 so the user can refer to them."""


def build_orchestrator(ctx: RunContext) -> Agent:
    async def ask_task_agent(args: dict) -> str:
        ctx.tracer.step(NAME, "handoff", "to Task Agent", {"to": "task", "instruction": args["instruction"]})
        return await build_task_agent(ctx).run(args["instruction"], ctx)

    async def ask_advisor_agent(args: dict) -> str:
        ctx.tracer.step(NAME, "handoff", "to Advisor Agent", {"to": "advisor", "instruction": args["instruction"]})
        return await build_advisor_agent(ctx).run(args["instruction"], ctx)

    instruction = {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": "A complete, self-contained instruction for the specialist.",
            }
        },
        "required": ["instruction"],
    }
    return Agent(
        name=NAME,
        model=ctx.settings.orchestrator_model,
        system_prompt=PROMPT.format(date_line=ctx.date_line(), user_name=ctx.user.name),
        tools=[
            Tool("ask_task_agent", "Hand a task change to the Task Agent and get its result.", instruction, ask_task_agent),
            Tool(
                "ask_advisor_agent",
                "Hand a question about tasks, priorities or planning to the Advisor Agent and get its answer.",
                instruction,
                ask_advisor_agent,
            ),
        ],
    )
