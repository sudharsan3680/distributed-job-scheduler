import asyncio

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session():
    # StaticPool: a single shared connection for the whole in-memory DB, so
    # every session (and every concurrent request in the race-condition
    # test) sees the same data instead of each getting its own empty DB.
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield session_maker
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient):
    resp = await client.post("/auth/register", json={
        "email": "dev@example.com", "password": "supersecret1", "full_name": "Dev User", "organization_name": "Acme",
    })
    assert resp.status_code == 201
    return resp.json()


@pytest_asyncio.fixture
async def auth_headers(registered_user):
    return {"Authorization": f"Bearer {registered_user['access_token']}"}


@pytest_asyncio.fixture
async def project(client: AsyncClient, auth_headers, registered_user):
    # organization id 1 -- first org created by the fixture user
    resp = await client.post("/organizations/1/projects", json={"name": "Test Project", "slug": "test-project"}, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()
