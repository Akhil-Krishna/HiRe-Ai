from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Optional
from pydantic import BaseModel
import time
import asyncio
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.config import settings
from app.core.task_runner import run_task_with_fallback
from app.models.user import User, UserRole
from app.models.interview import Interview, VisionLog, InterviewInterviewer
from app.services.vision_service import analyze_frame, aggregate_vision_logs
from app.tasks.vision_tasks import analyze_vision_frame_task
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vision", tags=["vision"])


class FrameRequest(BaseModel):
    frame: str           # base64 JPEG
    interview_id: Optional[str] = None
    tab_switch_count: int = 0
    frame_seq: Optional[int] = None


_vision_counter: Dict[str, int] = {}
_vision_counter_lock = asyncio.Lock()


def _safe_degraded_result(processing_ms: float, error: str) -> dict:
    return {
        "success": False,
        "emotions": {},
        "dominant_emotion": "neutral",
        "confidence_score": 65.0,
        "engagement_score": 65.0,
        "stress_score": 20.0,
        "face_count": 1,
        "gaze_deviation": 0.0,
        "cheating_flags": [],
        "cheating_score": 0.0,
        "error": error,
        "provider": "deepface",
        "degraded": True,
        "processing_ms": processing_ms,
    }


async def _should_persist_vision_log(interview_id: str, frame_seq: Optional[int]) -> bool:
    if not settings.VISION_PERSIST_ENABLED:
        return False
    sample_n = max(1, int(settings.VISION_LOG_SAMPLE_EVERY_N))
    if frame_seq is not None:
        return int(frame_seq) % sample_n == 0
    async with _vision_counter_lock:
        next_count = _vision_counter.get(interview_id, 0) + 1
        _vision_counter[interview_id] = next_count
    return next_count % sample_n == 0


@router.post("/analyze")
async def analyze_vision_frame(
    payload: FrameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    started = time.perf_counter()

    async def _fallback():
        return await analyze_frame(payload.frame)
    infer_started = time.perf_counter()
    try:
        result = await run_task_with_fallback(
            analyze_vision_frame_task,
            payload={"frame": payload.frame},
            fallback_callable=_fallback,
            endpoint_name="/vision/analyze",
            realtime=True,
        )
    except Exception as exc:
        logger.exception("Vision inference failed", extra={"event": "vision_infer_error", "component": "vision", "error": str(exc)})
        total_ms = round((time.perf_counter() - started) * 1000.0, 1)
        return _safe_degraded_result(total_ms, "Vision analysis temporarily unavailable")
    infer_ms = round((time.perf_counter() - infer_started) * 1000.0, 1)
    if "processing_ms" not in result:
        result["processing_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    result.setdefault("provider", "deepface")
    result.setdefault("degraded", False)

    db_write_ms = 0.0
    # Persist to DB if interview_id provided (sampled for throughput)
    if payload.interview_id and await _should_persist_vision_log(payload.interview_id, payload.frame_seq):
        db_started = time.perf_counter()
        try:
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
        except Exception as exc:
            logger.warning(
                "Vision log persistence skipped",
                extra={"event": "vision_db_write_error", "component": "vision", "error": str(exc)},
            )
        db_write_ms = round((time.perf_counter() - db_started) * 1000.0, 1)

    logger.info(
        "Vision analyze user=%s provider=%s degraded=%s processing_ms=%s infer_ms=%s db_write_ms=%s face_count=%s",
        current_user.id,
        result.get("provider"),
        result.get("degraded"),
        result.get("processing_ms"),
        infer_ms,
        db_write_ms,
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
