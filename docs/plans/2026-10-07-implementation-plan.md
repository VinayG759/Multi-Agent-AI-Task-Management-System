# Multi-Agent AI Task Management System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A chat bot that turns natural-language messages into managed tasks through a team of cooperating AI agents, proactively plans and reminds, and exposes everything through a FastAPI backend and a simple dashboard.

**Architecture:** One FastAPI process holds four layers: channels (Telegram, web) → agent runtime (Orchestrator delegating to Task, Advisor and Reviewer agents) → services (the only code that touches the database) → SQLite. A scheduler inside the same process feeds "events" into the same agent runtime, so proactive behaviour and chat share one brain.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2 (async) + SQLite, Anthropic SDK (Claude), python-telegram-bot 21+, APScheduler 3.x, pytest, uv.

**Spec:** The HiDevs hiring assignment brief. There is no separate spec file; the Design section below is the agreed design and takes its requirements from the brief.

**Companion:** [Plain-language version of this plan](2026-10-07-implementation-plan-simple.md)

## Global Constraints

- Backend is Python + FastAPI (mandatory in the brief).
- Python 3.11 (installed: 3.11.9). Dependencies managed with `uv`.
- Secrets (`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`) live only in `.env`, which is git-ignored. `.env.example` lists the names with empty values.
- Only `app/services/` reads or writes the database. Agents reach data through tools; tools call services.
- Every write an agent makes passes through the Reviewer gate in code. The LLM cannot skip it.
- LLMs never do date arithmetic or scoring. Code does; the LLM explains.
- All datetimes stored in UTC. One display timezone from settings (`Asia/Kolkata` default).
- Tests never call the real LLM or Telegram. They use `FakeLLM` and a fake channel.
- Git: stage explicit paths only (never `git add -A`), no `Co-Authored-By` trailer, work on `main`.

## Assumptions to confirm

| Assumption | Status |
|---|---|
| Chat platform is Telegram | Recommended; not explicitly confirmed by Vinay |
| LLM is Claude via Anthropic API key | Confirmed |
| SQLite database, local run | Assumed; Postgres is a one-line `DATABASE_URL` change |
| Real submission deadline | Unknown; the brief's date (15 June) has passed |
| Dashboard: plain HTML/CSS/JS served by FastAPI, no build step | Assumed; UI rules supplied by Vinay and written into Task 9 |
| Dashboard has no login; a "Viewing as" selector picks the user | Assumed; stated as a limit in the README |

## Review Focus

Failure modes the brief implies but does not spell out, most likely first. Each has a test in the task named.

1. **Ambiguous task reference** — "mark the deployment task done" matches two tasks. Expected: the bot asks which one; it never guesses. (Task 5)
2. **Unknown assignee** — "assign API testing to Rahul" when no Rahul exists. Expected: Rahul is created as an unlinked team member and the reply says so; the task is not silently left unassigned. (Task 5)
3. **Relative dates** — "by Friday" said on a Friday, or a date that resolves to the past. Expected: next Friday; a past date is flagged by the Reviewer and the user is asked. (Tasks 2, 5)
4. **LLM failure mid-request** — API error or timeout. Expected: the user gets a plain apology, nothing is half-written, the error is in the trace. (Task 6)
5. **Duplicate reminders** — the server restarts or a job runs twice. Expected: each reminder is sent at most once per task per day. (Task 8)

---

## Design

### The agents and why each exists

| Agent | Job | Tools it holds | Why it is separate |
|---|---|---|---|
| **Orchestrator** | Reads the message and recent conversation, decides which specialist to call and in what order, writes the final reply | `ask_task_agent`, `ask_advisor_agent` | One request can need several steps ("create X, then tell me what to focus on"). Something has to plan the sequence. |
| **Task Agent** | Turns an instruction into exact task operations: resolves which task and which person, fills fields | `find_user`, `search_tasks`, `create_task`, `update_task`, `complete_task` | The only agent with write tools. Precise extraction is a different skill from advising. |
| **Advisor Agent** | Answers "what should I focus on", builds daily plans and summaries | `get_ranked_tasks`, `list_tasks`, `get_stats` (all read-only) | Reasoning-heavy and read-only. It cannot change data even if it misbehaves. |
| **Reviewer Agent** | Checks every proposed write before it happens: duplicates, wrong person, past dates, mismatch with what the user said | none (receives the proposal and code-computed findings) | A second opinion on the only risky step. Enforced in code inside the write tools. |

