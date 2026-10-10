"""The tools agents use to reach task data. Tools call services; they never touch the database directly."""

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.agents.base import RunContext, Tool
from app.agents.reviewer_agent import ProposedAction, ReviewerAgent
from app.models import OPEN_STATUSES, Priority, Status, Task, User
from app.schemas import TaskCreate, TaskFilter, TaskUpdate
from app.services.priority import rank
from app.services.tasks import TaskService
from app.services.users import UserService

TASK_ACTOR = "agent:task"
ME = {"me", "myself", "i", "my", "mine"}
NOBODY = {"", "none", "nobody", "unassigned", "no one"}
NO_DATE = {"", "none", "no date", "clear"}
# A date with no time means "by the end of that working day".
END_OF_DAY = time(18, 0)
STEM_LENGTH = 5
HISTORY_SHOWN = 5
STOPWORDS = {"the", "a", "an", "task", "tasks", "to", "for", "of", "my", "on", "in", "and", "about"}


def parse_due(value: str, tz: ZoneInfo) -> datetime:
    """Accepts 2026-10-09 or 2026-10-09T15:00. The model supplies the date; code validates it."""
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.combine(datetime.fromisoformat(text).date(), END_OF_DAY, tzinfo=tz)
        else:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
    except ValueError:
        raise ValueError(f"'{value}' is not a valid date. Use ISO format like 2026-10-09 or 2026-10-09T15:00.") from None
    return parsed.astimezone(UTC)


def describe_due(due: datetime | None, now: datetime, tz: ZoneInfo) -> str | None:
    if due is None:
        return None
    local = due.astimezone(tz)
    days = (local.date() - now.astimezone(tz).date()).days
    if due < now:
        when = "overdue today" if days == 0 else f"overdue by {-days} day{'s' if days != -1 else ''}"
    elif days == 0:
        when = "today"
    elif days == 1:
        when = "tomorrow"
    else:
        when = f"in {days} days"
    return f"{local.strftime('%a %d %b %Y, %H:%M')} ({when})"


def task_brief(task: Task, now: datetime, tz: ZoneInfo) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "priority": task.priority.value,
        "assignee": task.assignee.name if task.assignee else None,
        "due": describe_due(task.due_at, now, tz),
        "overdue": bool(task.is_open and task.due_at and task.due_at < now),
    }


def _keywords(query: str) -> list[str]:
    """Key words cut to a five-letter stem, so 'deployment' still finds 'Deploy CRM'."""
    return [w[:STEM_LENGTH] for w in re.findall(r"\w+", query.lower()) if w not in STOPWORDS]


class _Toolbox:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.tz = ctx.settings.tz
        self.tasks = TaskService(ctx.session, tz=self.tz, now=lambda: ctx.now)
        self.users = UserService(ctx.session)

    def brief(self, task: Task) -> dict:
        return task_brief(task, self.ctx.now, self.tz)

    async def person(self, name: str) -> User | None:
        """A known person, or None if nobody by that name exists yet."""
        wanted = name.strip()
        if wanted.lower() in ME or wanted.lower() == self.ctx.user.name.lower():
            return self.ctx.user
        found = await self.users.find_by_name(wanted)
        if len(found) > 1:
            names = ", ".join(u.name for u in found)
            raise ValueError(f"More than one person matches '{wanted}': {names}. Ask the user which one they mean.")
        return found[0] if found else None

    async def match_tasks(self, query: str, filters: TaskFilter) -> list[Task]:
        """Tasks within the filters, ranked by how many of the query's words they contain."""
        candidates = await self.tasks.search(filters.model_copy(update={"limit": 200, "offset": 0}))
        words = _keywords(query)
        if not words:
            return candidates[:10]
        scored = []
        for task in candidates:
            text = f"{task.title} {task.description}".lower()
            hits = sum(1 for w in words if w in text)
            if hits:
                scored.append((hits, task))
        if not scored:
            return []
        best = max(hits for hits, _ in scored)
        # Keep only the strongest matches, so one clear hit is not buried under weak ones.
        return [task for hits, task in scored if hits == best][:10]


def _statuses(value: str | None) -> list[Status] | None:
    if value in (None, "", "all", "any"):
        return None
    if value == "open":
        return list(OPEN_STATUSES)
    return [Status(value)]


