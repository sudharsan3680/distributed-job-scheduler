import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.core.deps import get_project_by_api_key
from app.database import get_db
from app.models import (
    DeadLetterEntry, ExecutionStatus, Job, JobExecution, JobLog, JobStatus, LogLevel,
    Project, Queue, RetryPolicy, Worker, WorkerHeartbeat, WorkerStatus,
)
from app import schemas
from app.services.retry import compute_backoff_seconds
from app.websocket.manager import manager

router = APIRouter(prefix="/projects/{project_id}", tags=["workers"])

# In-process per-queue lock guarding the claim critical section. This is
# belt-and-suspenders: `SELECT ... FOR UPDATE SKIP LOCKED` is what makes
# claiming safe across multiple API processes on Postgres, but two
# coroutines *within the same process* can still interleave at an `await`
# before either has committed (and on SQLite, used in tests, SKIP LOCKED
# compiles to nothing at all -- SQLite has no row-level locking). Taking
# this lock for the read-modify-commit span closes that gap for the
# single-process case without changing the multi-process story, which
# still depends on the DB-level lock.
_queue_claim_locks: dict[int, asyncio.Lock] = {}


def _lock_for(queue_id: int) -> asyncio.Lock:
    lock = _queue_claim_locks.get(queue_id)
    if lock is None:
        lock = asyncio.Lock()
        _queue_claim_locks[queue_id] = lock
    return lock


