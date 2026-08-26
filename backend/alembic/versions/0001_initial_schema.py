"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("is_superuser", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "organizations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "organization_members",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.BigInteger, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("api_key_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "slug", name="uq_project_slug_per_org"),
    )
    op.create_index("ix_projects_slug", "projects", ["slug"])

    op.create_table(
        "retry_policies",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("base_delay_seconds", sa.Float, nullable=False, server_default="5"),
        sa.Column("max_delay_seconds", sa.Float, nullable=False, server_default="3600"),
        sa.Column("multiplier", sa.Float, nullable=False, server_default="2"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("jitter", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("max_attempts >= 0", name="ck_retry_max_attempts_nonneg"),
    )

    op.create_table(
        "queues",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.BigInteger, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_concurrency", sa.Integer, nullable=False, server_default="5"),
        sa.Column("is_paused", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("default_retry_policy_id", sa.BigInteger, sa.ForeignKey("retry_policies.id", ondelete="SET NULL")),
        sa.Column("rate_limit_per_minute", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "name", name="uq_queue_name_per_project"),
        sa.CheckConstraint("max_concurrency > 0", name="ck_queue_concurrency_positive"),
    )

    op.create_table(
        "batches",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("queue_id", sa.BigInteger, sa.ForeignKey("queues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("total_jobs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("queue_id", sa.BigInteger, sa.ForeignKey("queues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("job_type", sa.String(255), nullable=False),
        sa.Column("payload_template", sa.JSON, nullable=False),
        sa.Column("cron_expression", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("retry_policy_id", sa.BigInteger, sa.ForeignKey("retry_policies.id", ondelete="SET NULL")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scheduled_jobs_due", "scheduled_jobs", ["is_active", "next_run_at"])
    op.create_index("ix_scheduled_jobs_next_run_at", "scheduled_jobs", ["next_run_at"])

    op.create_table(
        "workers",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.BigInteger, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("pid", sa.Integer, nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
        sa.Column("concurrency_capacity", sa.Integer, nullable=False, server_default="4"),
        sa.Column("current_load", sa.Integer, nullable=False, server_default="0"),
        sa.Column("queues_subscribed", sa.JSON, nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_workers_project_status", "workers", ["project_id", "status"])

    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.BigInteger, sa.ForeignKey("workers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("active_jobs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cpu_pct", sa.Float),
        sa.Column("mem_mb", sa.Float),
    )
    op.create_index("ix_heartbeats_worker_time", "worker_heartbeats", ["worker_id", "heartbeat_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("queue_id", sa.BigInteger, sa.ForeignKey("queues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_type", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("retry_policy_id", sa.BigInteger, sa.ForeignKey("retry_policies.id", ondelete="SET NULL")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by_worker_id", sa.BigInteger, sa.ForeignKey("workers.id", ondelete="SET NULL")),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("scheduled_job_id", sa.BigInteger, sa.ForeignKey("scheduled_jobs.id", ondelete="SET NULL")),
        sa.Column("batch_id", sa.BigInteger, sa.ForeignKey("batches.id", ondelete="SET NULL")),
        sa.Column("depends_on_job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.BigInteger, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("queue_id", "idempotency_key", name="uq_job_idempotency_per_queue"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_job_attempt_nonneg"),
    )
    op.create_index("ix_jobs_claim_scan", "jobs", ["queue_id", "status", "run_at"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_scheduled_job_id", "jobs", ["scheduled_job_id"])
    op.create_index("ix_jobs_batch_id", "jobs", ["batch_id"])

    op.create_table(
        "job_executions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.BigInteger, sa.ForeignKey("workers.id", ondelete="SET NULL")),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("error_message", sa.Text),
        sa.Column("error_stacktrace", sa.Text),
        sa.Column("result", sa.JSON),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_execution_attempt"),
    )
    op.create_index("ix_executions_worker", "job_executions", ["worker_id"])

    op.create_table(
        "job_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("execution_id", sa.BigInteger, sa.ForeignKey("job_executions.id", ondelete="CASCADE")),
        sa.Column("level", sa.String(10), nullable=False, server_default="info"),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_job_logs_job_created", "job_logs", ["job_id", "created_at"])

    op.create_table(
        "dead_letter_queue",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("queue_id", sa.BigInteger, sa.ForeignKey("queues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("payload_snapshot", sa.JSON, nullable=False),
        sa.Column("moved_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reprocessed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reprocessed_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("dead_letter_queue")
    op.drop_table("job_logs")
    op.drop_table("job_executions")
    op.drop_index("ix_jobs_batch_id", table_name="jobs")
    op.drop_index("ix_jobs_scheduled_job_id", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_claim_scan", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("worker_heartbeats")
    op.drop_table("workers")
    op.drop_table("scheduled_jobs")
    op.drop_table("batches")
    op.drop_table("queues")
    op.drop_table("retry_policies")
    op.drop_table("projects")
    op.drop_table("organization_members")
    op.drop_table("organizations")
    op.drop_table("users")
