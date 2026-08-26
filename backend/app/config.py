"""
Central configuration. All tunables come from environment variables so the
same image can run in dev / CI / prod without code changes.
"""
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULT_JWT_SECRET = "CHANGE_ME_IN_PRODUCTION"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    # Async URL used by the API (asyncpg) for all request-time queries.
    # Sync URL is used ONLY by Alembic migrations (alembic/env.py), which
    # run outside the event loop. The standalone worker process
    # (app/worker/worker.py) does NOT use either of these -- it never
    # touches the database directly, only the HTTP API (see its own
    # docstring), so there is intentionally no "worker" database config
    # here.
    database_url: str = "postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler"
    database_url_sync: str = "postgresql+psycopg2://scheduler:scheduler@localhost:5432/scheduler"

    # --- Database connection pool (applied in app/database.py) ---
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 1800  # recycle connections before a managed-Postgres/LB idle-close drops them silently

    # --- Auth ---
    jwt_secret: str = _INSECURE_DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12

    # --- Scheduler tuning (background loop in app/services/scheduler.py) ---
    job_visibility_timeout_seconds: int = 60  # how long a CLAIMED/RUNNING lease is held before the reaper reclaims it
    scheduler_tick_seconds: float = 1.0  # how often the loop promotes due delayed/cron jobs and reaps stale leases

    # --- Rate limiting (token bucket, in-process; see app/services/rate_limit.py) ---
    rate_limit_requests_per_minute: int = 120

    # Gates the startup security check below. Set to "production" in any
    # real deployment. Also intended for any future environment-gated
    # behavior (verbose error bodies, docs exposure, etc.) -- there isn't
    # any yet, but this is the single flag that should drive it.
    environment: str = "development"

    @model_validator(mode="after")
    def _fail_fast_on_insecure_production_secret(self) -> "Settings":
        """
        Every field above has a default, which means a misconfigured
        deployment (env vars simply not set) would otherwise start up
        silently instead of failing -- for most fields that's a fine
        trade-off (wrong DB URL just fails loudly on first query), but for
        `jwt_secret` a silent fallback to the checked-in placeholder means
        every token the API issues is forgeable by anyone who has read this
        file. In production, refuse to start rather than run insecurely.
        """
        if self.environment == "production":
            if self.jwt_secret == _INSECURE_DEFAULT_JWT_SECRET:
                raise ValueError(
                    "JWT_SECRET is still the placeholder default. Set a real "
                    "JWT_SECRET environment variable before running with "
                    "ENVIRONMENT=production."
                )
            if len(self.jwt_secret) < 32:
                raise ValueError(
                    f"JWT_SECRET is only {len(self.jwt_secret)} characters. "
                    "Use a random secret of at least 32 characters in production."
                )
        return self


settings = Settings()
