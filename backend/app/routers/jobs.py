from datetime import datetime, timedelta, timezone

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_project_for_user, require_project_role
from app.database import get_db
from app.models import (
    DeadLetterEntry, Job, JobStatus, OrgRole, Project, Queue, RetryPolicy, ScheduledJob, Batch, User,
)
from app import schemas
from app.websocket.manager import manager

router = APIRouter(prefix="/projects/{project_id}", tags=["jobs"])


async def _get_queue(db: AsyncSession, project_id: int, queue_id: int) -> Queue:
    queue = (await db.execute(select(Queue).where(Queue.id == queue_id, Queue.project_id == project_id))).scalar_one_or_none()
    if not queue:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Queue not found")
    return queue


def _resolve_run_at_and_status(body: schemas.JobCreate) -> tuple[datetime, JobStatus]:
    now = datetime.now(timezone.utc)
    if body.run_at:
        run_at = body.run_at
    elif body.delay_seconds:
        run_at = now + timedelta(seconds=body.delay_seconds)
    else:
        run_at = now
    job_status = JobStatus.QUEUED if run_at <= now else JobStatus.SCHEDULED
    return run_at, job_status


async def _materialize_job(db: AsyncSession, queue: Queue, body: schemas.JobCreate, batch_id: int | None = None) -> Job:
    run_at, job_status = _resolve_run_at_and_status(body)

    retry_policy_id = queue.default_retry_policy_id
    max_attempts = body.max_attempts
    if body.retry_policy:
        rp = RetryPolicy(**body.retry_policy.model_dump())
        db.add(rp)
        await db.flush()
        retry_policy_id = rp.id
        max_attempts = max_attempts or rp.max_attempts
    if max_attempts is None:
        if retry_policy_id:
            rp = await db.get(RetryPolicy, retry_policy_id)
            max_attempts = rp.max_attempts if rp else 5
        else:
            max_attempts = 5

    job = Job(
        queue_id=queue.id,
        job_type=body.job_type,
        payload=body.payload,
        idempotency_key=body.idempotency_key,
        status=job_status,
        priority=body.priority,
        run_at=run_at,
        retry_policy_id=retry_policy_id,
        max_attempts=max_attempts,
        depends_on_job_id=body.depends_on_job_id,
        batch_id=batch_id,
    )
    db.add(job)
    return job


