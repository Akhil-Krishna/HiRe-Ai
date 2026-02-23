import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, DateTime, Enum as SAEnum, ForeignKey, Text, Float, Boolean, JSON, Integer, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
import enum


class InterviewStatus(str, enum.Enum):
    SCHEDULED   = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    CANCELLED   = "cancelled"


class Interview(Base):
    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String, nullable=False)
    job_role: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    organisation_id: Mapped[Optional[str]] = mapped_column(ForeignKey("organisations.id"), nullable=True, index=True)

    hr_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)

    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[InterviewStatus] = mapped_column(
        SAEnum(InterviewStatus), default=InterviewStatus.SCHEDULED, nullable=False
    )
    access_token: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, default=lambda: str(uuid.uuid4())
    )

    # Question bank stored as JSON list
    question_bank: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Resume file path (uploaded at scheduling time)
    resume_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resume_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # extracted text for AI

    # Vision always on
    enable_emotion_analysis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_cheating_detection: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Interviewer control flags
    ai_paused: Mapped[bool] = mapped_column(Boolean, default=False)  # interviewer paused AI
    manual_questions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # manual Q by interviewer

    # Scores
    answer_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    code_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    emotion_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cheating_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    integrity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    ai_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Vision detail
    emotion_scores: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    transcript: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Recording
    recording_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    recording_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    recording_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    hr: Mapped["User"] = relationship("User", foreign_keys=[hr_id], back_populates="scheduled_interviews")       # noqa
    candidate: Mapped["User"] = relationship("User", foreign_keys=[candidate_id], back_populates="candidate_interviews")  # noqa
    interviewers: Mapped[List["InterviewInterviewer"]] = relationship(
        "InterviewInterviewer", back_populates="interview", cascade="all, delete-orphan"
    )
    messages: Mapped[List["InterviewMessage"]] = relationship(
        "InterviewMessage", back_populates="interview", cascade="all, delete-orphan",
        order_by="InterviewMessage.timestamp"
    )
    vision_logs: Mapped[List["VisionLog"]] = relationship(
        "VisionLog", back_populates="interview", cascade="all, delete-orphan"
    )


class InterviewInterviewer(Base):
    __tablename__ = "interview_interviewers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), nullable=False)
    interviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    interview: Mapped["Interview"] = relationship("Interview", back_populates="interviewers")
    interviewer: Mapped["User"] = relationship("User", back_populates="interviewer_assignments")  # noqa


class InterviewMessage(Base):
    __tablename__ = "interview_messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)   # "ai" | "candidate" | "interviewer"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    code_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    interview: Mapped["Interview"] = relationship("Interview", back_populates="messages")


class VisionLog(Base):
    __tablename__ = "vision_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id"), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    dominant_emotion: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    engagement_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stress_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    emotions_raw: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    face_count: Mapped[int] = mapped_column(Integer, default=1)
    gaze_deviation: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cheating_flags: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    cheating_score: Mapped[float] = mapped_column(Float, default=0.0)
    tab_switch_count: Mapped[int] = mapped_column(Integer, default=0)
    interview: Mapped["Interview"] = relationship("Interview", back_populates="vision_logs")
