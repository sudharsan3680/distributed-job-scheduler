"""Coverage for the background scheduler loop (promote / reap), which the
default test harness doesn't exercise because FastAPI's lifespan -- and thus
the scheduler -- isn't started under ASGITransport. We point the scheduler's
sessionmaker at the test DB and call the loop functions directly."""
import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ExecutionStatus, Job, JobExecution, JobStatus, Queue
from app.services import scheduler

pytestmark = pytest.mark.asyncio


async def _make_queue(db, name="q"):
    q = Queue(project_id=1, name=name, max_concurrency=2)
    db.add(q)
    await db.flush()
    return q


async def test_promote_scheduled_jobs(db_session, auth_headers, project):
    """A SCHEDULED job whose run_at has passed is promoted to QUEUED."""
    from datetime import datetime, timedelta, timezone

    async with db_session() as db:
        q = await _make_queue(db)
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.add(Job(queue_id=q.id, job_type="noop", status=JobStatus.SCHEDULED, run_at=past))
        await db.commit()

    scheduler.AsyncSessionLocal = db_session
    await scheduler.promote_scheduled_jobs()
    scheduler.AsyncSessionLocal = AsyncSessionLocal

    async with db_session() as db:
        job = (await db.execute(select(Job))).scalar_one()
        assert job.status == JobStatus.QUEUED


async def test_reap_stale_lease_requeues_and_marks_timed_out(db_session, auth_headers, project):
    """A RUNNING job whose lease expired (worker died) is requeued and its
    dangling execution row is closed as TIMED_OUT -- not left RUNNING."""
    from datetime import datetime, timedelta, timezone

    async with db_session() as db:
        q = await _make_queue(db)
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        job = Job(queue_id=q.id, job_type="noop", status=JobStatus.RUNNING, run_at=datetime.now(timezone.utc),
                  attempt_count=1, max_attempts=5, claimed_by_worker_id=1, lease_expires_at=old)
        db.add(job)
        await db.flush()
        db.add(JobExecution(job_id=job.id, worker_id=1, attempt_number=1, status=ExecutionStatus.RUNNING,
                           started_at=datetime.now(timezone.utc) - timedelta(minutes=6)))
        await db.commit()

    scheduler.AsyncSessionLocal = db_session
    await scheduler.reap_stale_leases()
    scheduler.AsyncSessionLocal = AsyncSessionLocal

    async with db_session() as db:
        job = (await db.execute(select(Job))).scalar_one()
        exec_row = (await db.execute(select(JobExecution))).scalar_one()
        assert job.status == JobStatus.QUEUED
        assert job.claimed_by_worker_id is None
        assert exec_row.status == ExecutionStatus.TIMED_OUT


async def test_reap_stale_lease_with_no_retries_left_dead_letters(db_session, auth_headers, project):
    from datetime import datetime, timedelta, timezone

    async with db_session() as db:
        q = await _make_queue(db)
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        job = Job(queue_id=q.id, job_type="fail_always", status=JobStatus.RUNNING, run_at=datetime.now(timezone.utc),
                  attempt_count=5, max_attempts=5, claimed_by_worker_id=1, lease_expires_at=old)
        db.add(job)
        await db.flush()
        db.add(JobExecution(job_id=job.id, worker_id=1, attempt_number=5, status=ExecutionStatus.RUNNING,
                           started_at=datetime.now(timezone.utc) - timedelta(minutes=6)))
        await db.commit()

    scheduler.AsyncSessionLocal = db_session
    await scheduler.reap_stale_leases()
    scheduler.AsyncSessionLocal = AsyncSessionLocal

    async with db_session() as db:
        job = (await db.execute(select(Job))).scalar_one()
        assert job.status == JobStatus.DEAD_LETTER
