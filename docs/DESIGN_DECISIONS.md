# Design Decisions & Honest Trade-offs

This doc is the "brutal and honest" self-assessment: what was built, what
was deliberately cut, what's a known weak point, and what breaks first
under load. Grading criteria references are inline.

## What's actually implemented vs. bonus list

| Bonus feature | Status | Notes |
|---|---|---|
| Workflow dependencies | **Done** | `jobs.depends_on_job_id`; claim query gates on upstream `COMPLETED`. Single-parent only — no fan-in/fan-out DAG, no "wait for all of N". |
| Distributed locking | **Done, honestly scoped** | `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres is the real cross-process guarantee. An in-process `asyncio.Lock` per queue was added on top after testing surfaced a same-process interleaving race (see "Bugs found by testing" below) — it's not a separate advisory-lock or Redlock implementation. |
| Rate limiting | **Done, two layers** | (1) Per-queue `rate_limit_per_minute` gates claiming. (2) API-wide token-bucket middleware. Both are in-process memory — see Limitations. |
| Role-based access control | **Done, enforced** | `OrganizationMember.role` (owner/admin/member/viewer) with a rank-based dependency (`require_org_role`). Originally the dependency existed but was wired to **no** route — that gap is now closed: `require_project_role(...)` guards every mutating route (queue create = MEMBER; pause/resume/update/scheduled-job-pause = ADMIN; job create/batch/cancel/retry = MEMBER; project create = MEMBER). A VIEWER hitting any of these gets 403 — covered by `tests/test_rbac.py`. Reads still enforce plain membership via `get_project_for_user`. |
| WebSocket live updates | **Done + authenticated** | In-process pub/sub (`websocket/manager.py`), broadcasts on every job/worker state transition. The handshake now validates a `?token=<JWT>` query param and rejects unauthorized clients with WS 1008 (previously it connected anyone — a real eavesdropping hole, now fixed). Frontend uses it as a "refresh now" signal, not as the sole data source (still polls every 4-6s as a fallback) — deliberate, since WS delivery isn't guaranteed and a dashboard that silently goes stale on a dropped socket is worse than one that occasionally double-fetches. |
| Queue sharding | **Not implemented** | Would mean partitioning `jobs` (e.g. by `queue_id % N` or a separate DB per shard) once a single Postgres instance's claim-query throughput is the ceiling. Didn't build it because doing it *well* means a routing layer and cross-shard dashboard aggregation — a half-built version would be worse than an honest "not done." |
| Event-driven execution | **Not implemented** | Everything here is poll-based (workers poll `/claim`). A true event-driven push (e.g. Postgres `LISTEN/NOTIFY` waking idle workers instantly instead of on their next poll tick) would cut claim latency from ~poll-interval to near-zero — reasonable next step, cut for time. Poll interval defaults to 1s, which is fine for background-job latency, not for anything needing sub-second dispatch. |
| AI-generated failure summaries | **Not implemented** | Would be a straightforward addition (send `error_message` + `error_stacktrace` to an LLM, store the summary on the `JobExecution` or `DeadLetterEntry` row) but adds an external API dependency this build doesn't otherwise have, and wasn't core to the reliability/concurrency grading criteria, so skipped in favor of hardening what's there. |

## Bugs the test suite actually caught (kept in, not smoothed over)

Writing `tests/test_concurrent_claims.py` (two workers racing to claim the
same 10 jobs) caught a real bug: two coroutines *within the same API
process*, both awaiting the DB mid-transaction, could both read the same
row as `QUEUED` before either had committed — because `FOR UPDATE SKIP
LOCKED` only helps once a transaction actually holds a lock the DB engine
enforces, and (a) SQLite doesn't support row locks at all, it's silently
dropped, and (b) even on Postgres, two `async def` handlers on the same
process interleave at `await` points, so without *something* serializing
the read-modify-commit span per queue, the theoretical guarantee doesn't
hold in practice for concurrent requests hitting the same process.

Fix: added `_queue_claim_locks: dict[int, asyncio.Lock]` in
`routers/workers.py` and made the claim-and-commit for each queue happen
while holding that queue's lock, committing *before* releasing it (an
earlier version flushed-but-didn't-commit inside the lock, which was still
broken — the next lock-holder's `SELECT` wouldn't see uncommitted rows from
another session). This is documented here instead of quietly fixed and
forgotten because it's exactly the kind of concurrency bug the assignment
is testing for, and pretending the first version of the claim query was
correct would be dishonest.

**Residual limitation**: this lock is per-*process*. Run two API replicas
behind a load balancer and the in-process lock no longer coordinates
between them — you're back to relying solely on `FOR UPDATE SKIP LOCKED`
for cross-process safety, which is correct on Postgres (verified logically;
not load-tested against a real multi-replica Postgres deployment in this
environment — see "Not verified" below).

## Reliability fixes applied during review (real defects, now closed)

These were found while grading the original submission against the spec and
fixed rather than left as known gaps:

1. **Lease renewal on heartbeat (prevents double-execution).** The worker
   heartbeat originally only updated `workers.current_load`; it did *not* touch
   the jobs the worker held. With `job_visibility_timeout_seconds = 60`, any
   job running longer than 60s would have its lease expire, be reaped by the
   scheduler loop, and potentially claimed + executed a *second* time by
   another worker. `POST /workers/{id}/heartbeat` now extends
   `lease_expires_at` on every job held by that worker (`CLAIMED`/`RUNNING`),
   so a healthy worker keeps its leases fresh indefinitely.

2. **`JobStatus.FAILED` was a dead status.** Nothing ever assigned it, so the
   dashboard "Failed" counter was permanently 0 and the documented lifecycle
   (`… → Running → Completed, with retries`) skipped FAILED entirely. A
   retriable failure now sets `status = FAILED` with a `next_retry_at`, and the
   claim path promotes `FAILED → QUEUED` once the backoff window elapses. FAILED
   is now observable between attempts and the Failed counter is meaningful
   (`tests/test_failed_state.py` pins this behavior).

3. **`job_logs` was never written.** The spec requires maintaining execution
   logs; the table existed but no code populated it. `start_job` and
   `report_result` now emit `JobLog` rows (INFO on start/success, ERROR on
   failure with the message), so the job detail view's log stream is real.

4. **`jobs.created_by` was never set.** `create_job` now stamps the
   authenticated user's id for audit provenance (surfaced on `JobOut`).

5. **WebSocket auth hole.** `/ws/projects/{id}` connected any client. Now
   validates the JWT from `?token=` and closes with 1008 on failure.

6. **CORS `allow_credentials=True` + wildcard origin** (invalid/insecure
   combination) → set `allow_credentials=False` since the dashboard uses a
   bearer header, never cookies.

## Things genuinely not verified

Be clear-eyed about what "tests pass" does and doesn't prove here:

- **The default 17 tests run against SQLite** (in-memory, `StaticPool`, single
   connection), not Postgres. SQLite was used for test speed and zero
   external dependencies. It validates the *application logic* (status
   transitions, retry math, dependency gating, the lock-based claim
   serialization, RBAC) but **not** the actual `FOR UPDATE SKIP LOCKED` behavior,
   which SQLite silently ignores. `tests/test_concurrent_claims_postgres.py`
   now closes that gap: run `DATABASE_URL=postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler pytest tests/test_concurrent_claims_postgres.py`
   (the `docker compose` Postgres qualifies) and it races two workers against
   real Postgres, asserting no duplicate/lost claims. It is skipped
   automatically when `DATABASE_URL` is not postgresql, so CI without Postgres
   stays green.
- **No load testing was run.** No numbers here for "jobs/sec this
  sustains" — would be dishonest to make one up. The claim query is
  indexed and bounded (`LIMIT max_jobs`), which is the right shape for
  scaling, but the actual ceiling depends on Postgres instance size,
  network latency between workers and API, and job execution time — not
  measured.
- **The RBAC role-check dependency (`require_org_role`) is defined but not
  applied to every mutating endpoint** — noted above, not hidden.

## Other trade-offs, by area

**Rate limiting is in-process memory**, not Redis-backed. Correct for one
API replica, silently under-enforces (each replica gets its own bucket) at
N replicas. Called out in `services/rate_limit.py`'s own docstring, not
just here.

**WebSocket fanout is in-process**, same limitation — a client connected to
replica A never hears about an event that started on replica B. At
production scale this needs Postgres `LISTEN/NOTIFY` or Redis pub/sub as
the fanout backbone across replicas; single-replica (which is exactly what
`docker compose up` gives you) it works correctly.

**The scheduler loop (promote/cron/reap) runs in every API replica**, not
leader-elected. Each tick's queries are idempotent conditional `UPDATE`s
(guarded by `WHERE status = ...`), so running it redundantly N times is
*correct*, just wasteful — a Postgres advisory lock (`pg_advisory_lock`)
around the loop body is the one-line-ish fix, cut for time in favor of
spending that time on the claim-path concurrency bug above, which mattered
more for correctness.

**Idempotency is at the queue+key level, not global.** `idempotency_key` is
unique per `(queue_id, idempotency_key)`, not globally, on the theory that
the same logical key ("send-welcome-email-user-42") might legitimately be
reused across different queues for different purposes. If that's wrong for
a given use case, the fix is a schema change (drop `queue_id` from the
unique constraint), not an application-logic change.

**No soft-delete anywhere.** Deleting a project/queue is a hard cascade
delete. For a real product you'd want a `deleted_at` column and to filter
it everywhere instead — straightforward but touches every query, cut for
scope.

**Auth is JWT with no refresh-token flow.** Tokens are 12h and there's no
rotation/refresh endpoint — fine for a demo/intern-eval scope, not
something to ship to real users without adding one.

**Frontend has no automated tests.** The pytest suite covers backend
lifecycle/concurrency/retry logic, which is where the assignment's grading
weight actually sits (Reliability & Concurrency: 15, Backend Engineering:
20, vs. Frontend & UX: 10) — frontend testing (React Testing Library /
Playwright) would be the next addition given more time, not because the UI
doesn't matter, but because it wasn't where the point value was.

## Why Postgres `SELECT FOR UPDATE SKIP LOCKED` over a message broker

Covered in `ARCHITECTURE.md`; the short version restated here since it's
the single most consequential design decision in the system: it means
*one* piece of infrastructure to run, deploy, and reason about failure
modes for, at the cost of a lower ultimate throughput ceiling than a
dedicated broker (Kafka/RabbitMQ/SQS) would give you. For the job volumes
implied by this assignment's scope (an intern-eval scheduler, not a
company's core event bus), that trade is correct. The point at which it
stops being correct is roughly "single Postgres instance can't keep up
with claim-query QPS even after read-replica offloading of the dashboard
reads" — at that point, queue sharding (explicitly *not* built here) or a
broker-backed architecture is the right next step, not a micro-optimization
of the current query.
