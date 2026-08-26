# Distributed Job Scheduler

Production-inspired async job scheduling platform: multi-tenant auth,
projects/queues/jobs, a real worker fleet with atomic claiming, retry with
backoff, Dead Letter Queue, and a live dashboard.

See `docs/` for the architecture, ER diagram, API reference, and an honest
design-decisions / trade-offs writeup (including what's cut and why).

## Stack

- **Backend**: FastAPI (async), SQLAlchemy 2.0, PostgreSQL, Alembic, JWT auth, WebSockets
- **Worker**: standalone Python asyncio process, polls + claims + executes + heartbeats
- **Frontend**: React + TypeScript + Vite + Tailwind v4 + Recharts
- **Tests**: pytest + httpx (async), including a concurrency race test

## Quickstart — Docker (recommended)

```bash
docker compose up --build
```

- API: http://localhost:8000 (docs at `/docs`)
- Frontend: http://localhost:5173
- Postgres: localhost:5432 (scheduler/scheduler/scheduler)

Migrations run automatically on API container start (`alembic upgrade head`).

Register an account and create a project through the UI at
`http://localhost:5173`. Creating a project shows you a one-time API key —
save it, you'll need it to start workers.

## Running a worker

Workers are a separate process, started with the API key from a project:

```bash
cd backend
pip install -r requirements.txt
python -m app.worker.worker \
  --base-url http://localhost:8000 \
  --api-key sk_xxx \
  --project-id 1 \
  --queues default,emails \
  --concurrency 4
```

Start as many as you like — they compete for work safely (see
`docs/DESIGN_DECISIONS.md` for how atomic claiming works). Ctrl+C triggers a
graceful drain: the worker stops claiming new jobs, finishes in-flight ones,
then deregisters.

Job handlers live in `backend/app/worker/worker.py` (`@handler("job_type")`
decorator). Four example handlers ship out of the box: `noop`, `sleep`,
`http_request`, and `fail_always` (for exercising the retry/DLQ path).

## Local dev without Docker

```bash
# Postgres must be running locally on 5432 with a `scheduler` db/user, or
# edit backend/.env to point elsewhere.
cd backend
cp .env.example .env
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload

# separate terminal
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend
pip install -r requirements.txt
pytest -v
```

17 tests covering: full job lifecycle (queued → claimed → running →
completed), the now-real FAILED state + backoff promotion, retry-then-dead-letter,
queue pause, workflow dependency ordering, RBAC enforcement (VIEWER blocked /
OWNER allowed), concurrent-claim atomicity, and retry backoff math
(fixed/linear/exponential/jitter/cap). Runs against an in-memory SQLite DB —
no external services needed. A separate `test_concurrent_claims_postgres.py`
races two workers against a real Postgres (`DATABASE_URL=postgresql+asyncpg://…`)
to prove `FOR UPDATE SKIP LOCKED` under genuine cross-connection contention;
it is skipped automatically when no Postgres URL is configured.

## Project layout

```
backend/
  app/
    models.py          # SQLAlchemy schema (see docs/ER_DIAGRAM.md)
    schemas.py          # Pydantic request/response models
    routers/             # auth, projects, queues, jobs, workers, dashboard, ws
    services/
      retry.py           # backoff math
      rate_limit.py       # token-bucket API rate limiting
      scheduler.py        # background loop: promote delayed jobs, fire cron, reap stale leases
    worker/worker.py     # standalone worker process
    websocket/manager.py # live dashboard event fanout
  alembic/               # migrations
  tests/
frontend/
  src/
    pages/                # Overview, Queues, Job Explorer, Workers, DLQ
    lib/                  # typed API client, auth context, WS hook
docs/
  ARCHITECTURE.md
  ER_DIAGRAM.md
  API.md
  DESIGN_DECISIONS.md
```
