"""
Relational schema.

Design notes (see docs/DESIGN_DECISIONS.md for the full write-up):

- All PKs are surrogate BIGINT identities. Natural keys (email, slug,
  idempotency_key) get separate UNIQUE constraints instead of being PKs, so
  they can change without touching every FK in the graph.
- `jobs` is the hot table. It is intentionally denormalized with a few
  fields that could technically be derived (attempt_count, status) because
  the claim query and dashboard list view both need them without a join.
  Everything else (per-attempt detail, logs) is normalized out into
  `job_executions` / `job_logs` so the hot table stays narrow and its index
  stays small.
- Scheduling definitions (`scheduled_jobs`) are separate from job instances
  (`jobs`). A cron schedule is a template; every time it fires it produces
  one row in `jobs`. This mirrors how Sidekiq-cron / Celery beat / Temporal
  schedules work and avoids overloading one table with two different
  lifecycles (a schedule lives forever, a job instance lives once).
- ON DELETE behavior: org -> project -> queue -> job cascades on delete
  (deleting a project should not orphan its jobs). job -> job_executions /
  job_logs cascades too. Deleting a *user* does NOT cascade into jobs they
  created (SET NULL on created_by) — audit history must survive account
  deletion.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey,
    Index, Integer, JSON, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ---------------------------------------------------------------- enums ---

class OrgRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    SCHEDULED = "scheduled"       # waiting for run_at in the future
    CLAIMED = "claimed"           # a worker has locked it, hasn't started executing
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"             # terminal per-attempt failure that will be retried
    DEAD_LETTER = "dead_letter"   # exhausted retries, terminal
    CANCELLED = "cancelled"

    @classmethod
    def terminal(cls) -> set["JobStatus"]:
        return {cls.COMPLETED, cls.DEAD_LETTER, cls.CANCELLED}


class RetryStrategy(str, enum.Enum):
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class WorkerStatus(str, enum.Enum):
    IDLE = "idle"
    BUSY = "busy"
    DRAINING = "draining"   # graceful shutdown in progress
    OFFLINE = "offline"


class ExecutionStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class LogLevel(str, enum.Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


def _enum(pytype, name):
    # native_enum=False -> stored as VARCHAR + CHECK constraint, portable
    # between Postgres and SQLite (the latter is used in the test suite).
    return Enum(pytype, name=name, native_enum=False, validate_strings=True)


# ---------------------------------------------------------------- users ---

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list["OrganizationMember"]] = relationship(back_populates="user")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["OrganizationMember"]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    projects: Mapped[list["Project"]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class OrganizationMember(Base):
    """RBAC join table: a user's role within one org."""
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_org_member"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[OrgRole] = mapped_column(_enum(OrgRole, "org_role"), default=OrgRole.MEMBER, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_project_slug_per_org"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    api_key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped["Organization"] = relationship(back_populates="projects")
    queues: Mapped[list["Queue"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    workers: Mapped[list["Worker"]] = relationship(back_populates="project", cascade="all, delete-orphan")


# --------------------------------------------------------- retry policy ---

class RetryPolicy(Base):
    """
    Reusable retry configuration. Attached to a Queue as a default and
    optionally overridden per-Job. Kept as its own table (rather than columns
    inlined on Queue/Job) so the same policy can be shared and so future
    strategies don't require a migration on two hot tables.
    """
    __tablename__ = "retry_policies"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy: Mapped[RetryStrategy] = mapped_column(_enum(RetryStrategy, "retry_strategy"), nullable=False)
    base_delay_seconds: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    max_delay_seconds: Mapped[float] = mapped_column(Float, default=3600.0, nullable=False)
    multiplier: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)  # used by EXPONENTIAL
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    jitter: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("max_attempts >= 0", name="ck_retry_max_attempts_nonneg"),)


# --------------------------------------------------------------- queues ---

