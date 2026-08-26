import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import dispose_engine
from app.routers import auth, dashboard, jobs, projects, queues, workers, ws
from app.services.rate_limit import RateLimitMiddleware
from app.services.scheduler import scheduler_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_stop_event = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scheduler_loop(_stop_event))
    yield
    _stop_event.set()
    await task
    await dispose_engine()


app = FastAPI(
    title="Distributed Job Scheduler API",
    version="1.0.0",
    description="Production-inspired async job scheduling platform: projects, queues, jobs, workers, retries, DLQ.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # The dashboard authenticates with a Bearer token in the Authorization
    # header (never cookies), so we do NOT need credentials support -- and
    # combining credentials=True with a wildcard origin is rejected by
    # browsers and would silently break CORS. Left as False on purpose.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)


# --------------------------------------------------- structured errors ---

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.status_code, "message": exc.detail}})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": 422, "message": "Validation failed", "details": exc.errors()}},
    )


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(queues.router)
app.include_router(jobs.router)
app.include_router(workers.router)
app.include_router(dashboard.router)
app.include_router(ws.router)