@router.post("/queues/{queue_id}/jobs", response_model=schemas.JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    queue_id: int,
    body: schemas.JobCreate,
    project: Project = Depends(require_project_role(OrgRole.MEMBER)),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    queue = await _get_queue(db, project.id, queue_id)
    job = await _materialize_job(db, queue, body)
    job.created_by = current_user.id
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key already used in this queue")
    await db.refresh(job)
    await manager.broadcast(project.id, {"event": "job.created", "job_id": job.id, "queue_id": queue.id, "status": job.status.value})
    return job


@router.post("/queues/{queue_id}/jobs/batch", response_model=list[schemas.JobOut], status_code=status.HTTP_201_CREATED)
async def create_batch(
    queue_id: int,
    body: schemas.BatchJobCreate,
    project: Project = Depends(require_project_role(OrgRole.MEMBER)),
    db: AsyncSession = Depends(get_db),
):
    queue = await _get_queue(db, project.id, queue_id)
    batch = Batch(queue_id=queue.id, name=body.name, total_jobs=len(body.jobs))
    db.add(batch)
    await db.flush()

    jobs = []
    for job_body in body.jobs:
        jobs.append(await _materialize_job(db, queue, job_body, batch_id=batch.id))

    await db.commit()
    for j in jobs:
        await db.refresh(j)
    await manager.broadcast(project.id, {"event": "batch.created", "batch_id": batch.id, "queue_id": queue.id, "count": len(jobs)})
    return jobs


@router.get("/queues/{queue_id}/jobs", response_model=schemas.Page[schemas.JobOut])
async def list_jobs(
    queue_id: int,
    project: Project = Depends(get_project_for_user),
    db: AsyncSession = Depends(get_db),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    job_type: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
):
    await _get_queue(db, project.id, queue_id)
    stmt = select(Job).where(Job.queue_id == queue_id)
    if job_status:
        stmt = stmt.where(Job.status == job_status)
    if job_type:
        stmt = stmt.where(Job.job_type == job_type)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(Job.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return schemas.Page(items=rows, total=total, page=page, page_size=page_size)


@router.get("/jobs/{job_id}", response_model=schemas.JobDetailOut)
async def get_job(job_id: int, project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    job = (
        await db.execute(
            select(Job).join(Queue).where(Job.id == job_id, Queue.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    await db.refresh(job, attribute_names=["executions", "logs"])
    return job


@router.post("/jobs/{job_id}/cancel", response_model=schemas.JobOut)
async def cancel_job(job_id: int, project: Project = Depends(require_project_role(OrgRole.MEMBER)), db: AsyncSession = Depends(get_db)):
    job = (
        await db.execute(select(Job).join(Queue).where(Job.id == job_id, Queue.project_id == project.id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status in JobStatus.terminal():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot cancel a job in terminal state '{job.status.value}'")
    job.status = JobStatus.CANCELLED
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    await manager.broadcast(project.id, {"event": "job.cancelled", "job_id": job.id})
    return job


@router.post("/jobs/{job_id}/retry", response_model=schemas.JobOut)
async def retry_job(job_id: int, project: Project = Depends(require_project_role(OrgRole.MEMBER)), db: AsyncSession = Depends(get_db)):
    """Manually re-queue a job stuck in FAILED or DEAD_LETTER (operator-triggered replay)."""
    job = (
        await db.execute(select(Job).join(Queue).where(Job.id == job_id, Queue.project_id == project.id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status not in (JobStatus.FAILED, JobStatus.DEAD_LETTER):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only FAILED or DEAD_LETTER jobs can be retried")

    job.status = JobStatus.QUEUED
    job.run_at = datetime.now(timezone.utc)
    job.claimed_by_worker_id = None
    job.claimed_at = None
    job.lease_expires_at = None
    job.completed_at = None

    if job.dlq_entry:
        job.dlq_entry.reprocessed = True
        job.dlq_entry.reprocessed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(job)
    await manager.broadcast(project.id, {"event": "job.retried", "job_id": job.id})
    return job


# ------------------------------------------------------------- scheduled --

@router.post("/queues/{queue_id}/scheduled-jobs", response_model=schemas.ScheduledJobOut, status_code=status.HTTP_201_CREATED)
async def create_scheduled_job(
    queue_id: int,
    body: schemas.ScheduledJobCreate,
    project: Project = Depends(require_project_role(OrgRole.MEMBER)),
    db: AsyncSession = Depends(get_db),
):
    await _get_queue(db, project.id, queue_id)
    if not croniter.is_valid(body.cron_expression):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid cron expression")

    retry_policy_id = None
    if body.retry_policy:
        rp = RetryPolicy(**body.retry_policy.model_dump())
        db.add(rp)
        await db.flush()
        retry_policy_id = rp.id

    now = datetime.now(timezone.utc)
    next_run = croniter(body.cron_expression, now).get_next(datetime)

    sched = ScheduledJob(
        queue_id=queue_id,
        name=body.name,
        job_type=body.job_type,
        payload_template=body.payload_template,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        max_attempts=body.max_attempts,
        retry_policy_id=retry_policy_id,
        next_run_at=next_run,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return sched


@router.get("/queues/{queue_id}/scheduled-jobs", response_model=list[schemas.ScheduledJobOut])
async def list_scheduled_jobs(queue_id: int, project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    await _get_queue(db, project.id, queue_id)
    rows = (await db.execute(select(ScheduledJob).where(ScheduledJob.queue_id == queue_id))).scalars().all()
    return rows


@router.post("/scheduled-jobs/{scheduled_job_id}/pause", response_model=schemas.ScheduledJobOut)
async def pause_scheduled_job(scheduled_job_id: int, project: Project = Depends(require_project_role(OrgRole.ADMIN)), db: AsyncSession = Depends(get_db)):
    sched = (
        await db.execute(
            select(ScheduledJob).join(Queue).where(ScheduledJob.id == scheduled_job_id, Queue.project_id == project.id)
        )
    ).scalar_one_or_none()
    if not sched:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled job not found")
    sched.is_active = False
    await db.commit()
    await db.refresh(sched)
    return sched


# ------------------------------------------------------------------ DLQ ---

@router.get("/dead-letter-queue", response_model=list[schemas.JobOut])
async def list_dlq(project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(Job)
            .join(Queue)
            .where(Queue.project_id == project.id, Job.status == JobStatus.DEAD_LETTER)
            .order_by(Job.updated_at.desc())
        )
    ).scalars().all()
    return rows
