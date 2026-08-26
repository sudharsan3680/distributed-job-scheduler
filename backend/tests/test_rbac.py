import pytest
from sqlalchemy import select

from app.models import OrganizationMember, OrgRole, User

pytestmark = pytest.mark.asyncio


async def test_viewer_cannot_create_queue_but_owner_can(client, auth_headers, project, db_session):
    """RBAC: a VIEWER member of the project's org must be rejected (403) from
    a mutating route, while the OWNER succeeds. Exercises require_project_role
    which was previously defined-but-unwired."""
    project_id = project["id"]

    # Register a second user (this creates their own org 2) and then inject
    # them as a VIEWER into org 1 (the project's org) directly via the test DB,
    # mirroring what an org-admin invite flow would do.
    resp_b = await client.post("/auth/register", json={
        "email": "viewer@example.com", "password": "supersecret1",
        "full_name": "Viewer User", "organization_name": "OtherCo",
    })
    assert resp_b.status_code == 201
    viewer_id = resp_b.json()["user"]["id"]
    viewer_token = resp_b.json()["access_token"]

    # First org belongs to the fixture user (see conftest: org id 1).
    async with db_session() as db:
        db.add(OrganizationMember(organization_id=1, user_id=viewer_id, role=OrgRole.VIEWER))
        await db.commit()

    viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

    # VIEWER -> 403
    r = await client.post(
        f"/projects/{project_id}/queues", headers=viewer_headers,
        json={"name": "blocked", "max_concurrency": 1},
    )
    assert r.status_code == 403, r.text

    # OWNER -> 201
    r = await client.post(
        f"/projects/{project_id}/queues", headers=auth_headers,
        json={"name": "allowed", "max_concurrency": 1},
    )
    assert r.status_code == 201


async def test_non_member_gets_403(client, auth_headers, project):
    """A user with no membership in the project's org cannot even read it."""
    # Register a third user who is not a member of org 1.
    resp = await client.post("/auth/register", json={
        "email": "outsider@example.com", "password": "supersecret1",
        "full_name": "Outsider", "organization_name": "Solo",
    })
    outsider_token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {outsider_token}"}

    r = await client.get(f"/projects/{project['id']}/queues", headers=headers)
    assert r.status_code == 403
