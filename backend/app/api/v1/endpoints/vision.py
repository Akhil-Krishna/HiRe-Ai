from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel
import time
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole
from app.models.interview import Interview, VisionLog, InterviewInterviewer
from app.services.vision_service import analyze_frame, aggregate_vision_logs
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vision", tags=["vision"])


class FrameRequest(BaseModel):
    frame: str           # base64 JPEG
    interview_id: Optional[str] = None
    tab_switch_count: int = 0


@router.post("/analyze")
async def analyze_vision_frame(
    payload: FrameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started = time.perf_counter()
    result = await analyze_frame(payload.frame)
    if "processing_ms" not in result:
        result["processing_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    result.setdefault("provider", "deepface")
    result.setdefault("degraded", False)

    # Persist to DB if interview_id provided
    if payload.interview_id:
        res = await db.execute(select(Interview).where(Interview.id == payload.interview_id))
        iv = res.scalar_one_or_none()
        if iv and iv.candidate_id == current_user.id:
            log = VisionLog(
                interview_id=iv.id,
                dominant_emotion=result.get("dominant_emotion"),
                confidence_score=result.get("confidence_score"),
                engagement_score=result.get("engagement_score"),
                stress_score=result.get("stress_score"),
                emotions_raw=result.get("emotions_raw"),
                face_count=result.get("face_count", 1),
                gaze_deviation=result.get("gaze_deviation"),
                cheating_flags=result.get("cheating_flags"),
                cheating_score=result.get("cheating_score", 0.0),
                tab_switch_count=payload.tab_switch_count,
            )
            db.add(log)
            await db.flush()

    logger.info(
        "Vision analyze user=%s provider=%s degraded=%s processing_ms=%s face_count=%s",
        current_user.id,
        result.get("provider"),
        result.get("degraded"),
        result.get("processing_ms"),
        result.get("face_count"),
    )
    return result


@router.get("/summary/{interview_id}")
async def get_vision_summary(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    iv_res = await db.execute(
        select(Interview).where(Interview.id == interview_id)
    )
    iv = iv_res.scalar_one_or_none()
    if not iv:
        raise HTTPException(status_code=404, detail="Interview not found")

    if current_user.role != UserRole.ADMIN:
        if current_user.role == UserRole.CANDIDATE and iv.candidate_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        elif current_user.role == UserRole.HR:
            if iv.organisation_id and iv.organisation_id != current_user.organisation_id:
                raise HTTPException(status_code=403, detail="Access denied")
        elif current_user.role == UserRole.INTERVIEWER:
            ares = await db.execute(
                select(InterviewInterviewer.id).where(
                    InterviewInterviewer.interview_id == interview_id,
                    InterviewInterviewer.interviewer_id == current_user.id,
                )
            )
            if not ares.scalar_one_or_none():
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    res = await db.execute(
        select(VisionLog)
        .where(VisionLog.interview_id == interview_id)
        .order_by(VisionLog.timestamp)
    )
    logs = res.scalars().all()
    return aggregate_vision_logs(logs) if logs else {"frames_analyzed": 0}
