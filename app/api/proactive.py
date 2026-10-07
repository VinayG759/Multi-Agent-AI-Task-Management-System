from fastapi import APIRouter, HTTPException, Request

from app.proactive.jobs import JOBS, UnknownJob

router = APIRouter(prefix="/api/proactive", tags=["proactive"])


@router.get("/jobs")
async def list_jobs(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    jobs = []
    for name, info in JOBS.items():
        scheduled = scheduler.get_job(name) if scheduler else None
        next_run = scheduled.next_run_time.isoformat() if scheduled and scheduled.next_run_time else None
        jobs.append({"name": name, **info, "next_run": next_run})
    return jobs


@router.post("/{job}/run")
async def run_job(job: str, request: Request):
    """Run a proactive job immediately instead of waiting for its scheduled time."""
    try:
        sent = await request.app.state.proactive.run(job)
    except UnknownJob:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job}'. Jobs: {', '.join(JOBS)}") from None
    return {"job": job, "sent": sent}
