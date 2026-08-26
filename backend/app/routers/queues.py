from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_project_for_user, require_project_role
from app.database import get_db
from app.models import Job, JobStatus, OrgRole, Project, Queue, RetryPolicy, User
from app import schemas

router = APIRouter(prefix="/projects/{project_id}/queues", tags=["queues"])


@router.post("", response_model=schemas.QueueOut, status_code=status.HTTP_201_CREATED)
async def create_queue(
    body: schemas.QueueCreate,
    project: Project = Depends(require_project_role(OrgRole.MEMBER)),
    db: AsyncSession = Depends(get_db),
):
    retry_policy_id = None
    if body.retry_policy:
        rp = RetryPolicy(**body.retry_policy.model_dump())
        db.add(rp)
        await db.flush()
        retry_policy_id = rp.id

    queue = Queue(
        project_id=project.id,
        name=body.name,
        priority=body.priority,
        max_concurrency=body.max_concurrency,
        default_retry_policy_id=retry_policy_id,
        rate_limit_per_minute=body.rate_limit_per_minute,
    )
    db.add(queue)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Queue name already exists in this project")
    await db.refresh(queue)
    return queue


@router.get("", response_model=list[schemas.QueueOut])
async def list_queues(project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Queue).where(Queue.project_id == project.id))).scalars().all()
    return rows


@router.patch("/{queue_id}", response_model=schemas.QueueOut)
async def update_queue(
    queue_id: int,
    body: schemas.QueueUpdate,
    project: Project = Depends(require_project_role(OrgRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    queue = (await db.execute(select(Queue).where(Queue.id == queue_id, Queue.project_id == project.id))).scalar_one_or_none()
    if not queue:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Queue not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(queue, field, value)
    await db.commit()
    await db.refresh(queue)
    return queue


@router.post("/{queue_id}/pause", response_model=schemas.QueueOut)
async def pause_queue(queue_id: int, project: Project = Depends(require_project_role(OrgRole.ADMIN)), db: AsyncSession = Depends(get_db)):
    queue = (await db.execute(select(Queue).where(Queue.id == queue_id, Queue.project_id == project.id))).scalar_one_or_none()
    if not queue:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Queue not found")
    queue.is_paused = True
    await db.commit()
    await db.refresh(queue)
    return queue


@router.post("/{queue_id}/resume", response_model=schemas.QueueOut)
async def resume_queue(queue_id: int, project: Project = Depends(require_project_role(OrgRole.ADMIN)), db: AsyncSession = Depends(get_db)):
    queue = (await db.execute(select(Queue).where(Queue.id == queue_id, Queue.project_id == project.id))).scalar_one_or_none()
    if not queue:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Queue not found")
    queue.is_paused = False
    await db.commit()
    await db.refresh(queue)
    return queue


@router.get("/{queue_id}/stats", response_model=schemas.QueueStats)
async def queue_stats(queue_id: int, project: Project = Depends(get_project_for_user), db: AsyncSession = Depends(get_db)):
    queue = (await db.execute(select(Queue).where(Queue.id == queue_id, Queue.project_id == project.id))).scalar_one_or_none()
    if not queue:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Queue not found")

    counts_rows = (
        await db.execute(
            select(Job.status, func.count(Job.id)).where(Job.queue_id == queue_id).group_by(Job.status)
        )
    ).all()
    counts = {status_: 0 for status_ in JobStatus}
    for status_val, cnt in counts_rows:
        counts[JobStatus(status_val)] = cnt

    since = datetime.now(timezone.utc) - timedelta(hours=1)
    throughput = (
        await db.execute(
            select(func.count(Job.id)).where(
                Job.queue_id == queue_id, Job.status == JobStatus.COMPLETED, Job.completed_at >= since
            )
        )
    ).scalar_one()

    in_flight = counts[JobStatus.CLAIMED] + counts[JobStatus.RUNNING]

    return schemas.QueueStats(
        queue_id=queue.id,
        name=queue.name,
        queued=counts[JobStatus.QUEUED],
        scheduled=counts[JobStatus.SCHEDULED],
        claimed=counts[JobStatus.CLAIMED],
        running=counts[JobStatus.RUNNING],
        completed=counts[JobStatus.COMPLETED],
        failed=counts[JobStatus.FAILED],
        dead_letter=counts[JobStatus.DEAD_LETTER],
        is_paused=queue.is_paused,
        max_concurrency=queue.max_concurrency,
        current_in_flight=in_flight,
        throughput_last_hour=throughput,
    )
