from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import verify_password, get_password_hash, create_access_token
from app.core.config import settings
from app.models.user import User, UserRole
from app.schemas import Token, LoginRequest, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


@router.post("/login", response_model=Token)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    # Must eagerly load 'organisation' — lazy loading fails inside async pydantic serialization
    result = await db.execute(
        select(User)
        .options(selectinload(User.organisation))
        .where(User.email == payload.email)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    token = create_access_token({"sub": user.id})
    response.set_cookie(
        key="access_token", value=token,
        httponly=True, max_age=_COOKIE_MAX,
        samesite="lax", secure=not settings.DEBUG,
    )
    return Token(access_token=token, user=UserOut.model_validate(user))


@router.post("/register", response_model=UserOut, status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    """Open self-registration. Restricted to candidate role for safety."""
    if payload.role != UserRole.CANDIDATE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Self-registration is allowed only for candidate accounts",
        )
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.flush()
    # Reload with organisation (None for new users, but needed for schema)
    result = await db.execute(
        select(User)
        .options(selectinload(User.organisation))
        .where(User.id == user.id)
    )
    user = result.scalar_one()
    return UserOut.model_validate(user)


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out"}