def _as_aware_utc(dt: datetime) -> datetime:
    """SQLite (used only by the test suite) drops tzinfo on round-trip since
    it has no native timestamp type; Postgres preserves it. Normalize here
    so duration math works identically against either backend."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/workers/register", response_model=schemas.WorkerOut, status_code=status.HTTP_201_CREATED)
async def register_worker(body: schemas.WorkerRegister, project: Project = Depends(get_project_by_api_key), db: AsyncSession = Depends(get_db)):
    worker = Worker(
        project_id=project.id,
        hostname=body.hostname,
        pid=body.pid,
        label=body.label,
        concurrency_capacity=body.concurrency_capacity,
        queues_subscribed=body.queues,
        status=WorkerStatus.IDLE,
        last_heartbeat_at=datetime.now(timezone.utc),
    )
    db.add(worker)
    await db.commit()
    await db.refresh(worker)
    await manager.broadcast(project.id, {"event": "worker.registered", "worker_id": worker.id, "label": worker.label})
    return worker


@router.post("/workers/{worker_id}/heartbeat", response_model=schemas.WorkerOut)
async def heartbeat(worker_id: int, body: schemas.WorkerHeartbeatIn, project: Project = Depends(get_project_by_api_key), db: AsyncSession = Depends(get_db)):
    worker = (await db.execute(select(Worker).where(Worker.id == worker_id, Worker.project_id == project.id))).scalar_one_or_none()
    if not worker:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    now = datetime.now(timezone.utc)
    worker.last_heartbeat_at = now
    worker.current_load = body.active_jobs
    worker.status = WorkerStatus.BUSY if body.active_jobs > 0 else WorkerStatus.IDLE

    # LEASE RENEWAL: a heartbeat is proof the worker is alive, so extend the
    # visibility timeout on every job this worker currently holds. Without
    # this, a job that runs longer than `job_visibility_timeout_seconds`
    # would have its lease expire and be reaped by the scheduler loop, then
    # potentially claimed + executed a *second* time by another worker. The
    # worker's 5s heartbeat cadence keeps leases fresh for arbitrarily long
    # jobs as long as the process is healthy.
    await db.execute(
        update(Job)
        .where(
            Job.claimed_by_worker_id == worker.id,
            Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]),
        )
        .values(lease_expires_at=now + timedelta(seconds=settings.job_visibility_timeout_seconds))
    )

    db.add(WorkerHeartbeat(worker_id=worker.id, active_jobs=body.active_jobs, cpu_pct=body.cpu_pct, mem_mb=body.mem_mb))
    await db.commit()
    await db.refresh(worker)
    return worker


@router.post("/workers/{worker_id}/drain", response_model=schemas.WorkerOut)
async def drain_worker(worker_id: int, project: Project = Depends(get_project_by_api_key), db: AsyncSession = Depends(get_db)):
    """Mark a worker as draining -- it should finish in-flight jobs and stop claiming new ones (graceful shutdown)."""
    worker = (await db.execute(select(Worker).where(Worker.id == worker_id, Worker.project_id == project.id))).scalar_one_or_none()
    if not worker:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    worker.status = WorkerStatus.DRAINING
    await db.commit()
    await db.refresh(worker)
    return worker


@router.post("/workers/{worker_id}/deregister", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_worker(worker_id: int, project: Project = Depends(get_project_by_api_key), db: AsyncSession = Depends(get_db)):
    worker = (await db.execute(select(Worker).where(Worker.id == worker_id, Worker.project_id == project.id))).scalar_one_or_none()
    if not worker:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    worker.status = WorkerStatus.OFFLINE
    worker.stopped_at = datetime.now(timezone.utc)
    await db.commit()
    await manager.broadcast(project.id, {"event": "worker.offline", "worker_id": worker.id})


@router.get("/workers", response_model=list[schemas.WorkerOut])
async def list_workers(project: Project = Depends(get_project_by_api_key), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Worker).where(Worker.project_id == project.id).order_by(Worker.started_at.desc()))).scalars().all()
    return rows


# --------------------------------------------------------- atomic claim ---

@router.post("/workers/{worker_id}/claim", response_model=list[schemas.JobOut])
async def claim_jobs(
    worker_id: int,
    body: schemas.ClaimRequest,
    project: Project = Depends(get_project_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Atomically claim up to `max_jobs` runnable jobs across the requested
    queues. Concurrency safety comes from `SELECT ... FOR UPDATE SKIP
    LOCKED`: two workers racing this endpoint will never lock the same row
    -- the loser simply skips it and sees the next candidate, so no
    application-level mutex or distributed lock is needed for correctness.
    (On SQLite, used only in the test-suite, FOR UPDATE is a no-op -- fine,
    since the test client is single-connection anyway.)
    """
    worker = (await db.execute(select(Worker).where(Worker.id == worker_id, Worker.project_id == project.id))).scalar_one_or_none()
    if not worker:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Worker not found")
    if worker.status == WorkerStatus.DRAINING or worker.status == WorkerStatus.OFFLINE:
        return []

    now = datetime.now(timezone.utc)
    claimed: list[Job] = []
    remaining = body.max_jobs

    queues = (
        await db.execute(
            select(Queue).where(
                Queue.project_id == project.id, Queue.name.in_(body.queue_names), Queue.is_paused.is_(False)
            )
        )
    ).scalars().all()

    # Promote any jobs that failed on a previous attempt and whose backoff
    # window has now elapsed (status FAILED -> QUEUED, respecting run_at).
    # Doing this inside the claim path (rather than only in the scheduler
    # loop) means the retry actually fires the moment a worker polls, and it
    # keeps the FAILED state real/observable in the dashboard between attempts
    # instead of being a no-op status nobody ever sets.
    if queues:
        queue_ids = [q.id for q in queues]
        await db.execute(
            update(Job)
            .where(Job.queue_id.in_(queue_ids), Job.status == JobStatus.FAILED, Job.next_retry_at <= now)
            .values(status=JobStatus.QUEUED)
        )
        await db.commit()

    for queue in sorted(queues, key=lambda q: -q.priority):
        if remaining <= 0:
            break

        async with _lock_for(queue.id):
            in_flight = (
                await db.execute(
                    select(Job.id).where(Job.queue_id == queue.id, Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]))
                )
            ).all()
            slots = queue.max_concurrency - len(in_flight)
            if slots <= 0:
                continue

            if queue.rate_limit_per_minute:
                window_start = now - timedelta(minutes=1)
                recent = (
                    await db.execute(
                        select(Job.id).where(Job.queue_id == queue.id, Job.claimed_at.isnot(None), Job.claimed_at >= window_start)
                    )
                ).all()
                slots = min(slots, max(queue.rate_limit_per_minute - len(recent), 0))
                if slots <= 0:
                    continue

            take = min(slots, remaining)

            # Workflow-dependency gate: a job with depends_on_job_id only becomes
            # claimable once that upstream job has COMPLETED. Self-referencing FK
            # needs an alias so SQLAlchemy treats it as a separate table in the join.
            DepJob = aliased(Job)
            dep_ready = or_(
                Job.depends_on_job_id.is_(None),
                exists().where(and_(DepJob.id == Job.depends_on_job_id, DepJob.status == JobStatus.COMPLETED)),
            )

            stmt = (
                select(Job)
                .where(Job.queue_id == queue.id, Job.status == JobStatus.QUEUED, Job.run_at <= now, dep_ready)
                .order_by(Job.priority.desc(), Job.run_at.asc())
                .limit(take)
                .with_for_update(skip_locked=True)
            )
            candidates = (await db.execute(stmt)).scalars().all()

            for job in candidates:
                job.status = JobStatus.CLAIMED
                job.claimed_by_worker_id = worker.id
                job.claimed_at = now
                job.lease_expires_at = now + timedelta(seconds=settings.job_visibility_timeout_seconds)
                claimed.append(job)
                remaining -= 1

            # Commit while still holding this queue's lock. Committing here
            # (rather than once at the end, after every queue's lock has
            # already been released) is what actually closes the race: it
            # guarantees the next request queued on this same lock sees the
            # updated `status` before it runs its own SELECT, regardless of
            # backend. Committing only at the end would let two requests
            # each read-then-write between the other's flush and commit.
            if candidates:
                await db.commit()
                for job in candidates:
                    await db.refresh(job)

    if claimed:
        worker.status = WorkerStatus.BUSY
        worker.current_load = len(claimed)
        await db.commit()
        await manager.broadcast(project.id, {"event": "jobs.claimed", "worker_id": worker.id, "job_ids": [j.id for j in claimed]})

    return claimed


