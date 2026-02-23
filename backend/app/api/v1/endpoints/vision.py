from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.interview import Interview, VisionLog
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
    result = await analyze_frame(payload.frame)

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

    return result


@router.get("/summary/{interview_id}")
async def get_vision_summary(
    interview_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    res = await db.execute(
        select(VisionLog)
        .where(VisionLog.interview_id == interview_id)
        .order_by(VisionLog.timestamp)
    )
    logs = res.scalars().all()
    return aggregate_vision_logs(logs) if logs else {"frames_analyzed": 0}