class Queue(Base):
    __tablename__ = "queues"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_queue_name_per_project"),
        CheckConstraint("max_concurrency > 0", name="ck_queue_concurrency_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # higher = served first
    max_concurrency: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_retry_policy_id: Mapped[int | None] = mapped_column(ForeignKey("retry_policies.id", ondelete="SET NULL"), nullable=True)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = unlimited
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project: Mapped["Project"] = relationship(back_populates="queues")
    default_retry_policy: Mapped["RetryPolicy | None"] = relationship()
    jobs: Mapped[list["Job"]] = relationship(back_populates="queue", cascade="all, delete-orphan")
    scheduled_jobs: Mapped[list["ScheduledJob"]] = relationship(back_populates="queue", cascade="all, delete-orphan")


# ---------------------------------------------------------------- jobs ----

class Job(Base):
    """
    One instance of work. Both one-off (immediate/delayed/scheduled) jobs and
    materialized occurrences of a recurring/cron `ScheduledJob` live here.
    """
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("queue_id", "idempotency_key", name="uq_job_idempotency_per_queue"),
        # Composite index backing the atomic-claim query:
        #   WHERE queue_id = ? AND status = 'queued' AND run_at <= now()
        #   ORDER BY priority DESC, run_at ASC
        Index("ix_jobs_claim_scan", "queue_id", "status", "run_at"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_scheduled_job_id", "scheduled_job_id"),
        Index("ix_jobs_batch_id", "batch_id"),
        CheckConstraint("attempt_count >= 0", name="ck_job_attempt_nonneg"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=False)

    job_type: Mapped[str] = mapped_column(String(255), nullable=False)  # dispatch key, e.g. "send_email"
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), default=JobStatus.QUEUED, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)  # immediate = now()

    # --- retry config / state ---
    retry_policy_id: Mapped[int | None] = mapped_column(ForeignKey("retry_policies.id", ondelete="SET NULL"), nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- claim / lease state (drives atomic claiming + stale-lock reaping) ---
    claimed_by_worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # visibility timeout

    # --- provenance / grouping ---
    scheduled_job_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="SET NULL"), nullable=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="SET NULL"), nullable=True)
    depends_on_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queue: Mapped["Queue"] = relationship(back_populates="jobs")
    retry_policy: Mapped["RetryPolicy | None"] = relationship()
    executions: Mapped[list["JobExecution"]] = relationship(back_populates="job", cascade="all, delete-orphan", order_by="JobExecution.attempt_number")
    logs: Mapped[list["JobLog"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    dlq_entry: Mapped["DeadLetterEntry | None"] = relationship(back_populates="job", uselist=False, cascade="all, delete-orphan")


class ScheduledJob(Base):
    """
    A recurring (cron) or future one-shot schedule *definition*. The
    scheduler tick reads rows where next_run_at <= now() and is_active,
    materializes a `Job`, and advances next_run_at via croniter.
    """
    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_scheduled_jobs_due", "is_active", "next_run_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_type: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_template: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retry_policy_id: Mapped[int | None] = mapped_column(ForeignKey("retry_policies.id", ondelete="SET NULL"), nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    queue: Mapped["Queue"] = relationship(back_populates="scheduled_jobs")


class Batch(Base):
    """Groups jobs created together via the batch-submit endpoint."""
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    queue_id: Mapped[int] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=True)
    total_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobExecution(Base):
    """One row per attempt. `jobs.attempt_count` is the fast-path counter;
    this table is the normalized audit trail behind it."""
    __tablename__ = "job_executions"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_execution_attempt"),
        Index("ix_executions_worker", "worker_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workers.id", ondelete="SET NULL"), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ExecutionStatus] = mapped_column(_enum(ExecutionStatus, "execution_status"), default=ExecutionStatus.RUNNING, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_stacktrace: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="executions")


class JobLog(Base):
    """Structured, queryable log lines emitted during execution (distinct
    from `error_stacktrace`, which is a single terminal blob per attempt)."""
    __tablename__ = "job_logs"
    __table_args__ = (Index("ix_job_logs_job_created", "job_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    execution_id: Mapped[int | None] = mapped_column(ForeignKey("job_executions.id", ondelete="CASCADE"), nullable=True)
    level: Mapped[LogLevel] = mapped_column(_enum(LogLevel, "log_level"), default=LogLevel.INFO, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped["Job"] = relationship(back_populates="logs")


class DeadLetterEntry(Base):
    """Terminal home for jobs that exhausted retries. Kept as its own table
    (rather than just `status='dead_letter'` on Job) so DLQ listing/replay is
    a narrow, cheap scan and so we can snapshot the payload as it looked at
    time of death, independent of any later mutation of the job row."""
    __tablename__ = "dead_letter_queue"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    queue_id: Mapped[int] = mapped_column(ForeignKey("queues.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    moved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reprocessed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reprocessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["Job"] = relationship(back_populates="dlq_entry")


# -------------------------------------------------------------- workers ---

class Worker(Base):
    __tablename__ = "workers"
    __table_args__ = (Index("ix_workers_project_status", "project_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)  # human-friendly, e.g. "worker-1"
    status: Mapped[WorkerStatus] = mapped_column(_enum(WorkerStatus, "worker_status"), default=WorkerStatus.IDLE, nullable=False)
    concurrency_capacity: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    current_load: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    queues_subscribed: Mapped[dict] = mapped_column(JSON, default=list, nullable=False)  # list[str] of queue names
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="workers")


class WorkerHeartbeat(Base):
    """Time-series heartbeat log, separate from `workers.last_heartbeat_at`
    (which is just "latest") so the dashboard can chart load/health over
    time and so a slow drip of old rows can be pruned without touching the
    live worker row."""
    __tablename__ = "worker_heartbeats"
    __table_args__ = (Index("ix_heartbeats_worker_time", "worker_id", "heartbeat_at"),)

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id", ondelete="CASCADE"), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    active_jobs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cpu_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mem_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