class TaskTools(_Toolbox):
    """Tools for the Task Agent. The three write tools all pass through the Reviewer."""

    def __init__(self, ctx: RunContext, reviewer: ReviewerAgent):
        super().__init__(ctx)
        self.reviewer = reviewer

    async def _reviewed(self, proposal: ProposedAction) -> dict | None:
        """Returns None when approved; otherwise the answer to hand back without writing anything."""
        verdict = await self.reviewer.review(proposal, self.ctx)
        if verdict.approved:
            return None
        return {
            "status": "not_done",
            "reviewer_decision": verdict.decision,
            "message": verdict.message,
            "note": "Nothing was changed. Give this message to the user as it is.",
        }

    async def search_tasks(self, args: dict) -> dict:
        assignee_id = None
        if args.get("assignee_name"):
            person = await self.person(args["assignee_name"])
            if person is None:
                return {"tasks": [], "note": f"Nobody named '{args['assignee_name']}' is on the team."}
            assignee_id = person.id
        filters = TaskFilter(status=_statuses(args.get("status", "open")), assignee_id=assignee_id)
        found = await self.match_tasks(args.get("query", ""), filters)
        return {"count": len(found), "tasks": [self.brief(t) for t in found]}

    async def create_task(self, args: dict) -> dict:
        title = args["title"].strip()
        due = parse_due(args["due_date"], self.tz) if args.get("due_date") else None
        priority = Priority(args.get("priority") or "medium")
        assignee_name = (args.get("assignee_name") or "me").strip()
        assignee = await self.person(assignee_name)

        findings = []
        for other in await self.tasks.find_similar(title):
            owner = other.assignee.name if other.assignee else "nobody"
            findings.append(f"possible duplicate of open task #{other.id} '{other.title}' (assigned to {owner})")
        if due and due < self.ctx.now:
            findings.append(f"the due date {describe_due(due, self.ctx.now, self.tz)} is in the past")
        if assignee is None:
            findings.append(f"new team member will be added: {assignee_name.title()}")

        proposal = ProposedAction(
            kind="create",
            args={
                "title": title,
                "description": args.get("description", ""),
                "assignee": assignee.name if assignee else assignee_name.title(),
                "priority": priority.value,
                "due": describe_due(due, self.ctx.now, self.tz),
            },
            findings=findings,
        )
        if (held := await self._reviewed(proposal)) is not None:
            return held

        notes = []
        if assignee is None:
            assignee, _ = await self.users.get_or_create_member(assignee_name)
            notes.append(f"{assignee.name} was not on the team yet, so I added them as a new member.")
        task = await self.tasks.create(
            TaskCreate(
                title=title,
                description=args.get("description", ""),
                priority=priority,
                assignee_id=assignee.id,
                creator_id=self.ctx.user.id,
                due_at=due,
            ),
            actor=TASK_ACTOR,
        )
        return {"status": "created", "task": self.brief(task), "notes": notes}

    async def update_task(self, args: dict) -> dict:
        task = await self.tasks.get(int(args["task_id"]))
        if task is None:
            raise ValueError(f"There is no task with id {args['task_id']}. Use search_tasks to find it.")

        fields: dict = {}
        changes: dict = {}
        findings = []
        new_member: str | None = None

        if args.get("title"):
            fields["title"] = changes["title"] = args["title"].strip()
        if "description" in args:
            fields["description"] = changes["description"] = args["description"]
        if args.get("priority"):
            fields["priority"] = Priority(args["priority"])
            changes["priority"] = args["priority"]
        if args.get("status"):
            fields["status"] = Status(args["status"])
            changes["status"] = args["status"]
        if "due_date" in args:
            if (args["due_date"] or "").strip().lower() in NO_DATE:
                fields["due_at"] = None
                changes["due"] = "removed"
            else:
                fields["due_at"] = parse_due(args["due_date"], self.tz)
                changes["due"] = describe_due(fields["due_at"], self.ctx.now, self.tz)
                if fields["due_at"] < self.ctx.now:
                    findings.append(f"the new due date {changes['due']} is in the past")
        if "assignee_name" in args:
            name = (args["assignee_name"] or "").strip()
            if name.lower() in NOBODY:
                fields["assignee_id"] = None
                changes["assignee"] = "nobody"
            else:
                person = await self.person(name)
                if person is None:
                    new_member = name
                    findings.append(f"new team member will be added: {name.title()}")
                else:
                    fields["assignee_id"] = person.id
                changes["assignee"] = person.name if person else name.title()

        if not changes:
            raise ValueError("No changes were given. Say which field to change.")
        if not task.is_open and "status" not in fields:
            findings.append(f"this task is already {task.status.value}")

        proposal = ProposedAction(kind="update", args={"task": self.brief(task), "changes": changes}, findings=findings)
        if (held := await self._reviewed(proposal)) is not None:
            return held

        notes = []
        if new_member:
            person, _ = await self.users.get_or_create_member(new_member)
            fields["assignee_id"] = person.id
            notes.append(f"{person.name} was not on the team yet, so I added them as a new member.")
        updated = await self.tasks.update(task.id, TaskUpdate(**fields), actor=TASK_ACTOR)
        return {"status": "updated", "task": self.brief(updated), "changed": changes, "notes": notes}

    async def complete_task(self, args: dict) -> dict:
        task = await self.tasks.get(int(args["task_id"]))
        if task is None:
            raise ValueError(f"There is no task with id {args['task_id']}. Use search_tasks to find it.")
        if task.status == Status.done:
            return {"status": "already_done", "task": self.brief(task)}

        findings = [f"this task is {task.status.value}, not open"] if not task.is_open else []
        proposal = ProposedAction(kind="complete", args={"task": self.brief(task)}, findings=findings)
        if (held := await self._reviewed(proposal)) is not None:
            return held
        done = await self.tasks.complete(task.id, actor=TASK_ACTOR)
        return {"status": "completed", "task": self.brief(done)}

    def tools(self) -> list[Tool]:
        priority = {"type": "string", "enum": [p.value for p in Priority]}
        status = {"type": "string", "enum": [s.value for s in Status]}
        due = {"type": "string", "description": "ISO date or datetime, e.g. 2026-10-09 or 2026-10-09T15:00."}
        person = {"type": "string", "description": "The person's name as the user gave it, or 'me' for the user."}
        return [
            Tool(
                "search_tasks",
                "Find tasks by words in their title or description. Always use this to get a task's id "
                "before updating or completing it. Returns the best matches only.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Key words, e.g. 'CRM deployment'."},
                        "status": {
                            "type": "string",
                            "enum": ["open", "all", *[s.value for s in Status]],
                            "description": "Defaults to open (todo and in_progress).",
                        },
                        "assignee_name": person,
                    },
                    "required": ["query"],
                },
                self.search_tasks,
            ),
            Tool(
                "create_task",
                "Create a new task. Checked by the Reviewer before it is saved.",
                {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short phrase starting with a verb."},
                        "description": {"type": "string"},
                        "assignee_name": person,
                        "priority": priority,
                        "due_date": due,
                    },
                    "required": ["title"],
                },
                self.create_task,
            ),
            Tool(
                "update_task",
                "Change fields of an existing task: title, description, assignee, priority, due date or status. "
                "Only include the fields that should change. Checked by the Reviewer before it is saved.",
                {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "integer"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "assignee_name": {**person, "description": "A name, 'me', or 'none' to unassign."},
                        "priority": priority,
                        "due_date": {**due, "description": due["description"] + " Use 'none' to remove it."},
                        "status": status,
                    },
                    "required": ["task_id"],
                },
                self.update_task,
            ),
            Tool(
                "complete_task",
                "Mark a task as done. Checked by the Reviewer before it is saved.",
                {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
                self.complete_task,
            ),
        ]


