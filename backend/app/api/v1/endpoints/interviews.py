import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional

from app.core.database import get_db
from app.core.deps import get_current_user, require_hr
from app.core.config import settings
from app.models.user import User, UserRole, Organisation
from app.models.interview import Interview, InterviewInterviewer, InterviewStatus
from app.schemas import InterviewCreate, InterviewWithInterviewers
from app.services.email_service import send_interview_invite_sync, send_interviewer_notification_sync
from pathlib import Path

router = APIRouter(prefix="/interviews", tags=["interviews"])


def _eager_opts():
    """Eager-load ALL nested relationships to prevent MissingGreenlet errors."""
    return [
        selectinload(Interview.hr).selectinload(User.organisation),
        selectinload(Interview.candidate).selectinload(User.organisation),
        selectinload(Interview.interviewers).selectinload(InterviewInterviewer.interviewer).selectinload(User.organisation),
    ]


async def _load_interview(interview_id: str, db: AsyncSession) -> Interview:
    res = await db.execute(
        select(Interview)
        .options(*_eager_opts())
        .where(Interview.id == interview_id)
    )
    iv = res.scalar_one_or_none()
    if not iv:
        raise HTTPException(404, "Interview not found")
    return iv


async def _load_by_token(token: str, db: AsyncSession) -> Interview:
    res = await db.execute(
        select(Interview)
        .options(*_eager_opts())
        .where(Interview.access_token == token)
    )
    iv = res.scalar_one_or_none()
    if not iv:
        raise HTTPException(404, "Interview not found")
    return iv


def _to_schema(iv: Interview, temp_password: str = None) -> InterviewWithInterviewers:
    obj = InterviewWithInterviewers.model_validate(iv)
    obj.has_recording = bool(iv.recording_url)
    obj.temp_password = temp_password
    return obj


@router.post("/", response_model=InterviewWithInterviewers, status_code=201)
async def schedule_interview(
    payload: InterviewCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_hr),
):
    # Find or auto-create candidate
    result = await db.execute(select(User).where(User.email == payload.candidate_email))
    candidate = result.scalar_one_or_none()

    temp_password = None
    if not candidate:
        import secrets
        from app.core.security import get_password_hash
        temp_password = secrets.token_urlsafe(8)
        candidate = User(
            email=payload.candidate_email,
            full_name=payload.candidate_email.split("@")[0].replace(".", " ").title(),
            hashed_password=get_password_hash(temp_password),
            role=UserRole.CANDIDATE,
        )
        db.add(candidate)
        await db.flush()

    interview = Interview(
        title=payload.title,
        job_role=payload.job_role,
        description=payload.description,
        hr_id=current_user.id,
        candidate_id=candidate.id,
        organisation_id=current_user.organisation_id,
        scheduled_at=payload.scheduled_at,
        duration_minutes=payload.duration_minutes,
        enable_emotion_analysis=payload.enable_emotion_analysis,
        enable_cheating_detection=payload.enable_cheating_detection,
        question_bank=payload.question_bank,
    )
    db.add(interview)
    await db.flush()

    # Assign interviewers — org-scoped
    for interviewer_id in payload.interviewer_ids:
        query = select(User).where(User.id == interviewer_id, User.role == UserRole.INTERVIEWER)
        if current_user.organisation_id:
            query = query.where(User.organisation_id == current_user.organisation_id)
        r = await db.execute(query)
        interviewer = r.scalar_one_or_none()
        if interviewer:
            db.add(InterviewInterviewer(interview_id=interview.id, interviewer_id=interviewer_id))
            background_tasks.add_task(
                send_interviewer_notification_sync,
                interviewer_email=interviewer.email,
                interviewer_name=interviewer.full_name,
                interview_title=interview.title,
                scheduled_at=interview.scheduled_at.strftime("%Y-%m-%d %H:%M UTC"),
                dashboard_link=f"{settings.FRONTEND_URL}/",
            )

    await db.flush()

    interview_link = f"{settings.FRONTEND_URL}/interview/{interview.access_token}"
    background_tasks.add_task(
        send_interview_invite_sync,
        candidate_email=candidate.email,
        candidate_name=candidate.full_name,
        interview_title=interview.title,
        scheduled_at=interview.scheduled_at.strftime("%Y-%m-%d %H:%M UTC"),
        interview_link=interview_link,
        temp_password=temp_password,
    )

    loaded = await _load_interview(interview.id, db)
    return _to_schema(loaded, temp_password)


@router.post("/{interview_id}/resume")
async def upload_resume(
    interview_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_hr),
):
    """Upload a candidate resume for AI context."""
    iv = await _load_interview(interview_id, db)

    uploads_dir = Path(settings.UPLOADS_DIR) / "resumes"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "resume.pdf").suffix.lower()
    if ext not in (".pdf", ".doc", ".docx", ".txt"):
        raise HTTPException(400, "Unsupported file type. Use PDF, DOC, DOCX, or TXT.")

    dest = uploads_dir / f"{interview_id}{ext}"
    content = await file.read()
    dest.write_bytes(content)

    resume_text = ""
    if ext == ".txt":
        resume_text = content.decode("utf-8", errors="replace")
    elif ext == ".pdf":
        try:
            import io
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(content))
                resume_text = "\n".join(p.extract_text() or "" for p in reader.pages)
            except ImportError:
                resume_text = "[PDF text extraction requires: pip install pypdf]"
        except Exception:
            resume_text = "[Could not extract PDF text]"

    iv.resume_path = str(dest)
    iv.resume_text = resume_text[:8000] if resume_text else None
    await db.flush()

    return {"success": True, "filename": file.filename, "has_text": bool(resume_text)}


@router.get("/", response_model=List[InterviewWithInterviewers])
async def list_interviews(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.ADMIN:
        query = select(Interview)
    elif current_user.role == UserRole.HR:
        query = select(Interview).where(Interview.hr_id == current_user.id)
    elif current_user.role == UserRole.CANDIDATE:
        query = select(Interview).where(Interview.candidate_id == current_user.id)
    elif current_user.role == UserRole.INTERVIEWER:
        subq = select(InterviewInterviewer.interview_id).where(
            InterviewInterviewer.interviewer_id == current_user.id
        )
        query = select(Interview).where(Interview.id.in_(subq))
    else:
        query = select(Interview).where(Interview.id.is_(None))

    query = query.options(*_eager_opts()).order_by(Interview.scheduled_at.desc())

    result = await db.execute(query)
    interviews_raw = result.scalars().all()
    return [_to_schema(iv) for iv in interviews_raw]


@router.get("/{interview_id}", response_model=InterviewWithInterviewers)
async def get_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    interview = await _load_interview(interview_id, db)
    _check_access(interview, current_user)
    return _to_schema(interview)


@router.delete("/{interview_id}", status_code=204)
async def cancel_interview(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_hr),
):
    interview = await _load_interview(interview_id, db)
    if interview.status == InterviewStatus.COMPLETED:
        raise HTTPException(400, "Cannot cancel a completed interview")
    interview.status = InterviewStatus.CANCELLED
    await db.flush()


def _check_access(interview: Interview, user: User):
    if user.role in (UserRole.ADMIN, UserRole.HR):
        return
    if user.role == UserRole.CANDIDATE and interview.candidate_id == user.id:
        return
    if user.role == UserRole.INTERVIEWER:
        if any(ii.interviewer_id == user.id for ii in interview.interviewers):
            return
    raise HTTPException(403, "Access denied")
