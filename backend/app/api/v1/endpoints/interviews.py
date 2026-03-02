
import base64

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional

from app.core.database import get_db
from app.core.deps import get_current_user, require_hr
from app.core.config import settings
from app.core.task_runner import enqueue_task_with_fallback, run_task_with_fallback
from app.models.user import User, UserRole
from app.models.interview import Interview, InterviewInterviewer, InterviewStatus
from app.schemas import InterviewCreate, InterviewWithInterviewers
from app.services.email_service import send_interview_invite_sync, send_interviewer_notification_sync
from app.services.idempotency_service import check_idempotency, store_idempotency_response
from app.services.resume_service import extract_resume_text
from app.tasks.email_tasks import send_interview_invite_task, send_interviewer_notification_task
from app.tasks.resume_tasks import extract_resume_text_task
from pathlib import Path

router = APIRouter(prefix="/interviews", tags=["interviews"])


async def _queue_interviewer_notification(payload: dict) -> None:
    await enqueue_task_with_fallback(
        send_interviewer_notification_task,
        payload=payload,
        fallback_callable=lambda: send_interviewer_notification_sync(**payload),
        endpoint_name="/interviews:schedule:interviewer-email",
    )


async def _queue_candidate_invite(payload: dict) -> None:
    await enqueue_task_with_fallback(
        send_interview_invite_task,
        payload=payload,
        fallback_callable=lambda: send_interview_invite_sync(**payload),
        endpoint_name="/interviews:schedule:candidate-email",
    )


def _eager_opts():
    """Eager-load ALL nested relationships to prevent MissingGreenlet errors."""
    return [
        selectinload(Interview.hr).selectinload(User.organisation),
        selectinload(Interview.candidate).selectinload(User.organisation),
        selectinload(Interview.interviewers)
            .selectinload(InterviewInterviewer.interviewer)
            .selectinload(User.organisation),
    ]


async def _load_interview(interview_id: str, db: AsyncSession) -> Interview:
    res = await db.execute(
        select(Interview).options(*_eager_opts()).where(Interview.id == interview_id)
    )
    iv = res.scalar_one_or_none()
    if not iv:
        raise HTTPException(404, "Interview not found")
    return iv


async def _load_by_token(token: str, db: AsyncSession) -> Interview:
    res = await db.execute(
        select(Interview).options(*_eager_opts()).where(Interview.access_token == token)
    )
    iv = res.scalar_one_or_none()
    if not iv:
        raise HTTPException(404, "Interview not found")
    return iv


def _to_schema(iv: Interview, temp_password: Optional[str] = None) -> InterviewWithInterviewers:
    obj = InterviewWithInterviewers.model_validate(iv)
    obj.has_recording = bool(iv.recording_url)
    obj.temp_password = temp_password
    return obj


@router.post("/", response_model=InterviewWithInterviewers, status_code=201)
async def schedule_interview(
    payload: InterviewCreate,
    background_tasks: BackgroundTasks,
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_hr),
):
    idem_payload = {
        "title": payload.title,
        "job_role": payload.job_role,
        "description": payload.description,
        "candidate_email": payload.candidate_email,
        "scheduled_at": payload.scheduled_at.isoformat(),
        "duration_minutes": payload.duration_minutes,
        "interviewer_ids": sorted(payload.interviewer_ids),
    }
    idem_record, idem_response = await check_idempotency(
        db,
        scope="interviews.schedule",
        key=x_idempotency_key,
        payload=idem_payload,
    )
    if idem_response is not None:
        return InterviewWithInterviewers.model_validate(idem_response)

    # ── Find or auto-create candidate ──────────────────────────────────────────
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
    elif candidate.role != UserRole.CANDIDATE:
        raise HTTPException(
            status_code=400,
            detail="Candidate email belongs to a non-candidate account",
        )

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

    # ── Assign interviewers — with org + domain validation ─────────────────────
    hr_domain = current_user.email.split("@")[1].lower()

    for interviewer_id in payload.interviewer_ids:
        # Load interviewer with org relationship
        r = await db.execute(
            select(User)
            .options(selectinload(User.organisation))
            .where(User.id == interviewer_id, User.role == UserRole.INTERVIEWER)
        )
        interviewer = r.scalar_one_or_none()
        if not interviewer:
            continue  # skip unknown IDs silently

        # FIX 5 — Organisation must match
        if (
            current_user.organisation_id
            and interviewer.organisation_id != current_user.organisation_id
        ):
            raise HTTPException(
                400,
                f"Interviewer {interviewer.email} does not belong to your organisation. "
                f"Only interviewers in the same organisation can be assigned.",
            )

        # FIX 5 — Email domain must match
        interviewer_domain = interviewer.email.split("@")[1].lower()
        if interviewer_domain != hr_domain:
            raise HTTPException(
                400,
                f"Interviewer {interviewer.email} has a different email domain "
                f"(@{interviewer_domain}) than yours (@{hr_domain}). "
                f"Only same-domain interviewers can be assigned.",
            )

        db.add(InterviewInterviewer(
            interview_id=interview.id,
            interviewer_id=interviewer_id,
        ))
        background_tasks.add_task(
            _queue_interviewer_notification,
            {
                "interviewer_email": interviewer.email,
                "interviewer_name": interviewer.full_name,
                "interview_title": interview.title,
                "scheduled_at": interview.scheduled_at.isoformat(),
                "dashboard_link": f"{settings.FRONTEND_URL}/",
            },
        )

    await db.flush()

    interview_link = f"{settings.FRONTEND_URL}/interview/{interview.access_token}"
    background_tasks.add_task(
        _queue_candidate_invite,
        {
            "candidate_email": candidate.email,
            "candidate_name": candidate.full_name,
            "interview_title": interview.title,
            "scheduled_at": interview.scheduled_at.isoformat(),
            "interview_link": interview_link,
            "temp_password": temp_password,
        },
    )

    loaded = await _load_interview(interview.id, db)
    response = _to_schema(loaded, temp_password)
    await store_idempotency_response(db, idem_record, response.model_dump(mode="json"))
    return response


