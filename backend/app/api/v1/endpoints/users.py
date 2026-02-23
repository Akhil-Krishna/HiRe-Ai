from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.deps import get_current_user, require_admin, require_hr
from app.models.user import User, UserRole, Organisation
from app.schemas import UserOut, UserCreate, UserUpdate, OrgCreate, OrgOut
from app.core.security import get_password_hash
from typing import List, Optional

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


# /interviewers MUST come before /{user_id} to avoid route shadowing
@router.get("/interviewers", response_model=List[UserOut])
async def list_interviewers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Any authenticated user: list active interviewers. HR sees only own org."""
    query = (
        select(User)
        .options(selectinload(User.organisation))
        .where(User.role == UserRole.INTERVIEWER, User.is_active == True)
    )
    if current_user.role == UserRole.HR and current_user.organisation_id:
        query = query.where(User.organisation_id == current_user.organisation_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/", response_model=List[UserOut])
async def list_users(
    role: Optional[UserRole] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    query = select(User).options(selectinload(User.organisation))
    if role:
        query = query.where(User.role == role)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already exists")

    # Assign requesting HR's org if not specified
    org_id = payload.organisation_id or current_user.organisation_id

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
        organisation_id=org_id,
    )
    db.add(user)
    await db.flush()
    result2 = await db.execute(
        select(User).options(selectinload(User.organisation)).where(User.id == user.id)
    )
    return result2.scalar_one()


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_active is not None:
        user.is_active = payload.is_active

    await db.flush()
    await db.refresh(user)
    return user


# ── Organisation endpoints ────────────────────────────────────────────────────

org_router = APIRouter(prefix="/organisations", tags=["organisations"])


@org_router.get("/", response_model=List[OrgOut])
async def list_orgs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(select(Organisation))
    return result.scalars().all()


@org_router.post("/", response_model=OrgOut, status_code=201)
async def create_org(
    payload: OrgCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    existing = await db.execute(select(Organisation).where(Organisation.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Organisation already exists")
    org = Organisation(name=payload.name, domain=payload.domain)
    db.add(org)
    await db.flush()
    await db.refresh(org)
    return org