Conversation memory and the priority scorer are deliberately **not** agents. Memory is a table; scoring is arithmetic. Calling them agents would be decoration.

### Chat flow

```
Telegram ──► TelegramChannel ──► AgentRuntime.handle_chat(user, text)
                                      │
                                      ▼
                                 Orchestrator ──► ask_task_agent ──► Task Agent
                                      │                                 │ create_task(...)
                                      │                                 ▼
                                      │                          Reviewer gate ──► TaskService ──► DB
                                      │◄──────── result ────────────────┘
                                      ├──► ask_advisor_agent ──► Advisor ──► priority scorer ──► DB (read)
                                      ▼
                                 final reply ──► TelegramChannel.send
```

Every hop is written to `agent_steps`, so a run can be replayed on the dashboard.

### Proactive flow

```
APScheduler job ──► pick users with something to say ──► AgentRuntime.handle_event(user, kind)
                                                              │
                                                              ▼
                                                    Advisor Agent (same one as chat)
                                                              ▼
                                              notifications table (dedupe) ──► Channel.send
```

Jobs: daily plan (09:00), due-soon reminders (every 30 min, tasks due in the next 24 h), overdue follow-up (10:00, one bundled message per user), weekly summary (Friday 17:00). Single reminders use a text template; plans and summaries use the Advisor. Each job also has `POST /api/proactive/{job}/run` so it can be triggered on demand for the demo.

### File structure

```
app/
  main.py              FastAPI app; lifespan starts DB, Telegram polling, scheduler
  config.py            Settings from .env
  db.py                Async engine, session factory, create_all
  models.py            SQLAlchemy tables
  schemas.py           Pydantic request/response models
  services/
    tasks.py           TaskService: every task read/write, events, similarity
    users.py           UserService: find, create, link Telegram account
    priority.py        Deterministic scoring and ranking
    memory.py          Conversation history load/save
  agents/
    llm.py             LLMClient protocol, AnthropicLLM, FakeLLM
    base.py            Tool, Agent (the tool-use loop), RunContext
    trace.py           Tracer: writes agent_runs / agent_steps
    tools.py           Tool builders bound to services
    task_agent.py      Prompt + tool set
    advisor_agent.py   Prompt + tool set
    reviewer_agent.py  Verdict, deterministic findings, LLM check
    orchestrator.py    Prompt + delegate tools
    runtime.py         AgentRuntime: handle_chat, handle_event, per-user lock
  channels/
    base.py            IncomingMessage, Channel protocol
    telegram.py        Polling adapter
  proactive/
    jobs.py            The four jobs
    scheduler.py       APScheduler wiring
  api/
    tasks.py  users.py  chat.py  stats.py  runs.py  proactive.py
  static/              Dashboard: index.html, css/, js/ (see Task 9)
scripts/
  seed.py              Demo team and tasks
  eval_examples.py     Runs the brief's six example sentences against the real LLM
tests/
  conftest.py  test_priority.py  test_tasks_service.py  test_api.py
  test_agent_loop.py  test_task_agent.py  test_reviewer.py
  test_orchestrator.py  test_telegram.py  test_proactive.py
```

### Data model

| Table | Columns |
|---|---|
| `users` | id, name, telegram_chat_id (nullable, unique), created_at |
| `tasks` | id, title, description, status (`todo`/`in_progress`/`done`/`cancelled`), priority (`low`/`medium`/`high`/`urgent`), assignee_id, creator_id, due_at, created_at, updated_at, completed_at |
| `task_events` | id, task_id, actor (`user:3`, `agent:task`, `system`), action, changes (JSON), created_at |
| `messages` | id, user_id, role (`user`/`assistant`), content, created_at |
| `agent_runs` | id (uuid), user_id, trigger (`chat`/`scheduler`), input, output, status, started_at, finished_at |
| `agent_steps` | id, run_id, seq, agent, kind (`llm`/`tool`/`handoff`/`review`), summary, payload (JSON), created_at |
| `notifications` | id, user_id, task_id (nullable), kind, sent_on (date); unique on (user_id, task_id, kind, sent_on) |

---

## Day 1 — Foundation

