# Entity-Relationship Diagram

```mermaid
erDiagram
    USERS ||--o{ ORGANIZATION_MEMBERS : "has"
    ORGANIZATIONS ||--o{ ORGANIZATION_MEMBERS : "has"
    ORGANIZATIONS ||--o{ PROJECTS : "owns"
    PROJECTS ||--o{ QUEUES : "owns"
    PROJECTS ||--o{ WORKERS : "runs"
    QUEUES ||--o{ JOBS : "contains"
    QUEUES ||--o{ SCHEDULED_JOBS : "contains"
    QUEUES ||--o{ BATCHES : "contains"
    RETRY_POLICIES ||--o{ QUEUES : "default for"
    RETRY_POLICIES ||--o{ JOBS : "overrides for"
    RETRY_POLICIES ||--o{ SCHEDULED_JOBS : "used by"
    SCHEDULED_JOBS ||--o{ JOBS : "materializes"
    BATCHES ||--o{ JOBS : "groups"
    JOBS ||--o{ JOB_EXECUTIONS : "attempts"
    JOBS ||--o{ JOB_LOGS : "logs"
    JOBS ||--o| DEAD_LETTER_QUEUE : "terminal snapshot"
    JOBS }o--o| JOBS : "depends_on"
    WORKERS ||--o{ JOB_EXECUTIONS : "executes"
    WORKERS ||--o{ WORKER_HEARTBEATS : "reports"
    WORKERS ||--o{ JOBS : "currently claims"

    USERS {
        bigint id PK
        string email UK
        string hashed_password
        string full_name
        bool is_active
    }
    ORGANIZATIONS {
        bigint id PK
        string name
        string slug UK
    }
    ORGANIZATION_MEMBERS {
        bigint id PK
        bigint organization_id FK
        bigint user_id FK
        enum role "owner/admin/member/viewer"
    }
    PROJECTS {
        bigint id PK
        bigint organization_id FK
        string name
        string slug
        string api_key_hash UK
    }
    RETRY_POLICIES {
        bigint id PK
        string name
        enum strategy "fixed/linear/exponential"
        float base_delay_seconds
        float max_delay_seconds
        float multiplier
        int max_attempts
        bool jitter
    }
    QUEUES {
        bigint id PK
        bigint project_id FK
        string name
        int priority
        int max_concurrency
        bool is_paused
        bigint default_retry_policy_id FK
        int rate_limit_per_minute "nullable"
    }
    SCHEDULED_JOBS {
        bigint id PK
        bigint queue_id FK
        string name
        string job_type
        json payload_template
        string cron_expression
        string timezone
        bool is_active
        bigint retry_policy_id FK
        datetime next_run_at
        datetime last_run_at
    }
    BATCHES {
        bigint id PK
        bigint queue_id FK
        string name
        int total_jobs
    }
    JOBS {
        bigint id PK
        bigint queue_id FK
        string job_type
        json payload
        string idempotency_key "nullable, unique per queue"
        enum status "queued/scheduled/claimed/running/completed/failed/dead_letter/cancelled"
        int priority
        datetime run_at
        bigint retry_policy_id FK
        int max_attempts
        int attempt_count
        bigint claimed_by_worker_id FK
        datetime claimed_at
        datetime lease_expires_at
        bigint scheduled_job_id FK "nullable"
        bigint batch_id FK "nullable"
        bigint depends_on_job_id FK "nullable, self-referencing"
        bigint created_by FK "nullable"
    }
    JOB_EXECUTIONS {
        bigint id PK
        bigint job_id FK
        bigint worker_id FK "nullable"
        int attempt_number
        enum status "running/succeeded/failed/timed_out"
        datetime started_at
        datetime finished_at
        int duration_ms
        text error_message
        text error_stacktrace
        json result
    }
    JOB_LOGS {
        bigint id PK
        bigint job_id FK
        bigint execution_id FK "nullable"
        enum level
        text message
    }
    DEAD_LETTER_QUEUE {
        bigint id PK
        bigint job_id FK UK
        bigint queue_id FK
        string reason
        text last_error
        json payload_snapshot
        bool reprocessed
    }
    WORKERS {
        bigint id PK
        bigint project_id FK
        string hostname
        int pid
        string label
        enum status "idle/busy/draining/offline"
        int concurrency_capacity
        int current_load
        json queues_subscribed
        datetime last_heartbeat_at
    }
    WORKER_HEARTBEATS {
        bigint id PK
        bigint worker_id FK
        datetime heartbeat_at
        int active_jobs
        float cpu_pct
        float mem_mb
    }
```

