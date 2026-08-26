import pytest

pytestmark = pytest.mark.asyncio


async def _register_worker(client, project_id, api_key, queues=("default",)):
    resp = await client.post(
        f"/projects/{project_id}/workers/register",
        headers={"X-API-Key": api_key},
        json={"hostname": "h", "pid": 1, "label": "w1", "concurrency_capacity": 4, "queues": list(queues)},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_failed_status_is_real_and_promotes_on_backoff(client, auth_headers, project):
    """Regression test for the previously-dead JobStatus.FAILED. A retriable
    failure must leave the job in FAILED (visible in the dashboard / stats)
    and, once the backoff window elapses, the next claim poll promotes it back
    to QUEUED and reclaims it. Before this change the job went straight to
    QUEUED and the 'Failed' counter was always 0."""
    project_id, api_key = project["id"], project["api_key"]

    q = await client.post(f"/projects/{project_id}/queues", headers=auth_headers, json={"name": "flaky", "max_concurrency": 1})
    queue_id = q.json()["id"]

    j = await client.post(
        f"/projects/{project_id}/queues/{queue_id}/jobs", headers=auth_headers,
        json={
            "job_type": "fail_always", "payload": {}, "max_attempts": 3,
            "retry_policy": {"name": "fast", "strategy": "fixed", "base_delay_seconds": 0, "jitter": False, "max_attempts": 3},
        },
    )
    job_id = j.json()["id"]
    worker_id = await _register_worker(client, project_id, api_key, queues=("flaky",))

    # First attempt fails (retriable) -> FAILED, not QUEUED/DEAD_LETTER.
    await client.post(f"/projects/{project_id}/workers/{worker_id}/claim", headers={"X-API-Key": api_key}, json={"queue_names": ["flaky"], "max_jobs": 5})
    await client.post(f"/projects/{project_id}/jobs/{job_id}/start", headers={"X-API-Key": api_key}, params={"worker_id": worker_id})
    r = await client.post(f"/projects/{project_id}/jobs/{job_id}/result", headers={"X-API-Key": api_key}, params={"worker_id": worker_id}, json={"success": False, "error_message": "boom"})
    assert r.json()["status"] == "failed", r.text

    # The per-queue stats now reflect 1 failed job (was always 0 before).
    stats = await client.get(f"/projects/{project_id}/queues/{queue_id}/stats", headers=auth_headers)
    assert stats.json()["failed"] == 1

    # A new claim poll promotes FAILED -> QUEUED (backoff is 0s) and reclaims it.
    claim = await client.post(f"/projects/{project_id}/workers/{worker_id}/claim", headers={"X-API-Key": api_key}, json={"queue_names": ["flaky"], "max_jobs": 5})
    claimed = claim.json()
    assert len(claimed) == 1 and claimed[0]["id"] == job_id
    assert claimed[0]["status"] == "claimed"

    # Execution logs should have been written (start + failure).
    detail = await client.get(f"/projects/{project_id}/jobs/{job_id}", headers=auth_headers)
    assert len(detail.json()["logs"]) >= 2
