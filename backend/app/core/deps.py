import hashlib

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Organization, OrganizationMember, OrgRole, Project, User,
)
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

# Role hierarchy: an action requiring VIEWER is satisfied by MEMBER/ADMIN/OWNER too.
_ROLE_RANK = {OrgRole.VIEWER: 0, OrgRole.MEMBER: 1, OrgRole.ADMIN: 2, OrgRole.OWNER: 3}


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    payload = decode_access_token(creds.credentials)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = (await db.execute(select(User).where(User.id == int(payload["sub"])))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_org_role(min_role: OrgRole):
    """Dependency factory: use as a route dependency, expects `organization_id`
    path/query param to be present on the request, or pass explicitly."""

    async def _check(
        organization_id: int,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> OrganizationMember:
        membership = (
            await db.execute(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == organization_id,
                    OrganizationMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not membership:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this organization")
        if _ROLE_RANK[membership.role] < _ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role >= {min_role.value}")
        return membership

    return _check


async def get_project_for_user(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    membership = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == project.organization_id,
                OrganizationMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not membership:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this project")
    return project


def require_project_role(min_role: OrgRole):
    """RBAC dependency for project-scoped routes. Resolves the project from
    the `{project_id}` path param, then checks the caller's role within that
    project's organization. A VIEWER hitting a mutating route gets 403.

    Usage:
        project: Project = Depends(require_project_role(OrgRole.MEMBER))
    """

    async def _check(
        project_id: int,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Project:
        project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one_or_none()
        if not project:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        membership = (
            await db.execute(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == project.organization_id,
                    OrganizationMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if not membership:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No access to this project")
        if _ROLE_RANK[membership.role] < _ROLE_RANK[min_role]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Action requires role >= {min_role.value}; your role is {membership.role.value}",
            )
        return project

    return _check


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def get_project_by_api_key(
    project_id: int,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """
    Machine-to-machine auth for the worker fleet: workers authenticate with
    a project-scoped API key (not a user JWT) so a worker process never
    needs a human's login credentials. `project_id` in the path must match
    the key's project, which also prevents a leaked key for project A being
    used to claim jobs from project B's path.
    """
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    key_hash = hash_api_key(x_api_key)
    project = (await db.execute(select(Project).where(Project.api_key_hash == key_hash))).scalar_one_or_none()
    if not project or project.id != project_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key for this project")
    return project
