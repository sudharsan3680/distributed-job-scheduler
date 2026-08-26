"""
Runs as an in-process asyncio background task (started from main.py's
lifespan). Three responsibilities, each idempotent and safe to run
concurrently with itself across API replicas because every write is a
conditional UPDATE ... WHERE, not a blind write:

1. promote_scheduled_jobs  - SCHEDULED -> QUEUED once run_at has arrived.
2. materialize_cron_jobs   - fires due ScheduledJob templates, creates a Job
                              instance, advances next_run_at via croniter.
3. reap_stale_leases       - a worker that died mid-job leaves rows stuck in
                              CLAIMED/RUNNING forever unless something
                              notices the lease expired. This is that
                              something: it requeues (or dead-letters, if
                              attempts are exhausted) any job whose
                              lease_expires_at is in the past.

Honest limitation: running this loop in every API replica means the queries
below execute redundantly N times. They're cheap, indexed, and
idempotent, so it's correct, just not maximally efficient -- a production
system would elect a single leader (e.g. Postgres advisory lock) to run
this loop. Noted in DESIGN_DECISIONS.md rather than glossed over.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from croniter import croniter
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import DeadLetterEntry, ExecutionStatus, Job, JobExecution, JobStatus, RetryPolicy, ScheduledJob
from app.services.retry import compute_backoff_seconds

logger = logging.getLogger("scheduler")


def _as_aware_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on timestamp round-trip; normalize so duration math
    is identical across backends."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def promote_scheduled_jobs():
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(Job).where(Job.status == JobStatus.SCHEDULED, Job.run_at <= now))
        ).scalars().all()
        for job in rows:
            job.status = JobStatus.QUEUED
        if rows:
            await db.commit()
            logger.info("promoted %d scheduled job(s) to queued", len(rows))


async def materialize_cron_jobs():
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        due = (
            await db.execute(
                select(ScheduledJob).where(ScheduledJob.is_active.is_(True), ScheduledJob.next_run_at <= now)
            )
        ).scalars().all()
        for sched in due:
            db.add(Job(
                queue_id=sched.queue_id,
                job_type=sched.job_type,
                payload=sched.payload_template,
                status=JobStatus.QUEUED,
                run_at=now,
                retry_policy_id=sched.retry_policy_id,
                max_attempts=sched.max_attempts,
                scheduled_job_id=sched.id,
            ))
            sched.last_run_at = now
            sched.next_run_at = croniter(sched.cron_expression, now).get_next(datetime)
        if due:
            await db.commit()
            logger.info("materialized %d cron job instance(s)", len(due))


async def reap_stale_leases():
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        stale = (
            await db.execute(
                select(Job).where(
                    Job.status.in_([JobStatus.CLAIMED, JobStatus.RUNNING]),
                    Job.lease_expires_at.isnot(None),
                    Job.lease_expires_at < now,
                )
            )
        ).scalars().all()
        for job in stale:
            # Close out the dangling attempt row so the execution history is
            # accurate instead of being stuck in RUNNING forever.
            open_exec = (
                await db.execute(
                    select(JobExecution).where(
                        JobExecution.job_id == job.id, JobExecution.status == ExecutionStatus.RUNNING
                    ).order_by(JobExecution.attempt_number.desc())
                )
            ).scalar_one_or_none()
            if open_exec:
                open_exec.status = ExecutionStatus.TIMED_OUT
                open_exec.finished_at = now
                open_exec.duration_ms = int((now - _as_aware_utc(open_exec.started_at)).total_seconds() * 1000)
                open_exec.error_message = "Lease expired: worker stopped heartbeating before reporting a result."

            if job.attempt_count >= job.max_attempts:
                job.status = JobStatus.DEAD_LETTER
                job.completed_at = now
                db.add(DeadLetterEntry(
                    job_id=job.id, queue_id=job.queue_id, reason="worker_lease_expired_no_retries_left",
                    last_error="Worker heartbeat/lease expired without reporting a result.",
                    payload_snapshot=job.payload,
                ))
            else:
                policy = await db.get(RetryPolicy, job.retry_policy_id) if job.retry_policy_id else None
                delay = compute_backoff_seconds(policy, job.attempt_count) if policy else 5.0
                job.status = JobStatus.QUEUED
                job.run_at = now + timedelta(seconds=delay)
            job.claimed_by_worker_id = None
            job.lease_expires_at = None
        if stale:
            await db.commit()
            logger.warning("reaped %d stale-lease job(s) (worker likely crashed)", len(stale))


async def scheduler_loop(stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            await promote_scheduled_jobs()
            await materialize_cron_jobs()
            await reap_stale_leases()
        except Exception:
            logger.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.scheduler_tick_seconds)
        except asyncio.TimeoutError:
            pass