@router.post("/jobs/{job_id}/start", response_model=schemas.JobOut)
async def start_job(job_id: int, worker_id: int, project: Project = Depends(get_project_by_api_key), db: AsyncSession = Depends(get_db)):
    """Worker calls this immediately before executing a claimed job. Transitions CLAIMED -> RUNNING and opens the JobExecution row for this attempt."""
    job = (await db.execute(select(Job).join(Queue).where(Job.id == job_id, Queue.project_id == project.id))).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status != JobStatus.CLAIMED or job.claimed_by_worker_id != worker_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not CLAIMED by this worker")

    job.status = JobStatus.RUNNING
    job.attempt_count += 1
    now = datetime.now(timezone.utc)
    job.lease_expires_at = now + timedelta(seconds=settings.job_visibility_timeout_seconds)

    db.add(JobExecution(job_id=job.id, worker_id=worker_id, attempt_number=job.attempt_count, status=ExecutionStatus.RUNNING, started_at=now))
    db.add(JobLog(job_id=job.id, execution_id=None, level=LogLevel.INFO, message=f"Attempt {job.attempt_count} started by worker {worker_id}"))
    await db.commit()
    await db.refresh(job)
    await manager.broadcast(project.id, {"event": "job.running", "job_id": job.id, "worker_id": worker_id})
    return job


@router.post("/jobs/{job_id}/result", response_model=schemas.JobOut)
async def report_result(
    job_id: int,
    worker_id: int,
    body: schemas.JobResultIn,
    project: Project = Depends(get_project_by_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Worker reports the outcome of the current attempt. On failure, either
    schedules a backoff retry (status -> QUEUED with a future run_at) or,
    once max_attempts is exhausted, moves the job to the Dead Letter Queue.
    """
    job = (await db.execute(select(Job).join(Queue).where(Job.id == job_id, Queue.project_id == project.id))).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    if job.status != JobStatus.RUNNING or job.claimed_by_worker_id != worker_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not RUNNING under this worker")

    now = datetime.now(timezone.utc)
    execution = (
        await db.execute(
            select(JobExecution).where(JobExecution.job_id == job.id, JobExecution.attempt_number == job.attempt_count)
        )
    ).scalar_one_or_none()

    if execution:
        execution.finished_at = now
        execution.duration_ms = int((now - _as_aware_utc(execution.started_at)).total_seconds() * 1000)
        execution.result = body.result
        execution.error_message = body.error_message
        execution.error_stacktrace = body.error_stacktrace
        execution.status = ExecutionStatus.SUCCEEDED if body.success else ExecutionStatus.FAILED

    if body.success:
        job.status = JobStatus.COMPLETED
        job.completed_at = now
        job.claimed_by_worker_id = None
        job.lease_expires_at = None
        db.add(JobLog(job_id=job.id, level=LogLevel.INFO, message=f"Attempt {job.attempt_count} succeeded"))
        event = "job.completed"
    else:
        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.DEAD_LETTER
            job.completed_at = now
            job.claimed_by_worker_id = None
            job.lease_expires_at = None
            db.add(JobLog(job_id=job.id, level=LogLevel.ERROR, message=f"Attempt {job.attempt_count} failed; max attempts exhausted: {body.error_message}"))
            db.add(DeadLetterEntry(
                job_id=job.id, queue_id=job.queue_id, reason="max_attempts_exhausted",
                last_error=body.error_message, payload_snapshot=job.payload,
            ))
            event = "job.dead_letter"
        else:
            policy = await db.get(RetryPolicy, job.retry_policy_id) if job.retry_policy_id else None
            if policy:
                delay = compute_backoff_seconds(policy, job.attempt_count)
            else:
                delay = min(5 * (2 ** (job.attempt_count - 1)), 3600)  # sane default: exponential, cap 1h
            # Real FAILED state: the job sits in FAILED (visible in the
            # dashboard / job explorer as "failed") until its backoff window
            # elapses, at which point the claim path promotes it back to
            # QUEUED. This makes JobStatus.FAILED a first-class, observable
            # part of the lifecycle instead of a value nothing ever sets.
            job.status = JobStatus.FAILED
            job.run_at = now + timedelta(seconds=delay)
            job.next_retry_at = job.run_at
            job.claimed_by_worker_id = None
            job.lease_expires_at = None
            db.add(JobLog(job_id=job.id, level=LogLevel.ERROR, message=f"Attempt {job.attempt_count} failed; retry scheduled in {delay:.1f}s: {body.error_message}"))
            event = "job.retry_scheduled"

    await db.commit()
    await db.refresh(job)
    await manager.broadcast(project.id, {"event": event, "job_id": job.id})
    return job