class AdvisorTools(_Toolbox):
    """Read-only tools for the Advisor. Nothing here can change a task."""

    async def _scope(self, scope: str | None) -> int | None:
        return None if scope == "team" else self.ctx.user.id

    async def get_ranked_tasks(self, args: dict) -> dict:
        assignee_id = await self._scope(args.get("scope"))
        open_tasks = await self.tasks.search(TaskFilter(status=list(OPEN_STATUSES), assignee_id=assignee_id, limit=200))
        ranked = rank(open_tasks, self.ctx.now, self.tz)
        limit = int(args.get("limit") or 10)
        return {
            "open_count": len(ranked),
            "tasks": [{**self.brief(s.task), "score": s.score, "reasons": s.reasons} for s in ranked[:limit]],
        }

    async def list_tasks(self, args: dict) -> dict:
        assignee_id = None
        name = (args.get("assignee_name") or "").strip()
        if name and name.lower() not in {"team", "everyone", "all"}:
            person = await self.person(name)
            if person is None:
                return {"count": 0, "tasks": [], "note": f"Nobody named '{name}' is on the team."}
            assignee_id = person.id
        filters = TaskFilter(
            status=_statuses(args.get("status", "open")),
            priority=[Priority(args["priority"])] if args.get("priority") else None,
            assignee_id=assignee_id,
            overdue=True if args.get("overdue") else None,
            due_before=self.ctx.now + timedelta(days=int(args["due_within_days"])) if args.get("due_within_days") else None,
            limit=int(args.get("limit") or 20),
        )
        if args.get("query"):
            found = await self.match_tasks(args["query"], filters)
            total = len(found)
        else:
            found = await self.tasks.search(filters)
            total = await self.tasks.count(filters)
        return {"count": total, "tasks": [self.brief(t) for t in found]}

    async def get_task_details(self, args: dict) -> dict:
        """Everything about one task. Lists stay short; this is where the full record comes from."""
        task = await self.tasks.get(int(args["task_id"]))
        if task is None:
            raise ValueError(f"There is no task with id {args['task_id']}. Use list_tasks with a query to find it.")

        def when(moment):
            return moment.astimezone(self.tz).strftime("%a %d %b %Y, %H:%M") if moment else None

        events = await self.tasks.events(task.id)
        return {
            **self.brief(task),
            "description": task.description or None,
            "created_by": task.creator.name if task.creator else None,
            "created": when(task.created_at),
            "last_changed": when(task.updated_at),
            "completed": when(task.completed_at),
            "history": [
                {"when": when(e.created_at), "by": e.actor, "action": e.action, "changes": e.changes}
                for e in events[:HISTORY_SHOWN]
            ],
        }

    async def get_stats(self, args: dict) -> dict:
        days = int(args.get("days") or 7)
        stats = await self.tasks.stats(await self._scope(args.get("scope")), since=self.ctx.now - timedelta(days=days))
        return {**stats.model_dump(), "completed_in_last_days": days}

    def tools(self) -> list[Tool]:
        scope = {
            "type": "string",
            "enum": ["me", "team"],
            "description": "'me' for the user's own tasks (default), 'team' for everyone's.",
        }
        return [
            Tool(
                "get_ranked_tasks",
                "Open tasks ordered by urgency. Each has a score and the reasons behind it "
                "(priority, due date, overdue). Use this for 'what should I focus on' and for daily plans.",
                {"type": "object", "properties": {"scope": scope, "limit": {"type": "integer"}}},
                self.get_ranked_tasks,
            ),
            Tool(
                "list_tasks",
                "List tasks with filters. Use this for 'show my pending tasks', 'what is overdue', "
                "'what is Rahul working on'.",
                {
                    "type": "object",
                    "properties": {
                        "assignee_name": {
                            "type": "string",
                            "description": "A name, 'me' for the user, or 'team' for everyone. Defaults to everyone.",
                        },
                        "status": {"type": "string", "enum": ["open", "all", *[s.value for s in Status]]},
                        "priority": {"type": "string", "enum": [p.value for p in Priority]},
                        "overdue": {"type": "boolean"},
                        "due_within_days": {"type": "integer"},
                        "query": {"type": "string", "description": "Words to look for in the title or description."},
                        "limit": {"type": "integer"},
                    },
                },
                self.list_tasks,
            ),
            Tool(
                "get_task_details",
                "The full record of one task: description, who created it and when, when it was completed, "
                "and its most recent changes with who made them. Use it when someone asks about a specific "
                "task. Find the id with list_tasks first if you do not have it.",
                {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
                self.get_task_details,
            ),
            Tool(
                "get_stats",
                "Counts: open, overdue, due today, completed recently, and breakdowns by status and priority.",
                {"type": "object", "properties": {"scope": scope, "days": {"type": "integer"}}},
                self.get_stats,
            ),
        ]
