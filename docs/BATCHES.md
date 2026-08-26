# Codebase Batches

Each batch is self-contained enough to load, review, or hand to a fresh
context window without needing the rest of the repo open at the same time.
Ordered by dependency — earlier batches are inputs to later ones. File
paths are relative to the repo root.

| # | Batch | Files | Depends on | ~Size |
|---|---|---|---|---|
| 1 | **Config & DB bootstrap** | `backend/app/config.py`, `backend/app/database.py` | none | 2 files |
| 2 | **Schema (the whole ER model)** | `backend/app/models.py` | 1 | 1 file, largest single file |
| 3 | **Migration (schema as SQL)** | `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/script.py.mako`, `backend/alembic/versions/0001_initial_schema.py` | 2 | 4 files |
| 4 | **Auth primitives** | `backend/app/security.py`, `backend/app/core/deps.py` | 1, 2 | 2 files |
| 5 | **API contracts (Pydantic)** | `backend/app/schemas.py` | 2 | 1 file |
| 6 | **Auth + Projects routes** | `backend/app/routers/auth.py`, `backend/app/routers/projects.py` | 1–5 | 2 files |
| 7 | **Queues route** | `backend/app/routers/queues.py` | 1–5 | 1 file |
| 8 | **Retry math (pure, unit-testable in isolation)** | `backend/app/services/retry.py` | 2 | 1 file |
| 9 | **Jobs route** | `backend/app/routers/jobs.py` | 1–5, 8 | 1 file |
| 10 | **Rate limiting middleware** | `backend/app/services/rate_limit.py` | 1 | 1 file, no DB dependency |
| 11 | **Workers route (claim/lifecycle — the concurrency-critical batch)** | `backend/app/routers/workers.py` | 1–5, 8 | 1 file, read this one slowly |
| 12 | **Background scheduler loop** | `backend/app/services/scheduler.py` | 2, 8 | 1 file |
| 13 | **WebSocket fanout + dashboard route** | `backend/app/websocket/manager.py`, `backend/app/routers/dashboard.py`, `backend/app/routers/ws.py` | 1–5 | 3 files |
| 14 | **App wiring** | `backend/app/main.py` | 1, 4, 6, 7, 9, 10, 11, 12, 13 | 1 file, pulls everything together |
| 15 | **Standalone worker process** | `backend/app/worker/worker.py` | none (talks to the API over HTTP only — no shared imports with the rest of the backend) | 1 file |
| 16 | **Backend test infra** | `backend/tests/conftest.py`, `backend/pytest.ini` | 1–14 | 2 files |
| 16b | **Backend tests — config validation** | `backend/tests/test_config.py` | 1 | 1 file, no DB/HTTP; regression-guards the batch-1 audit fixes |
| 17 | **Backend tests — pure logic** | `backend/tests/test_retry.py` | 8 | 1 file, no DB/HTTP |
| 18 | **Backend tests — lifecycle & rules** | `backend/tests/test_job_lifecycle.py` | 16 | 1 file |
| 19 | **Backend tests — concurrency** | `backend/tests/test_concurrent_claims.py` | 16 | 1 file, pairs with batch 11 |
| 20 | **Backend packaging** | `backend/requirements.txt`, `backend/.env.example`, `backend/.gitignore`, `backend/Dockerfile`, `backend/Dockerfile.worker` | none | 5 files |
| 21 | **Frontend typed API client** | `frontend/src/lib/api.ts` | none (mirrors backend schemas by hand — no codegen) | 1 file |
| 22 | **Frontend auth + live-events plumbing** | `frontend/src/lib/auth.tsx`, `frontend/src/lib/useProjectEvents.ts` | 21 | 2 files |
| 23 | **Frontend shared UI atoms** | `frontend/src/components/ui.tsx`, `frontend/src/index.css` | none | 2 files |
| 24 | **Frontend shell & routing** | `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/pages/DashboardLayout.tsx` | 22 | 3 files |
| 25 | **Frontend auth/onboarding pages** | `frontend/src/pages/LoginPage.tsx`, `frontend/src/pages/ProjectSetupPage.tsx` | 21, 22, 23 | 2 files |
| 26 | **Frontend Overview page** | `frontend/src/pages/OverviewPage.tsx` | 21, 23, 24 | 1 file |
| 27 | **Frontend Queues page** | `frontend/src/pages/QueuesPage.tsx` | 21, 23, 24 | 1 file |
| 28 | **Frontend Job Explorer page** | `frontend/src/pages/JobsPage.tsx` | 21, 23, 24 | 1 file, largest frontend file |
| 29 | **Frontend Workers page** | `frontend/src/pages/WorkersPage.tsx` | 21, 23, 24 | 1 file |
| 30 | **Frontend DLQ page** | `frontend/src/pages/DlqPage.tsx` | 21, 23, 24 | 1 file |
| 31 | **Frontend build config** | `frontend/package.json`, `frontend/package-lock.json`, `frontend/vite.config.ts`, `frontend/postcss.config.js`, `frontend/tsconfig*.json`, `frontend/index.html`, `frontend/.oxlintrc.json`, `frontend/.env`, `frontend/.gitignore`, `frontend/Dockerfile` | none | 10 files, no logic |
| 32 | **Deploy orchestration** | `docker-compose.yml` | 20, 31 | 1 file |
| 33 | **Docs — architecture** | `docs/ARCHITECTURE.md` | conceptual, references all batches | 1 file |
| 34 | **Docs — schema** | `docs/ER_DIAGRAM.md` | 2 | 1 file |
| 35 | **Docs — API surface** | `docs/API.md` | 6, 7, 9, 11, 13 | 1 file |
| 36 | **Docs — trade-offs / honesty pass** | `docs/DESIGN_DECISIONS.md` | conceptual, references all batches | 1 file |
| 37 | **Top-level README** | `README.md` | conceptual | 1 file |

## How to use this for review

- **Correctness-critical path**: 1 → 2 → 8 → 11 → 12 → 19. This is the
  claim/retry/DLQ/concurrency core — read these six in order before
  anything else if you're auditing reliability.
- **Independent/parallelizable batches**: 15 (worker process), 20/31
  (packaging), 21–30 (frontend) share almost no state with the backend
  route batches and can be reviewed in any order or by a different reviewer
  entirely.
- **Docs batches (33–37)** are safe to skip on a code-only pass and safe to
  read standalone without the code open — each was written to stand alone.

## Rebuild ordering (if regenerating from scratch in a fresh context)

Follow the numeric order above as a topological sort — every batch's
"Depends on" column lists only earlier numbers, so building 1→37 in order
never requires forward references.
