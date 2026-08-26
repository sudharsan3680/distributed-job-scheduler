"""
Real cross-process / cross-connection atomicity proof.

The default test suite runs against in-memory SQLite (single shared
connection), which validates the claim query's WHERE-clause logic but cannot
exercise `SELECT ... FOR UPDATE SKIP LOCKED` — SQLite silently ignores row
locks. This test closes that gap: it points the app at a real Postgres (via
the DATABASE_URL env var) and races two workers concurrently, proving no job
is claimed twice and none are dropped.

It is SKIPPED unless DATABASE_URL points at a Postgres instance, so the suite
stays zero-dependency by default:

    DATABASE_URL=postgresql+asyncpg://scheduler:scheduler@localhost:5432/scheduler \
        pytest tests/test_concurrent_claims_postgres.py
"""
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

pytestmark = pytest.mark.asyncio

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_RUN_POSTGRES = _DATABASE_URL.startswith("postgresql")

skipif_no_postgres = pytest.mark.skipif(
    not _RUN_POSTGRES,
    reason="DATABASE_URL is not postgresql; skipping real SKIP LOCKED concurrency test",
)


@pytest.fixture
async def postgres_client():
    engine = create_async_engine(_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    await engine.dispose()


@skipif_no_postgres
async def test_two_workers_never_claim_same_job_under_postgres(postgres_client):
    client = postgres_client
    reg = await client.post("/auth/register", json={
        "email": "pg@example.com", "password": "supersecret1",
        "full_name": "PG", "organization_name": "PGOrg",
    })
    auth = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    api_key = reg.json()["user"].get("api_key")  # not present on register; create project
    proj = await client.post("/organizations/1/projects", headers=auth, json={"name": "P", "slug": "p"})
    api_key = proj.json()["api_key"]

    q = await client.post("/projects/1/queues", headers=auth, json={"name": "race", "max_concurrency": 20})
    queue_id = q.json()["id"]

    n_jobs = 20
    for i in range(n_jobs):
        await client.post(f"/projects/1/queues/{queue_id}/jobs", headers=auth, json={"job_type": "noop", "payload": {"i": i}})

    async def register(label):
        r = await client.post(f"/projects/1/workers/register", headers={"X-API-Key": api_key},
                              json={"hostname": "h", "pid": 1, "label": label, "concurrency_capacity": 20, "queues": ["race"]})
        return r.json()["id"]

    wa, wb = await register("a"), register("b")

    async def claim(wid):
        r = await client.post(f"/projects/1/workers/{wid}/claim", headers={"X-API-Key": api_key},
                             json={"queue_names": ["race"], "max_jobs": n_jobs})
        return [j["id"] for j in r.json()]

    import asyncio
    results = await asyncio.gather(claim(wa), claim(wb))
    all_claimed = results[0] + results[1]
    assert len(all_claimed) == len(set(all_claimed)), "duplicate claim under Postgres!"
    assert set(all_claimed) == set(range(1, n_jobs + 1))
