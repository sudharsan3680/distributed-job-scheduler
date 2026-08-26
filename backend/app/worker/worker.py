"""
Standalone worker process. Run with:

    python -m app.worker.worker --api-key sk_xxx --queues emails,reports --concurrency 4

Design:
- Polls `/claim` on an interval, requesting up to (concurrency - current in
  flight) jobs at once, so a slow job doesn't block claiming work for its
  idle siblings.
- Each claimed job runs as its own asyncio task -> real concurrency within
  one process, bounded by an asyncio.Semaphore(concurrency).
- SIGTERM/SIGINT triggers a graceful drain: stop claiming new work, call
  /drain so the dashboard reflects it, let in-flight tasks finish (up to a
  timeout), then deregister.
- Heartbeats run on their own independent timer so a burst of long jobs
  never causes a missed heartbeat -> false-positive lease reaping.
"""
import argparse
import asyncio
import logging
import os
import signal
import socket
import time
import traceback
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

JobHandler = Callable[[dict], Awaitable[dict]]
HANDLERS: dict[str, JobHandler] = {}


def handler(job_type: str):
    """Decorator to register a job_type -> async handler mapping."""
    def _wrap(fn: JobHandler):
        HANDLERS[job_type] = fn
        return fn
    return _wrap


# ------------------------------------------------------- example handlers
# Replace/extend these with real business logic. Unknown job_type -> error,
# which correctly drives the job through the retry -> DLQ path.

@handler("noop")
async def _noop(payload: dict) -> dict:
    return {"ok": True}


@handler("sleep")
async def _sleep(payload: dict) -> dict:
    await asyncio.sleep(payload.get("seconds", 1))
    return {"slept": payload.get("seconds", 1)}


@handler("http_request")
async def _http_request(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=payload.get("timeout", 10)) as client:
        resp = await client.request(payload.get("method", "GET"), payload["url"])
        resp.raise_for_status()
        return {"status_code": resp.status_code}


@handler("fail_always")
async def _fail_always(payload: dict) -> dict:
    raise RuntimeError("Simulated permanent failure (for testing DLQ path)")


class Worker:
    def __init__(self, base_url: str, api_key: str, project_id: int, queues: list[str], concurrency: int):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.project_id = project_id
        self.queues = queues
        self.concurrency = concurrency
        self.client = httpx.AsyncClient(base_url=self.base_url, headers={"X-API-Key": api_key}, timeout=30)
        self.worker_id: int | None = None
        self.semaphore = asyncio.Semaphore(concurrency)
        self.active_jobs = 0
        self.draining = False
        self._stop = asyncio.Event()

    async def register(self):
        resp = await self.client.post(
            f"/projects/{self.project_id}/workers/register",
            json={
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "label": f"{socket.gethostname()}-{os.getpid()}",
                "concurrency_capacity": self.concurrency,
                "queues": self.queues,
            },
        )
        resp.raise_for_status()
        self.worker_id = resp.json()["id"]
        logger.info("registered as worker_id=%s queues=%s concurrency=%d", self.worker_id, self.queues, self.concurrency)

    async def heartbeat_loop(self, interval: float):
        while not self._stop.is_set():
            try:
                await self.client.post(
                    f"/projects/{self.project_id}/workers/{self.worker_id}/heartbeat",
                    json={"active_jobs": self.active_jobs},
                )
            except Exception:
                logger.warning("heartbeat failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def claim_loop(self, poll_interval: float):
        while not self._stop.is_set() and not self.draining:
            available = self.concurrency - self.active_jobs
            if available <= 0:
                await asyncio.sleep(poll_interval)
                continue
            try:
                resp = await self.client.post(
                    f"/projects/{self.project_id}/workers/{self.worker_id}/claim",
                    json={"queue_names": self.queues, "max_jobs": available},
                )
                resp.raise_for_status()
                jobs = resp.json()
            except Exception:
                logger.warning("claim failed", exc_info=True)
                jobs = []

            for job in jobs:
                self.active_jobs += 1
                asyncio.create_task(self._run_job(job))

            if not jobs:
                await asyncio.sleep(poll_interval)

    async def _run_job(self, job: dict):
        job_id = job["id"]
        async with self.semaphore:
            try:
                await self.client.post(
                    f"/projects/{self.project_id}/jobs/{job_id}/start",
                    params={"worker_id": self.worker_id},
                )
                fn = HANDLERS.get(job["job_type"])
                if fn is None:
                    raise ValueError(f"No handler registered for job_type={job['job_type']!r}")

                start = time.monotonic()
                result = await fn(job["payload"])
                logger.info("job %s (%s) succeeded in %.2fs", job_id, job["job_type"], time.monotonic() - start)

                await self.client.post(
                    f"/projects/{self.project_id}/jobs/{job_id}/result",
                    params={"worker_id": self.worker_id},
                    json={"success": True, "result": result},
                )
            except Exception as exc:
                logger.error("job %s (%s) failed: %s", job_id, job.get("job_type"), exc)
                try:
                    await self.client.post(
                        f"/projects/{self.project_id}/jobs/{job_id}/result",
                        params={"worker_id": self.worker_id},
                        json={
                            "success": False,
                            "error_message": str(exc),
                            "error_stacktrace": traceback.format_exc(),
                        },
                    )
                except Exception:
                    logger.error("failed to report failure for job %s -- lease reaper will requeue it", job_id, exc_info=True)
            finally:
                self.active_jobs -= 1

    async def drain(self, timeout: float = 30.0):
        self.draining = True
        if self.worker_id:
            try:
                await self.client.post(f"/projects/{self.project_id}/workers/{self.worker_id}/drain")
            except Exception:
                pass
        logger.info("draining: waiting up to %.0fs for %d in-flight job(s)", timeout, self.active_jobs)
        deadline = time.monotonic() + timeout
        while self.active_jobs > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
        self._stop.set()
        if self.worker_id:
            try:
                await self.client.post(f"/projects/{self.project_id}/workers/{self.worker_id}/deregister")
            except Exception:
                pass
        await self.client.aclose()
        logger.info("shutdown complete")

    async def run(self):
        await self.register()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.drain()))
        await asyncio.gather(
            self.heartbeat_loop(interval=5.0),
            self.claim_loop(poll_interval=1.0),
        )


def main():
    parser = argparse.ArgumentParser(description="Distributed job scheduler worker")
    parser.add_argument("--base-url", default=os.environ.get("SCHEDULER_API_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--queues", required=True, help="comma-separated queue names")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    worker = Worker(
        base_url=args.base_url,
        api_key=args.api_key,
        project_id=args.project_id,
        queues=[q.strip() for q in args.queues.split(",")],
        concurrency=args.concurrency,
    )
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
