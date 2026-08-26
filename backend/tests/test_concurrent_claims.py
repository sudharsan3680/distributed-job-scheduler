import asyncio

import pytest

pytestmark = pytest.mark.asyncio


async def test_two_workers_racing_never_claim_the_same_job(client, auth_headers, project):
    """
    Regression test for the core reliability guarantee of the scheduler:
    with N jobs and two workers claiming concurrently, every job is claimed
    by exactly one worker -- no duplicates, no drops.

    Honest caveat: this runs against SQLite with a single shared connection
    (StaticPool), so it proves the claim query's WHERE-clause logic is
    correct (a job claimed by request A is no longer visible to request B)
    but it does NOT exercise real multi-connection row-lock contention --
    SQLite serializes access to that one connection regardless. The
    `FOR UPDATE SKIP LOCKED` clause that provides the actual cross-process
    guarantee only compiles against Postgres; verifying it under real
    concurrent load requires the docker-compose Postgres stack (see
    docs/DESIGN_DECISIONS.md for the manual multi-worker load test used to
    validate that in practice).
    """
    project_id, api_key = project["id"], project["api_key"]

    q = await client.post(f"/projects/{project_id}/queues", headers=auth_headers, json={"name": "race", "max_concurrency": 20})
    queue_id = q.json()["id"]

    n_jobs = 10
    job_ids = []
    for i in range(n_jobs):
        r = await client.post(f"/projects/{project_id}/queues/{queue_id}/jobs", headers=auth_headers, json={"job_type": "noop", "payload": {"i": i}})
        job_ids.append(r.json()["id"])

    async def register(label):
        r = await client.post(
            f"/projects/{project_id}/workers/register", headers={"X-API-Key": api_key},
            json={"hostname": "h", "pid": 1, "label": label, "concurrency_capacity": 10, "queues": ["race"]},
        )
        return r.json()["id"]

    worker_a = await register("worker-a")
    worker_b = await register("worker-b")

    async def claim(worker_id):
        r = await client.post(
            f"/projects/{project_id}/workers/{worker_id}/claim", headers={"X-API-Key": api_key},
            json={"queue_names": ["race"], "max_jobs": n_jobs},
        )
        return [j["id"] for j in r.json()]

    results = await asyncio.gather(claim(worker_a), claim(worker_b))
    claimed_a, claimed_b = results

    all_claimed = claimed_a + claimed_b
    assert len(all_claimed) == len(set(all_claimed)), "a job was claimed by both workers -- atomicity violated"
    assert set(all_claimed) == set(job_ids), "every job should be claimed exactly once across both workers"
