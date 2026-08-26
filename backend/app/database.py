from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle_seconds,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            # Explicit rollback rather than relying on session.close()'s
            # implicit rollback-on-close: makes the failure path obvious to
            # a reader, and guarantees a clean transaction state before the
            # connection is returned to the pool even if a future
            # SQLAlchemy version changes close()'s implicit behavior.
            await session.rollback()
            raise
        finally:
            await session.close()


async def dispose_engine() -> None:
    """Release the pool's connections. Called from the FastAPI lifespan
    shutdown (app/main.py) so the process doesn't hold Postgres connections
    open after the app has stopped serving requests."""
    await engine.dispose()