@router.post("/{interview_id}/resume")
async def upload_resume(
    interview_id: str,
    file: UploadFile = File(...),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_hr),
):
    """Upload a candidate resume for AI context."""
    iv = await _load_interview(interview_id, db)
    _check_access(iv, current_user)

    uploads_dir = Path(settings.UPLOADS_DIR) / "resumes"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "resume.pdf").suffix.lower()
    if ext not in (".pdf", ".doc", ".docx", ".txt"):
        raise HTTPException(400, "Unsupported file type. Use PDF, DOC, DOCX, or TXT.")

    dest = uploads_dir / f"{interview_id}{ext}"
    content = await file.read()
    idem_payload = {
        "interview_id": interview_id,
        "filename": file.filename or f"resume{ext}",
        "content_len": len(content),
    }
    idem_record, idem_response = await check_idempotency(
        db,
        scope="interviews.resume_upload",
        key=x_idempotency_key,
        payload=idem_payload,
    )
    if idem_response is not None:
        return idem_response
    dest.write_bytes(content)

    async def _fallback_extract():
        return {"text": extract_resume_text(content, filename=file.filename or f"resume{ext}")}

    extracted = await run_task_with_fallback(
        extract_resume_text_task,
        payload={
            "content_b64": base64.b64encode(content).decode("ascii"),
            "filename": file.filename or f"resume{ext}",
        },
        fallback_callable=_fallback_extract,
        endpoint_name="/interviews/{id}/resume",
    )
    resume_text = extracted.get("text", "")

    iv.resume_path = str(dest)
    iv.resume_text = resume_text[:8000] if resume_text else None
    await db.flush()

    response = {"success": True, "filename": file.filename, "has_text": bool(resume_text)}
    await store_idempotency_response(db, idem_record, response)
    return response


@router.get("/", response_model=List[InterviewWithInterviewers])
async def list_interviews(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == UserRole.ADMIN:
        # Admin sees everything
        query = select(Interview)

    elif current_user.role == UserRole.HR:
        # FIX 2 — HR sees ALL interviews belonging to their organisation,
        # not just the ones they personally created.
        # Join Interview → hr (User) and filter by that user's organisation_id.
        query = (
            select(Interview)
            .join(Interview.hr)                          # JOIN interviews → users ON hr_id
            .where(User.organisation_id == current_user.organisation_id)
        )

    elif current_user.role == UserRole.CANDIDATE:
        # Candidate sees only their own interviews
        query = select(Interview).where(Interview.candidate_id == current_user.id)

    elif current_user.role == UserRole.INTERVIEWER:
        # Interviewer sees only assigned interviews
        subq = select(InterviewInterviewer.interview_id).where(
            InterviewInterviewer.interviewer_id == current_user.id
        )
        query = select(Interview).where(Interview.id.in_(subq))

    else:
        query = select(Interview).where(Interview.id.is_(None))

    query = query.options(*_eager_opts()).order_by(Interview.scheduled_at.desc())

    result = await db.execute(query)
    # scalars().unique() prevents duplicates when join produces multiple rows
    interviews_raw = result.scalars().unique().all()
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
    _check_access(interview, current_user)
    if interview.status == InterviewStatus.COMPLETED:
        raise HTTPException(400, "Cannot cancel a completed interview")
    interview.status = InterviewStatus.CANCELLED
    await db.flush()


def _check_access(interview: Interview, user: User):
    """Fine-grained access check — mirrors _is_org_viewer in interview_session.py."""
    if user.role == UserRole.ADMIN:
        return
    if user.role == UserRole.HR:
        # HR can only access interviews in their own organisation
        if (
            interview.hr
            and interview.hr.organisation_id
            and user.organisation_id == interview.hr.organisation_id
        ):
            return
        raise HTTPException(403, "Access denied: interview belongs to a different organisation")
    if user.role == UserRole.CANDIDATE and interview.candidate_id == user.id:
        return
    if user.role == UserRole.INTERVIEWER:
        if any(ii.interviewer_id == user.id for ii in interview.interviewers):
            return
    raise HTTPException(403, "Access denied")
