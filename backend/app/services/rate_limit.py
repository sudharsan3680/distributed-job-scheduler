"""
Token-bucket rate limiter for the public API.

Honest limitation: this is in-process memory, so it only rate-limits
correctly against a single API instance. Behind a multi-instance deployment
you'd back this with Redis (INCR + EXPIRE, or a Lua-scripted bucket) instead
-- the interface below is deliberately narrow so swapping the backend later
is a one-file change. Documented as a known gap in docs/DESIGN_DECISIONS.md
rather than silently pretending this scales.
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class RateLimiter:
    def __init__(self, requests_per_minute: int):
        self.capacity = requests_per_minute
        self.refill_rate = requests_per_minute / 60.0  # tokens per second
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(tokens=self.capacity))

    def allow(self, key: str) -> tuple[bool, int]:
        bucket = self._buckets[key]
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_rate)
        bucket.last_refill = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return True, int(bucket.tokens)
        return False, 0


rate_limiter = RateLimiter(settings.rate_limit_requests_per_minute)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Key by API key header if present, else client IP. Health checks
        # and docs are exempt so the dashboard chrome never gets throttled.
        if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        key = request.headers.get("x-api-key") or request.headers.get("authorization") or (
            request.client.host if request.client else "unknown"
        )
        allowed, remaining = rate_limiter.allow(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": {"code": 429, "message": "Rate limit exceeded. Try again shortly."}},
                headers={"Retry-After": "1"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