### Task 0: Project setup

**Files:** Create `pyproject.toml`, `.gitignore`, `.env.example`, `app/__init__.py`, `app/config.py`, `app/main.py`, `tests/conftest.py`, `tests/test_health.py`

**Produces:** `Settings` (fields: `database_url`, `anthropic_api_key`, `telegram_bot_token`, `timezone`, `orchestrator_model`, `specialist_model`, `reviewer_model`, `enable_telegram`, `enable_scheduler`); `app.main.app`.

- [ ] `git init`, branch `main`. `.gitignore` covers `.env`, `.venv/`, `__pycache__/`, `*.db`, `.pytest_cache/`.
- [ ] `uv init`, then `uv add fastapi "uvicorn[standard]" "sqlalchemy[asyncio]" aiosqlite pydantic-settings anthropic "python-telegram-bot>=21" "apscheduler>=3.10,<4" httpx` and `uv add --dev pytest pytest-asyncio ruff`.
- [ ] Write `tests/test_health.py`: `GET /health` returns 200 and `{"status": "ok"}`. Run it; expect failure (no app).
- [ ] Write `config.py` and `main.py` with the `/health` route. Run the test; expect pass.
- [ ] Run `uv run uvicorn app.main:app --reload` and open `/docs` to confirm FastAPI's auto-generated API page loads.
- [ ] Commit the files listed above by explicit path.

### Task 1: Data layer

**Files:** Create `app/db.py`, `app/models.py`; modify `app/main.py` (create tables at startup); extend `tests/conftest.py`

**Produces:** `get_session()` FastAPI dependency; `session_factory`; the seven models in the table above; a `session` pytest fixture backed by in-memory SQLite.

- [ ] Write `tests/test_models.py`: create a `User` and a `Task` assigned to them, read back, assert status defaults to `todo`, priority to `medium`, `created_at` is set.
- [ ] Implement `db.py` and `models.py`. Status and priority are Python `Enum`s stored as strings.
- [ ] Test the `notifications` unique constraint: inserting the same (user, task, kind, date) twice raises `IntegrityError`.
- [ ] Run tests, commit.

### Task 2: Task service and priority scoring

**Files:** Create `app/schemas.py`, `app/services/tasks.py`, `app/services/users.py`, `app/services/priority.py`, `tests/test_tasks_service.py`, `tests/test_priority.py`

**Produces:**

```python
class TaskService:
    def __init__(self, session: AsyncSession): ...
    async def create(self, data: TaskCreate, actor: str) -> Task
    async def get(self, task_id: int) -> Task | None
    async def update(self, task_id: int, data: TaskUpdate, actor: str) -> Task
    async def complete(self, task_id: int, actor: str) -> Task
    async def list(self, f: TaskFilter) -> list[Task]
    async def find_similar(self, title: str, assignee_id: int | None, threshold: float = 0.8) -> list[Task]
    async def stats(self, user_id: int | None, since: datetime) -> TaskStats

class UserService:
    async def find_by_name(self, name: str) -> list[User]          # case-insensitive, prefix match
    async def get_or_create_member(self, name: str) -> tuple[User, bool]
    async def link_telegram(self, chat_id: str, display_name: str) -> User

# priority.py
def score(task: Task, now: datetime) -> ScoredTask    # ScoredTask(task, score: float, reasons: list[str])
def rank(tasks: list[Task], now: datetime) -> list[ScoredTask]
```

`TaskCreate` and `TaskUpdate` enforce title 1 to 200 characters and description up to 2000; the dashboard form applies the same limits.

`TaskFilter`: `status: list[Status] | None`, `priority: list[Priority] | None`, `assignee_id`, `q` (matches title or description), `overdue: bool | None`, `due_before`, `due_after`, `sort` (`due_at`/`priority`/`created_at`/`score`), `limit` (default 50, max 200), `offset`.

Scoring rule: priority weight (urgent 40, high 30, medium 20, low 10) + due weight (overdue 40 plus 2 per day late capped at 20; due today 30; within 3 days 20; within 7 days 10; no date 0) + 5 if `in_progress`. `reasons` records each part in words, e.g. `"overdue by 2 days"`.

