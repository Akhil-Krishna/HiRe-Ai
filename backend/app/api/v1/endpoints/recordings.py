import os
import logging
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole
from app.models.interview import Interview, InterviewInterviewer
from app.schemas import RecordingUploadResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recordings", tags=["recordings"])

# Recordings stored in this directory (created on startup by main.py)
RECORDINGS_DIR = Path("recordings")
MAX_RECORDING_BYTES = 500 * 1024 * 1024  # 500 MB
ALLOWED_MIME = {"video/webm", "video/mp4", "video/x-matroska", "application/octet-stream"}


async def _get_interview_by_token(token: str, db: AsyncSession) -> Interview:
    res = await db.execute(
        select(Interview)
        .options(
            selectinload(Interview.interviewers).selectinload(InterviewInterviewer.interviewer),
            selectinload(Interview.candidate),
            selectinload(Interview.hr),
        )
        .where(Interview.access_token == token)
    )
    iv = res.scalar_one_or_none()
    if not iv:
        raise HTTPException(status_code=404, detail="Interview not found")
    return iv


def _check_download_access(interview: Interview, user: User) -> None:
    """HR/Admin/assigned Interviewers/the Candidate can download."""
    if user.role == UserRole.ADMIN:
        return
    if user.role == UserRole.HR:
        interview_org = interview.hr.organisation_id if interview.hr else None
        if interview_org and user.organisation_id == interview_org:
            return
        raise HTTPException(status_code=403, detail="Access denied")
    if user.role == UserRole.CANDIDATE and interview.candidate_id == user.id:
        return
    if user.role == UserRole.INTERVIEWER:
        if any(ii.interviewer_id == user.id for ii in interview.interviewers):
            return
    raise HTTPException(status_code=403, detail="Access denied")


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload/{interview_token}", response_model=RecordingUploadResponse)
async def upload_recording(
    interview_token: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload an interview recording (WebM/MP4 blob from browser MediaRecorder).
    Only the candidate who owns the interview may upload.
    """
    interview = await _get_interview_by_token(interview_token, db)

    # Only the candidate who sat the interview can upload
    if current_user.id != interview.candidate_id:
        raise HTTPException(status_code=403, detail="Only the interview candidate can upload recordings")

    # Content-type check (browser may send application/octet-stream)
    ct = (file.content_type or "").lower()
    filename_lower = (file.filename or "").lower()
    if ct not in ALLOWED_MIME and not any(filename_lower.endswith(ext) for ext in (".webm", ".mp4", ".mkv")):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Accepted: webm, mp4"
        )

    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # Build a deterministic safe filename
    ext = ".webm" if "webm" in (ct + filename_lower) else ".mp4"
    safe_name = f"interview_{interview.id}{ext}"
    dest = RECORDINGS_DIR / safe_name

    # Stream to disk with size guard
    size = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1 MB chunks
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RECORDING_BYTES:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="Recording exceeds 500 MB limit")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Recording upload failed for %s: %s", interview_token, e)
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to save recording")

    # Update Interview record
    interview.recording_url = str(dest)
    interview.recording_size_bytes = size
    await db.flush()

    logger.info("Recording saved for interview %s — %.1f MB", interview.id, size / 1024 / 1024)

    return RecordingUploadResponse(
        success=True,
        recording_url=str(dest),
        size_bytes=size,
        message=f"Recording saved ({size // 1024 // 1024:.1f} MB)",
    )


# ── Download ──────────────────────────────────────────────────────────────────

@router.get("/download/{interview_token}")
async def download_recording(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Download/stream a recording. Supports HTTP Range requests (video player).
    Access: HR / Admin / assigned Interviewers / the Candidate.
    """
    interview = await _get_interview_by_token(interview_token, db)
    _check_download_access(interview, current_user)

    if not interview.recording_url:
        raise HTTPException(status_code=404, detail="No recording available for this interview")

    rec_path = Path(interview.recording_url)
    if not rec_path.exists():
        raise HTTPException(status_code=404, detail="Recording file not found on server")

    media_type = "video/webm" if rec_path.suffix.lower() == ".webm" else "video/mp4"
    filename = f"interview_{interview_token[:8]}{rec_path.suffix.lower()}"

    return FileResponse(
        path=str(rec_path),
        media_type=media_type,
        filename=filename,
        headers={"Accept-Ranges": "bytes"},
    )


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/{interview_token}", status_code=204)
async def delete_recording(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a recording. HR / Admin only."""
    if current_user.role not in (UserRole.ADMIN, UserRole.HR):
        raise HTTPException(status_code=403, detail="Only HR or Admin can delete recordings")

    interview = await _get_interview_by_token(interview_token, db)
    if current_user.role == UserRole.HR:
        interview_org = interview.hr.organisation_id if interview.hr else None
        if not interview_org or interview_org != current_user.organisation_id:
            raise HTTPException(status_code=403, detail="Access denied")

    if interview.recording_url:
        try:
            Path(interview.recording_url).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Could not delete recording file: %s", e)

    interview.recording_url = None
    interview.recording_size_bytes = None
    interview.recording_duration_seconds = None
    await db.flush()
