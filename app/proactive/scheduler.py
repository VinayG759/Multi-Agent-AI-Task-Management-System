"""The alarm clock: runs the proactive jobs at set times inside the server process."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings
from app.proactive.jobs import ProactiveRunner


def build_scheduler(settings: Settings, runner: ProactiveRunner) -> AsyncIOScheduler:
    tz = settings.tz
    triggers = {
        "daily_plan": CronTrigger(hour=9, minute=0, timezone=tz),
        "due_soon": IntervalTrigger(minutes=30, timezone=tz),
        "overdue_followup": CronTrigger(hour=10, minute=0, timezone=tz),
        "weekly_summary": CronTrigger(day_of_week="fri", hour=17, minute=0, timezone=tz),
    }
    scheduler = AsyncIOScheduler(timezone=tz)
    for job, trigger in triggers.items():
        # max_instances=1: a slow run is never overlapped by the next one.
        scheduler.add_job(runner.run_safely, trigger, args=[job], id=job, max_instances=1, coalesce=True)
    return scheduler
