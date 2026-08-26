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


async def test_full_happy_path_completes_job(client, auth_headers, project):
    project_id = project["id"]
    api_key = project["api_key"]

    q = await client.post(f"/projects/{project_id}/queues", headers=auth_headers, json={"name": "default", "max_concurrency": 2})
    assert q.status_code == 201
    queue_id = q.json()["id"]

    j = await client.post(
        f"/projects/{project_id}/queues/{queue_id}/jobs", headers=auth_headers,
        json={"job_type": "noop", "payload": {"x": 1}},
    )
    assert j.status_code == 201
    job = j.json()
    assert job["status"] == "queued"

    worker_id = await _register_worker(client, project_id, api_key)

    claim = await client.post(
        f"/projects/{project_id}/workers/{worker_id}/claim", headers={"X-API-Key": api_key},
        json={"queue_names": ["default"], "max_jobs": 5},
    )
    assert claim.status_code == 200
    claimed = claim.json()
    assert len(claimed) == 1
    assert claimed[0]["id"] == job["id"]
    assert claimed[0]["status"] == "claimed"

    start = await client.post(
        f"/projects/{project_id}/jobs/{job['id']}/start", headers={"X-API-Key": api_key}, params={"worker_id": worker_id},
    )
    assert start.status_code == 200
    assert start.json()["status"] == "running"
    assert start.json()["attempt_count"] == 1

    result = await client.post(
        f"/projects/{project_id}/jobs/{job['id']}/result", headers={"X-API-Key": api_key},
        params={"worker_id": worker_id}, json={"success": True, "result": {"ok": True}},
    )
    assert result.status_code == 200
    assert result.json()["status"] == "completed"

    detail = await client.get(f"/projects/{project_id}/jobs/{job['id']}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["executions"]) == 1
    assert body["executions"][0]["status"] == "succeeded"


async def test_job_retries_then_moves_to_dead_letter(client, auth_headers, project):
    project_id, api_key = project["id"], project["api_key"]

    q = await client.post(f"/projects/{project_id}/queues", headers=auth_headers, json={"name": "flaky", "max_concurrency": 1})
    queue_id = q.json()["id"]

    j = await client.post(
        f"/projects/{project_id}/queues/{queue_id}/jobs", headers=auth_headers,
        json={
            "job_type": "fail_always", "payload": {}, "max_attempts": 2,
            "retry_policy": {"name": "fast", "strategy": "fixed", "base_delay_seconds": 0, "jitter": False, "max_attempts": 2},
        },
    )
    job_id = j.json()["id"]
    worker_id = await _register_worker(client, project_id, api_key, queues=("flaky",))

    for attempt in range(2):
        claim = await client.post(
            f"/projects/{project_id}/workers/{worker_id}/claim", headers={"X-API-Key": api_key},
            json={"queue_names": ["flaky"], "max_jobs": 5},
        )
        claimed = claim.json()
        assert len(claimed) == 1, f"expected a claimable job on attempt {attempt}"
        await client.post(f"/projects/{project_id}/jobs/{job_id}/start", headers={"X-API-Key": api_key}, params={"worker_id": worker_id})
        result = await client.post(
            f"/projects/{project_id}/jobs/{job_id}/result", headers={"X-API-Key": api_key},
            params={"worker_id": worker_id}, json={"success": False, "error_message": "boom"},
        )

    assert result.json()["status"] == "dead_letter"

    dlq = await client.get(f"/projects/{project_id}/dead-letter-queue", headers=auth_headers)
    assert dlq.status_code == 200
    assert any(d["id"] == job_id for d in dlq.json())


async def test_queue_pause_prevents_claiming(client, auth_headers, project):
    project_id, api_key = project["id"], project["api_key"]
    q = await client.post(f"/projects/{project_id}/queues", headers=auth_headers, json={"name": "paused-q"})
    queue_id = q.json()["id"]
    await client.post(f"/projects/{project_id}/queues/{queue_id}/pause", headers=auth_headers)

    await client.post(f"/projects/{project_id}/queues/{queue_id}/jobs", headers=auth_headers, json={"job_type": "noop"})
    worker_id = await _register_worker(client, project_id, api_key, queues=("paused-q",))

    claim = await client.post(
        f"/projects/{project_id}/workers/{worker_id}/claim", headers={"X-API-Key": api_key},
        json={"queue_names": ["paused-q"], "max_jobs": 5},
    )
    assert claim.json() == []


async def test_job_respects_dependency_ordering(client, auth_headers, project):
    project_id, api_key = project["id"], project["api_key"]
    q = await client.post(f"/projects/{project_id}/queues", headers=auth_headers, json={"name": "deps"})
    queue_id = q.json()["id"]

    upstream = (await client.post(f"/projects/{project_id}/queues/{queue_id}/jobs", headers=auth_headers, json={"job_type": "noop"})).json()
    downstream = (await client.post(
        f"/projects/{project_id}/queues/{queue_id}/jobs", headers=auth_headers,
        json={"job_type": "noop", "depends_on_job_id": upstream["id"]},
    )).json()

    worker_id = await _register_worker(client, project_id, api_key, queues=("deps",))

    # Only the upstream job should be claimable while downstream's dependency is unmet.
    claim = await client.post(
        f"/projects/{project_id}/workers/{worker_id}/claim", headers={"X-API-Key": api_key},
        json={"queue_names": ["deps"], "max_jobs": 5},
    )
    claimed_ids = [j["id"] for j in claim.json()]
    assert claimed_ids == [upstream["id"]]
