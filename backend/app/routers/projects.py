import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, hash_api_key, require_org_role
from app.database import get_db
from app.models import Organization, OrganizationMember, OrgRole, Project, User
from app import schemas

router = APIRouter(prefix="/organizations/{organization_id}/projects", tags=["projects"])


async def _assert_member(db, organization_id: int, user: User):
    m = (
        await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if not m:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this organization")
    return m


@router.post("", response_model=schemas.ProjectWithApiKey, status_code=status.HTTP_201_CREATED)
async def create_project(
    organization_id: int,
    body: schemas.ProjectCreate,
    _membership=Depends(require_org_role(OrgRole.MEMBER)),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw_key = f"sk_{secrets.token_urlsafe(32)}"
    project = Project(
        organization_id=organization_id,
        name=body.name,
        slug=body.slug,
        api_key_hash=hash_api_key(raw_key),
    )
    db.add(project)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Project slug already exists in this organization")
    await db.refresh(project)
    out = schemas.ProjectWithApiKey(
        id=project.id, name=project.name, slug=project.slug, created_at=project.created_at, api_key=raw_key,
    )
    return out


@router.get("", response_model=list[schemas.ProjectOut])
async def list_projects(
    organization_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _assert_member(db, organization_id, user)
    rows = (await db.execute(select(Project).where(Project.organization_id == organization_id))).scalars().all()
    return rows