- [ ] Priority tests: an overdue medium task outranks a high task due next week; two equal scores order by earlier `due_at`; a `done` task is excluded from `rank`; `reasons` is non-empty.
- [ ] Service tests: `create` writes one `task_events` row with action `created`; `update` records only changed fields in `changes`; `complete` sets `completed_at` and status `done`; completing an already-done task is a no-op, not an error; `update` on a missing id raises `TaskNotFound`.
- [ ] Filter tests: `q` matches title and description case-insensitively; `overdue=True` returns only open tasks with `due_at` in the past; `limit` above 200 is clamped.
- [ ] `find_similar` tests (uses `difflib.SequenceMatcher` on lower-cased titles, open tasks only): "Deploy frontend" matches "deploy the frontend"; does not match "Deploy backend" at threshold 0.8.
- [ ] `link_telegram` test: an existing unlinked member named "Rahul" is linked when a Telegram user with first name "rahul" starts the bot; otherwise a new user is created.
- [ ] Implement, run, commit.

### Task 3: REST API

**Files:** Create `app/api/tasks.py`, `app/api/users.py`, `app/api/stats.py`, `tests/test_api.py`; modify `app/main.py`

**Produces:**

| Route | Purpose |
|---|---|
| `GET /api/tasks` | List with every `TaskFilter` field as a query parameter; returns `{items, total}` |
| `GET /api/tasks/{id}` | One task with its events |
| `POST /api/tasks` | Create (actor `user:dashboard`) |
| `PATCH /api/tasks/{id}` | Update fields or status, including to `cancelled` and back from `cancelled` or `done` (the dashboard's Undo and Restore) |
| `GET /api/users` | Team list |
| `GET /api/stats` | Counts by status and priority, overdue count, completed in last 7 days |

- [ ] Tests with `httpx.AsyncClient`: create then fetch returns the same task; `PATCH` to `done` sets `completed_at`; `PATCH` from `done` back to `todo` clears `completed_at`; unknown id returns 404; an invalid priority, an empty title, and a 201-character title each return 422 naming the field; `GET /api/tasks?overdue=true&assignee_id=1` filters correctly and `total` counts all matches, not just the page.
- [ ] Implement routers. Errors map through one exception handler: `TaskNotFound` → 404.
- [ ] Run tests, check `/docs` shows all routes, commit.

**Day 1 also includes Vinay's Architecture Copilot session** (see "Vinay's own tasks").

---

## Day 2 — Agents

### Task 4: Agent foundation

**Files:** Create `app/agents/llm.py`, `app/agents/base.py`, `app/agents/trace.py`, `tests/test_agent_loop.py`

**Produces:**

```python
@dataclass
class ToolCall:      id: str; name: str; input: dict
@dataclass
class LLMResponse:   text: str; tool_calls: list[ToolCall]; raw_content: list[dict]

class LLMClient(Protocol):
    async def complete(self, *, model: str, system: str, messages: list[dict],
                       tools: list[dict] | None = None, max_tokens: int = 1024) -> LLMResponse

@dataclass
class Tool:          name: str; description: str; input_schema: dict
                     handler: Callable[[dict], Awaitable[Any]]

@dataclass
class RunContext:    run_id: str; user: User; now: datetime; original_message: str
                     session: AsyncSession; tracer: Tracer; llm: LLMClient

class Agent:
    def __init__(self, name: str, model: str, system_prompt: str, tools: list[Tool], max_steps: int = 6)
    async def run(self, instruction: str, ctx: RunContext, history: list[dict] | None = None) -> str
```

`Agent.run` is the tool-use loop: send the instruction, and while the model asks for tools, run each handler, append the results, and call again. It stops when the model answers in plain text or `max_steps` is reached.

- [ ] Before writing `AnthropicLLM`, load the `claude-api` skill reference and confirm the current tool-use request and response shapes and model ids. Do not write SDK calls from memory.
- [ ] `FakeLLM` takes a scripted list of `LLMResponse`s and records every call it receives.
- [ ] Loop tests with `FakeLLM`: plain-text answer returns immediately; one tool call runs the handler and feeds the result back; a handler that raises returns the error text to the model as a tool result rather than crashing; an unknown tool name does the same; hitting `max_steps` returns a fixed "could not finish" message.
- [ ] Tracer test: a run with one tool call writes an `agent_runs` row and steps of kind `llm`, `tool`, `llm` in order.
- [ ] `AnthropicLLM` retries twice on rate-limit and 5xx errors with backoff, then raises `LLMUnavailable`.
- [ ] Implement, run, commit.

### Task 5: Specialist agents

**Files:** Create `app/agents/tools.py`, `app/agents/task_agent.py`, `app/agents/advisor_agent.py`, `app/agents/reviewer_agent.py`, `tests/test_task_agent.py`, `tests/test_reviewer.py`

**Produces:**

```python
class ProposedAction(BaseModel):
    kind: Literal["create", "update", "complete"]
    args: dict
    findings: list[str]            # filled by code before the LLM sees it

class Verdict(BaseModel):
    decision: Literal["approve", "clarify", "reject"]
    message: str                   # question for the user, or the reason

class ReviewerAgent:
    async def review(self, proposal: ProposedAction, ctx: RunContext) -> Verdict

def build_task_agent(ctx: RunContext) -> Agent
def build_advisor_agent(ctx: RunContext) -> Agent
```

Task Agent tools and inputs:

| Tool | Input | Behaviour |
|---|---|---|
| `find_user` | `name` | Returns matching users; empty list if none |
| `search_tasks` | `query`, optional `status`, `assignee_name` | Returns id, title, status, assignee, due date |
| `create_task` | `title`, optional `description`, `assignee_name`, `priority`, `due_date` (ISO) | Reviewer gate, then `TaskService.create` |
| `update_task` | `task_id`, any of `title`, `description`, `assignee_name`, `priority`, `due_date`, `status` | Reviewer gate, then `TaskService.update` |
| `complete_task` | `task_id` | Reviewer gate, then `TaskService.complete` |

Advisor tools: `get_ranked_tasks(scope: "me"|"team", limit)`, `list_tasks(...filters)`, `get_stats(scope, days)`.

The system prompt for every agent states today's date, weekday, and timezone, so "Friday" can be resolved; the tool then validates the ISO date it receives.

Reviewer findings computed in code before the LLM call: similar open tasks (`find_similar`), due date in the past, assignee newly created, target task already `done`. The Reviewer LLM (the small fast model) then receives the user's original message, the proposal, and the findings, and returns a `Verdict`. A write tool that receives `clarify` or `reject` does not touch the database and returns the message to the Task Agent, which relays it.

- [ ] Tool tests (no LLM): `create_task` with an approving fake reviewer writes the task; with a `clarify` verdict writes nothing and returns the question; `assignee_name` of an unknown person creates an unlinked member and adds the finding `"new team member created: Rahul"`; an unparseable `due_date` returns an error string to the model.
- [ ] Reviewer tests: a proposal whose title is similar to an open task carries a duplicate finding; a past due date carries a finding; with no findings and a matching message a scripted `approve` passes through; malformed LLM output falls back to `clarify` with a generic question (fail closed).
- [ ] Task Agent tests with `FakeLLM`: scripted `search_tasks` returning two matches followed by a text question results in no write (Review Focus 1); scripted `create_task` produces one task and one `review` trace step.
- [ ] Advisor test: `get_ranked_tasks` for scope `me` returns only the current user's open tasks, in score order, each with reasons.
- [ ] Implement, run, commit.

### Task 6: Orchestrator, memory, and the chat endpoint

**Files:** Create `app/services/memory.py`, `app/agents/orchestrator.py`, `app/agents/runtime.py`, `app/api/chat.py`, `app/api/runs.py`, `tests/test_orchestrator.py`; modify `app/main.py`

**Produces:**

```python
class AgentRuntime:
    def __init__(self, session_factory, llm: LLMClient, settings: Settings)
    async def handle_chat(self, user_id: int, text: str) -> str
    async def handle_event(self, user_id: int, kind: Literal["daily_plan", "overdue_followup", "weekly_summary"]) -> str
```

| Route | Purpose |
|---|---|
| `POST /api/chat` | `{user_id, text}` → `{reply, run_id}`. A web channel: the same runtime without Telegram |
| `GET /api/runs` | Recent agent runs |
| `GET /api/runs/{id}` | One run with its ordered steps |

The Orchestrator's tools are `ask_task_agent(instruction)` and `ask_advisor_agent(instruction)`. Each builds the specialist, runs it, and returns its answer. The Orchestrator receives the last 10 messages of the user's conversation as history. `handle_chat` holds a per-user `asyncio.Lock` so two quick messages are processed in order, saves both the user message and the reply to `messages`, and wraps the run in one database transaction.

- [ ] Memory tests: history returns the last 10 messages oldest-first; another user's messages are never returned.
- [ ] Orchestrator tests with `FakeLLM`: a scripted `ask_task_agent` then `ask_advisor_agent` then final text produces two `handoff` steps in order; a greeting scripted as plain text calls no specialist.
- [ ] Failure test (Review Focus 4): the LLM raising `LLMUnavailable` midway returns the fixed apology, leaves no new task rows, and marks the run `failed`.
- [ ] Ordering test: two concurrent `handle_chat` calls for the same user do not interleave their saved messages.
- [ ] `POST /api/chat` test with `FakeLLM` injected: returns the reply and a `run_id` that `GET /api/runs/{id}` resolves.
- [ ] **Live check (real LLM, costs a few cents):** `scripts/eval_examples.py` sends the brief's six example sentences through `/api/chat` and prints each reply and the trace. Read the output by hand; fix prompts where an agent misreads a sentence.
- [ ] Commit.

### Task 7: Telegram channel

**Files:** Create `app/channels/base.py`, `app/channels/telegram.py`, `tests/test_telegram.py`; modify `app/main.py`

**Produces:**

```python
@dataclass
class IncomingMessage:  channel: str; external_user_id: str; display_name: str; text: str

class Channel(Protocol):
    name: str
    async def send(self, user: User, text: str) -> bool     # False if the user is not linked to this channel
```

The adapter uses polling (the bot asks Telegram for new messages, so no public address is needed), started and stopped from FastAPI's lifespan and skipped when `enable_telegram` is false. `/start` links the Telegram account to a user. Any text message shows the "typing" indicator, calls `runtime.handle_chat`, and sends the reply. Replies over Telegram's 4096-character limit are split.

- [ ] Tests with a fake bot object: `/start` creates and links a user; a text message from a linked user reaches `handle_chat` and the reply is sent back; a message from someone who never ran `/start` gets the "please send /start" reply; a photo or sticker gets a polite "text only" reply; a 5000-character reply is sent as two messages.
- [ ] **Manual check with Vinay:** token in `.env`, start the server, send the six example sentences from a real Telegram chat. This is the first point where the real product is visible.
- [ ] Commit.

---

## Day 3 — Autonomy, dashboard, delivery

### Task 8: Proactive assistance

**Files:** Create `app/proactive/jobs.py`, `app/proactive/scheduler.py`, `app/api/proactive.py`, `tests/test_proactive.py`; modify `app/main.py`

**Produces:**

```python
async def run_daily_plan(runtime, channels, session_factory, now) -> int        # returns messages sent
async def run_due_soon(channels, session_factory, now) -> int
async def run_overdue_followup(runtime, channels, session_factory, now) -> int
async def run_weekly_summary(runtime, channels, session_factory, now) -> int
```

`POST /api/proactive/{job}/run` triggers any job immediately.

- [ ] `daily_plan` test: a linked user with open tasks receives one message; a user with no open tasks receives none; an unlinked user is skipped.
- [ ] `due_soon` test: a task due in 5 hours triggers a reminder; one due in 3 days does not; a `done` task does not.
- [ ] Dedupe test (Review Focus 5): running `due_soon` twice in the same day sends one message, because the `notifications` row already exists.
- [ ] `overdue_followup` test: three overdue tasks for one user produce one bundled message, not three.
- [ ] Scheduler test: with `enable_scheduler` false no jobs are registered; with it true the four jobs exist with the expected triggers in the settings timezone.
- [ ] Manual check: trigger each job through the API and confirm the Telegram messages arrive and read well.
- [ ] Commit.

### Task 9: Dashboard

**Approach:** Plain HTML, CSS and JavaScript modules served by FastAPI from `app/static/`. No build step and no Node dependency, so the whole product starts with one command. The look is restrained SaaS: neutral surfaces, one accent colour, a dense readable table, no gradients, illustrations, or decorative animation.

**Files:** Create `app/static/index.html`, `app/static/css/tokens.css`, `app/static/css/app.css`, `app/static/js/main.js`, `api.js`, `state.js`, `tasks.js`, `drawer.js`, `toast.js`, `shortcuts.js`, `activity.js`; modify `app/main.py` (mount static files, serve `index.html` at `/`)

| File | Responsibility |
|---|---|
| `tokens.css` | Every colour, spacing step, font size, radius and shadow as CSS variables, with light and dark values. Nothing else in the CSS uses a raw value |
| `app.css` | Layout shell and the shared components: button, input, select, badge, table, drawer, dialog, toast |
| `api.js` | All `fetch` calls; turns HTTP errors into one error shape `{message, fields}` |
| `state.js` | Filters, sort, selected task and "viewing as" user; reads and writes the URL query string |
| `tasks.js` | Stats strip, toolbar, table, empty and loading states |
| `drawer.js` | Task detail, history, and the create/edit form with validation |
| `toast.js` | Feedback messages, Undo, and the screen-reader live region |
| `shortcuts.js` | Keyboard shortcuts and the help overlay |
| `activity.js` | Agent runs and their steps |

**Screens (three, sharing one shell: top bar, left navigation, content):**

1. **Tasks** — stats strip (Open, Overdue, Due today, Completed this week; each is a one-click filter), quick-add bar, toolbar (search, status, priority, assignee, sort, preset chips), task table.
2. **Task detail** — a side drawer over the Tasks screen so the list stays visible: all fields, edit, mark complete, cancel/restore, and the change history from `task_events`.
3. **Agent activity** — recent agent runs; opening one shows the ordered steps (which agent, which tool, the Reviewer's verdict).

There is no login. A "Viewing as" selector in the top bar picks the current user, stored in `localStorage`; "My tasks" and the quick-add bar use it. The README states this plainly as a limit.

**The UI rules and what each one means here.** Rules that say the same thing are grouped.

| Rule | Concrete requirement |
|---|---|
| **1, 14 Consistency** | All styling comes from `tokens.css`. One button style per role (primary, secondary, danger). Status and priority look identical in the table, the drawer, and the activity view. Dates use one format everywhere. Every screen uses the same shell. |
| **2, 13 Shortcuts and productivity** | Keys: `/` focus search, `n` new task, `j`/`k` move selection, `Enter` open, `e` edit, `c` mark complete, `Esc` close, `?` show shortcut help. Status can be changed from the table row in one click. Preset chips: My tasks, Overdue, Due this week, High priority. The quick-add bar accepts a sentence ("assign API testing to Rahul by Friday") and sends it to `POST /api/chat`, so the agents fill the form. Filters live in the URL, so a view survives refresh and can be shared. |
| **3 Informative feedback** | Every action produces a toast naming what happened ("Marked 'Deploy frontend' complete"). Loading shows skeleton rows. Buttons show a busy state while saving. The toolbar shows the result count ("12 tasks, 3 overdue"). Empty states say why the list is empty and what to do next. |
| **4 Closure** | Creating, editing or completing ends with a confirmation toast, the drawer closing, the row updating in place, and focus returning to that row. No action ends silently. |
| **5, 9 Error handling and prevention** | Title is required and at most 200 characters; description at most 2000. Errors appear under the field in plain words, and Save stays disabled until the form is valid. A past due date shows a warning and asks for confirmation. Server validation errors (422) map back to their fields. A network failure shows a toast with Retry and keeps what the user typed. Actions that make no sense are disabled (Complete on a task already done). The same limits are enforced in `schemas.py`, so the API rejects what the form would. |
| **6 Easy reversal** | Complete, status change, edit, and cancel each show an Undo button in the toast for 8 seconds; Undo sends the previous values back through `PATCH`. Tasks are never hard-deleted: Cancel sets status `cancelled`, and a cancelled task has Restore. |
| **7 User in control** | The list never rearranges on its own. Background refresh shows a banner ("3 tasks changed. Refresh") that the user clicks. Nothing opens unprompted. `Esc` and a visible Close button leave any drawer or dialog. Unsaved edits prompt before the drawer closes. |
| **8 Low memory load** | Active filters show as removable chips above the table. Detail opens beside the list instead of replacing it. Due dates show both forms ("Fri 9 Oct, in 2 days"). Badges carry a text label, not colour alone. The `?` hint is always visible in the top bar. |
| **10 Satisfaction and speed** | Changes apply to the screen immediately and roll back with an error toast if the server refuses. Search waits 250 ms after typing stops. The layout works from 360 px wide to desktop; below 720 px the table becomes stacked cards and the drawer becomes full-screen. Transitions are 150 ms or less. |
| **11 Accessibility** | WCAG 2.1 AA: text contrast at least 4.5:1 in both themes; every control reachable and usable by keyboard with a visible focus ring; real `<button>`, `<label>`, `<table>` elements; toasts announced through an `aria-live` region; the drawer traps focus while open and returns it on close; `prefers-reduced-motion` and `prefers-color-scheme` respected; touch targets at least 44 px on small screens. |
| **12 Ease of use** | Three screens. One primary action per screen ("New task"). Labels are plain words. A new user can create, find, and complete a task without reading instructions. |

**Backend changes this task needs** (made in the tasks named, not here):

- `GET /api/tasks` returns `{items, total}` so the result count and paging work. (Task 3)
- `TaskCreate`/`TaskUpdate` enforce title 1 to 200 characters and description up to 2000. (Task 2)
- `PATCH /api/tasks/{id}` accepts `cancelled` and moving back from `cancelled` or `done`, which is what Undo and Restore send. (Task 3)

**Steps:**

- [ ] `tokens.css` and `app.css`: shell and shared components in both themes. Check contrast of every text/background pair before building screens.
- [ ] `api.js` and `state.js`. Check: changing a filter updates the URL; loading that URL restores the filter.
- [ ] Tasks screen: stats strip, toolbar, table, loading, empty and error states.
- [ ] Drawer: detail, history, create/edit form with the validation above.
- [ ] `toast.js` with Undo; wire complete, status change, edit, cancel/restore with optimistic update and rollback.
- [ ] `shortcuts.js` and the `?` overlay. Shortcuts are ignored while typing in a field.
- [ ] Quick-add bar through `POST /api/chat`; show the assistant's reply, then refresh the list.
- [ ] Agent activity screen.
- [ ] Verification pass, done in a real browser and recorded as a checklist in the README: one line per rule in the table above with how it was checked; a keyboard-only run through create, find, edit, complete, undo; a 360 px width check; both colour themes; server stopped mid-action to confirm the error and Retry path.
- [ ] Commit.

### Task 10: Delivery

**Files:** Create `README.md`, `docs/architecture.md`, `scripts/seed.py`, `Dockerfile`, `docker-compose.yml`

- [ ] `scripts/seed.py` creates a small demo team and about fifteen tasks across statuses, priorities and due dates, including overdue ones.
- [ ] `README.md`: what it is, how to run it in five commands, the agent design and the reason for each agent, the two flows, the scalability section below, known limits.
- [ ] `docs/architecture.md`: the Architecture Copilot diagram, then Vinay's evaluation of it (liked, changed, would improve). The wording of the evaluation is Vinay's.
- [ ] `Dockerfile` and `docker-compose.yml` (optional in the brief; small once the app runs). Verify with `docker compose up` and a `/health` call.
- [ ] Full test run, `ruff check`, final commit. Push only when Vinay says so.

### Scalability notes for the README

| Today | At scale |
|---|---|
| SQLite file | Postgres through `DATABASE_URL`; migrations with Alembic |
| Telegram polling in-process | Webhook endpoint behind a load balancer |
| APScheduler in-process | A job queue with separate workers, so jobs survive restarts and do not run twice |
| Per-user `asyncio.Lock` | A distributed lock, so several server copies can run |
| One timezone setting | Per-user timezone |
| Sequential specialist calls | Parallel calls where steps are independent; prompt caching for the fixed system prompts |

---

## Vinay's own tasks

These cannot be delegated and are part of what HiDevs is grading.

1. **Get the real deadline** from HiDevs.
2. **Telegram bot token** from @BotFather (`/newbot`). Put it in `.env`; do not paste it in chat.
3. **Architecture Copilot session** (Day 1). Give it the brief, keep the diagram it produces, and note what you agree and disagree with compared with the Design section here.
4. **Written evaluation** of the Copilot's design: what you liked, what you changed, what could be improved.
5. **Real-chat testing** after Task 7 and Task 8.
6. **Demo video**, 3 to 5 minutes.
7. **UI rules** for Task 9. Done; written into Task 9.
