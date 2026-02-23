import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import String, Boolean, DateTime, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    HR = "hr"
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


class Organisation(Base):
    """Organisation — HR and Interviewers belong to one org; candidates are org-scoped."""
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    domain: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. acme.com
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    members: Mapped[List["User"]] = relationship("User", back_populates="organisation")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), nullable=False, default=UserRole.CANDIDATE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Organisation membership — candidates may be org-less (external)
    organisation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("organisations.id"), nullable=True, index=True
    )
    organisation: Mapped[Optional["Organisation"]] = relationship("Organisation", back_populates="members")

    # Relationships
    scheduled_interviews: Mapped[List["Interview"]] = relationship(  # noqa: F821
        "Interview", foreign_keys="Interview.hr_id", back_populates="hr"
    )
    candidate_interviews: Mapped[List["Interview"]] = relationship(  # noqa: F821
        "Interview", foreign_keys="Interview.candidate_id", back_populates="candidate"
    )
    interviewer_assignments: Mapped[List["InterviewInterviewer"]] = relationship(  # noqa: F821
        "InterviewInterviewer", back_populates="interviewer"
    )
