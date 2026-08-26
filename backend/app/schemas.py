from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import JobStatus, OrgRole, RetryStrategy, WorkerStatus

T = TypeVar("T")


# ------------------------------------------------------------------ auth --

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str
    organization_name: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    is_active: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# -------------------------------------------------------------- projects --

class ProjectCreate(BaseModel):
    name: str
    slug: str = Field(pattern=r"^[a-z0-9-]+$")


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    created_at: datetime


class ProjectWithApiKey(ProjectOut):
    api_key: str  # shown once, at creation time only


# ---------------------------------------------------------- retry policy --

class RetryPolicyCreate(BaseModel):
    name: str
    strategy: RetryStrategy
    base_delay_seconds: float = 5.0
    max_delay_seconds: float = 3600.0
    multiplier: float = 2.0
    max_attempts: int = 5
    jitter: bool = True


class RetryPolicyOut(RetryPolicyCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ------------------------------------------------------------------ queue --

class QueueCreate(BaseModel):
    name: str
    priority: int = 0
    max_concurrency: int = Field(default=5, gt=0)
    retry_policy: RetryPolicyCreate | None = None
    rate_limit_per_minute: int | None = None


class QueueUpdate(BaseModel):
    priority: int | None = None
    max_concurrency: int | None = Field(default=None, gt=0)
    is_paused: bool | None = None
    rate_limit_per_minute: int | None = None


class QueueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    priority: int
    max_concurrency: int
    is_paused: bool
    rate_limit_per_minute: int | None
    created_at: datetime
    updated_at: datetime


class QueueStats(BaseModel):
    queue_id: int
    name: str
    queued: int
    scheduled: int
    claimed: int
    running: int
    completed: int
    failed: int
    dead_letter: int
    is_paused: bool
    max_concurrency: int
    current_in_flight: int
    throughput_last_hour: int


# ------------------------------------------------------------------- job --

class JobCreate(BaseModel):
    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    idempotency_key: str | None = None
    run_at: datetime | None = None          # None => immediate
    delay_seconds: float | None = None       # convenience alt to run_at
    max_attempts: int | None = None          # overrides queue's retry policy
    retry_policy: RetryPolicyCreate | None = None  # per-job override
    depends_on_job_id: int | None = None


class BatchJobCreate(BaseModel):
    name: str | None = None
    jobs: list[JobCreate] = Field(min_length=1, max_length=1000)


class ScheduledJobCreate(BaseModel):
    name: str
    job_type: str
    payload_template: dict[str, Any] = Field(default_factory=dict)
    cron_expression: str
    timezone: str = "UTC"
    max_attempts: int = 5
    retry_policy: RetryPolicyCreate | None = None


class ScheduledJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    job_type: str
    cron_expression: str
    timezone: str
    is_active: bool
    next_run_at: datetime | None
    last_run_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    queue_id: int
    job_type: str
    payload: dict[str, Any]
    status: JobStatus
    priority: int
    run_at: datetime
    attempt_count: int
    max_attempts: int
    claimed_by_worker_id: int | None
    scheduled_job_id: int | None
    batch_id: int | None
    depends_on_job_id: int | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class JobDetailOut(JobOut):
    executions: list["JobExecutionOut"] = []
    logs: list["JobLogOut"] = []


class JobExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    attempt_number: int
    status: str
    worker_id: int | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_message: str | None


class JobLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    level: str
    message: str
    created_at: datetime


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------- worker ---

class WorkerRegister(BaseModel):
    hostname: str
    pid: int
    label: str
    concurrency_capacity: int = 4
    queues: list[str] = Field(default_factory=list)


class WorkerHeartbeatIn(BaseModel):
    active_jobs: int
    cpu_pct: float | None = None
    mem_mb: float | None = None


class WorkerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    hostname: str
    label: str
    status: WorkerStatus
    concurrency_capacity: int
    current_load: int
    last_heartbeat_at: datetime | None
    started_at: datetime


class ClaimRequest(BaseModel):
    queue_names: list[str]
    max_jobs: int = Field(default=1, ge=1, le=50)


class JobResultIn(BaseModel):
    success: bool
    result: dict[str, Any] | None = None
    error_message: str | None = None
    error_stacktrace: str | None = None


# ------------------------------------------------------------- dashboard --

class SystemHealthOut(BaseModel):
    total_queues: int
    total_workers_online: int
    jobs_queued: int
    jobs_running: int
    jobs_completed_last_hour: int
    jobs_failed_last_hour: int
    dead_letter_count: int