## Design rationale

**Surrogate BIGINT PKs everywhere.** Natural keys (`email`, `slug`,
`idempotency_key`) get their own `UNIQUE` constraints instead of being
primary keys — they can change (or, for `idempotency_key`, be absent) without
cascading FK churn.

**`jobs` is deliberately the widest table.** It carries both "queue"
concerns (status, run_at, priority) and "lease" concerns (claimed_by,
lease_expires_at) inline, because the one query that matters most for
system throughput — the claim scan — needs all of it without a join. Every
piece of size-unbounded per-attempt detail (errors, stack traces, results,
timing) is normalized out into `job_executions` instead, one row per
attempt, so the hot table's rows stay small and its index
(`ix_jobs_claim_scan` on `queue_id, status, run_at`) stays cheap to
maintain under heavy insert/update churn.

**`scheduled_jobs` vs `jobs`.** A cron schedule and a job instance have
different lifecycles — one lives forever (until deactivated), the other
lives once. Modeling them as the same table (e.g. a `jobs` row that
"resets" itself after each cron fire) would mean the hot table also has to
carry cron bookkeeping (`next_run_at`, `cron_expression`) on every row, most
of which don't need it, and it would make "show me every past instance of
this schedule" require awkward self-joins instead of a plain
`WHERE scheduled_job_id = ?`.

**`dead_letter_queue` as its own table, not just `status='dead_letter'`.**
Two reasons: (1) DLQ listing/paging is then a narrow, purpose-built scan
instead of filtering the (much busier) `jobs` table; (2) `payload_snapshot`
freezes what the payload looked like at time of death, independent of
anything that mutates the `jobs` row afterward (e.g. a future feature that
lets operators edit-and-retry).

**Retry policies are a separate, reusable table**, not columns inlined on
`queues`/`jobs`. A policy can be shared across many queues, and a job can
override its queue's default without duplicating the whole config — it just
points at a different (or new) `RetryPolicy` row.

**Cascade behavior**, summarized:
- `organization → project → queue → job → {job_executions, job_logs}`:
  `ON DELETE CASCADE`. Deleting a project shouldn't leave orphaned queues;
  deleting a job shouldn't leave orphaned execution history floating with a
  dangling FK.
- `user → job.created_by`, `worker → job_executions.worker_id`,
  `retry_policy → {queue,job,scheduled_job}`: `ON DELETE SET NULL`. Deleting
  a user account or decommissioning a worker record must not destroy job/
  execution audit history — it should just lose the attribution.
- `job.depends_on_job_id → job.id`: `ON DELETE SET NULL` (self-referencing).
  If an upstream job is deleted, the downstream job becomes unconditionally
  claimable rather than being deleted or stuck.

**Indexes beyond PK/FK auto-indexes**:
- `ix_jobs_claim_scan (queue_id, status, run_at)` — the claim query's
  entire `WHERE` clause in one composite index.
- `ix_jobs_status`, `ix_jobs_scheduled_job_id`, `ix_jobs_batch_id` —
  dashboard filters and grouped views.
- `ix_scheduled_jobs_due (is_active, next_run_at)` — the cron-tick scan.
- `ix_heartbeats_worker_time`, `ix_job_logs_job_created` — time-ordered
  detail views (worker health chart, per-job log stream) that would
  otherwise be full scans as those tables grow.

**Normalization**: schema is in 3NF. The one deliberate denormalization is
`jobs.attempt_count` duplicating what's derivable by `COUNT(*)` on
`job_executions` — kept as a counter column because the claim/retry/DLQ
decision path reads it on every single job transition and a `COUNT(*)`
join there would be a needless tax on the hottest code path in the system.
