from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_project_for_user
from app.database import get_db
from app.models import Job, JobStatus, Project, Queue, Worker, WorkerStatus
from app import schemas

router = APIRouter(prefix="/projects/{project_id}/dashboard", tags=["dashboard"])


@router.get("/health", response_model=schemas.SystemHealthOut)
async def system_health(project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)

    total_queues = (await db.execute(select(func.count(Queue.id)).where(Queue.project_id == project.id))).scalar_one()

    online_cutoff = now - timedelta(seconds=30)
    total_workers_online = (
        await db.execute(
            select(func.count(Worker.id)).where(
                Worker.project_id == project.id,
                Worker.status != WorkerStatus.OFFLINE,
                Worker.last_heartbeat_at >= online_cutoff,
            )
        )
    ).scalar_one()

    base = select(func.count(Job.id)).join(Queue).where(Queue.project_id == project.id)
    jobs_queued = (await db.execute(base.where(Job.status == JobStatus.QUEUED))).scalar_one()
    jobs_running = (await db.execute(base.where(Job.status == JobStatus.RUNNING))).scalar_one()
    jobs_completed_last_hour = (
        await db.execute(base.where(Job.status == JobStatus.COMPLETED, Job.completed_at >= hour_ago))
    ).scalar_one()
    jobs_failed_last_hour = (
        await db.execute(base.where(Job.status == JobStatus.DEAD_LETTER, Job.updated_at >= hour_ago))
    ).scalar_one()
    dead_letter_count = (await db.execute(base.where(Job.status == JobStatus.DEAD_LETTER))).scalar_one()

    return schemas.SystemHealthOut(
        total_queues=total_queues,
        total_workers_online=total_workers_online,
        jobs_queued=jobs_queued,
        jobs_running=jobs_running,
        jobs_completed_last_hour=jobs_completed_last_hour,
        jobs_failed_last_hour=jobs_failed_last_hour,
        dead_letter_count=dead_letter_count,
    )


@router.get("/queues", response_model=list[schemas.QueueStats])
async def all_queue_stats(project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    from app.routers.queues import queue_stats  # reuse single-queue logic
    queues = (await db.execute(select(Queue).where(Queue.project_id == project.id))).scalars().all()
    return [await queue_stats(q.id, project, db) for q in queues]


@router.get("/workers", response_model=list[schemas.WorkerOut])
async def list_workers_for_dashboard(project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    """JWT-authenticated mirror of the API-key-gated /workers list in the
    workers router, so the human dashboard doesn't need a project API key."""
    rows = (await db.execute(select(Worker).where(Worker.project_id == project.id).order_by(Worker.started_at.desc()))).scalars().all()
    return rows
