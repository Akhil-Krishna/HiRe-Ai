"""
Interview Session — candidate chat, AI pause/resume, interviewer manual questions,
live message streaming, completion with vision scores.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone
from typing import List, Optional

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole
from app.models.interview import (
    Interview, InterviewMessage, InterviewInterviewer,
    InterviewStatus, VisionLog
)
from app.schemas import (
    ChatMessage, ChatResponse, MessageOut, InterviewWithInterviewers,
    CompleteInterviewRequest, EvaluationResult, ScoreBreakdown,
    InterviewerQuestion,
)
from app.services.ai_service import get_ai_response, generate_final_evaluation
from app.services.vision_service import aggregate_vision_logs

router = APIRouter(prefix="/interview-session", tags=["interview-session"])


async def _get_iv(token: str, db: AsyncSession) -> Interview:
    res = await db.execute(
        select(Interview)
        .options(
            selectinload(Interview.messages),
            selectinload(Interview.interviewers).selectinload(InterviewInterviewer.interviewer).selectinload(User.organisation),
            selectinload(Interview.candidate).selectinload(User.organisation),
            selectinload(Interview.hr).selectinload(User.organisation),
        )
        .where(Interview.access_token == token)
    )
    iv = res.scalar_one_or_none()
    if not iv:
        raise HTTPException(404, "Interview not found")
    return iv


def _is_org_viewer(iv: Interview, user: User) -> bool:
    """
    Returns True if the user is authorised to watch/monitor this interview.
    Enforces organisation-level isolation — HR can only view interviews
    that belong to their own organisation.
    """
    if user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.HR:
        # HR is only allowed to monitor interviews inside their own organisation.
        # iv.hr is the HR who created the interview; they share an organisation_id.
        return (
            iv.hr is not None
            and iv.hr.organisation_id is not None
            and user.organisation_id == iv.hr.organisation_id
        )
    if user.role == UserRole.INTERVIEWER:
        # Interviewer must be explicitly assigned to this interview.
        return any(ii.interviewer_id == user.id for ii in iv.interviewers)
    return False


def _can_join_interview(iv: Interview, user: User) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.CANDIDATE:
        return iv.candidate_id == user.id
    return _is_org_viewer(iv, user)


@router.get("/join/{interview_token}", response_model=InterviewWithInterviewers)
async def join_interview(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    iv = await _get_iv(interview_token, db)
    if not _can_join_interview(iv, current_user):
        raise HTTPException(403, "Access denied")
    if iv.status == InterviewStatus.CANCELLED:
        raise HTTPException(400, "Interview cancelled")
    iv.has_recording = bool(iv.recording_url)
    return iv


@router.post("/start/{interview_token}")
async def start_interview(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    iv = await _get_iv(interview_token, db)
    if iv.candidate_id != current_user.id:
        raise HTTPException(403, "Only the candidate can start this interview")
    if iv.status == InterviewStatus.COMPLETED:
        raise HTTPException(400, "Interview already completed")
    if iv.status == InterviewStatus.IN_PROGRESS:
        msgs = sorted(iv.messages, key=lambda m: m.timestamp)
        return {"status": "resumed", "messages": [MessageOut.model_validate(m) for m in msgs], "ai_paused": iv.ai_paused}

    iv.status = InterviewStatus.IN_PROGRESS
    iv.started_at = datetime.now(timezone.utc)
    await db.flush()

    ai_text, _ = await get_ai_response(interview=iv, messages=[], candidate_message="[START INTERVIEW]")
    ai_msg = InterviewMessage(interview_id=iv.id, role="ai", content=ai_text)
    db.add(ai_msg)
    await db.flush()
    await db.refresh(ai_msg)

    return {"status": "started", "messages": [MessageOut.model_validate(ai_msg)], "ai_paused": False}


@router.post("/chat/{interview_token}", response_model=ChatResponse)
async def chat(
    interview_token: str,
    payload: ChatMessage,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    iv = await _get_iv(interview_token, db)
    if iv.candidate_id != current_user.id:
        raise HTTPException(403, "Only candidate can send messages")
    if iv.status != InterviewStatus.IN_PROGRESS:
        raise HTTPException(400, "Interview not in progress")

    # Save candidate message
    db.add(InterviewMessage(
        interview_id=iv.id, role="candidate",
        content=payload.content, code_snippet=payload.code_snippet,
    ))
    await db.flush()

    # If AI is paused by interviewer — acknowledge but don't call AI
    if iv.ai_paused:
        return ChatResponse(message="", is_complete=False, ai_paused=True)

    msgs = sorted(iv.messages, key=lambda m: m.timestamp)
    ai_text, is_complete = await get_ai_response(
        interview=iv, messages=msgs,
        candidate_message=payload.content,
        code_snippet=payload.code_snippet,
    )
    db.add(InterviewMessage(interview_id=iv.id, role="ai", content=ai_text))
    await db.flush()

    return ChatResponse(message=ai_text, is_complete=is_complete, ai_paused=False)


@router.get("/messages/{interview_token}", response_model=List[MessageOut])
async def get_messages(
    interview_token: str,
    since_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all messages (or messages since a given message ID for polling).
    Used by interviewers to see live chat without refreshing.
    """
    iv = await _get_iv(interview_token, db)
    is_candidate = iv.candidate_id == current_user.id
    if not (is_candidate or _is_org_viewer(iv, current_user)):
        raise HTTPException(403, "Access denied")

    msgs = sorted(iv.messages, key=lambda m: m.timestamp)
    if since_id:
        # Return only messages after since_id
        ids = [m.id for m in msgs]
        if since_id in ids:
            msgs = msgs[ids.index(since_id) + 1:]
    return [MessageOut.model_validate(m) for m in msgs]


@router.get("/status/{interview_token}")
async def get_status(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lightweight status poll — used by interviewer view."""
    iv = await _get_iv(interview_token, db)
    if not (iv.candidate_id == current_user.id or _is_org_viewer(iv, current_user)):
        raise HTTPException(403, "Access denied")
    # Get tab switch count from most recent vision log
    from sqlalchemy import desc
    vres = await db.execute(
        select(VisionLog).where(VisionLog.interview_id == iv.id).order_by(desc(VisionLog.timestamp)).limit(1)
    )
    last_log = vres.scalar_one_or_none()
    return {
        "status": iv.status.value,
        "ai_paused": iv.ai_paused,
        "message_count": len(iv.messages),
        "tab_switches": last_log.tab_switch_count if last_log else 0,
    }


@router.get("/metrics/{interview_token}")
async def get_live_metrics(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live vision metrics for HR/Interviewer watch view — polls every 3s."""
    iv = await _get_iv(interview_token, db)
    if not _is_org_viewer(iv, current_user) and iv.candidate_id != current_user.id:
        raise HTTPException(403, "Access denied")

    from sqlalchemy import desc, func as sqlfunc
    # Get last 10 vision logs for rolling averages
    vres = await db.execute(
        select(VisionLog)
        .where(VisionLog.interview_id == iv.id)
        .order_by(desc(VisionLog.timestamp))
        .limit(10)
    )
    logs = vres.scalars().all()

    if not logs:
        return {
            "frames_analyzed": 0,
            "confidence": None,
            "engagement": None,
            "stress": None,
            "dominant_emotion": None,
            "face_count": None,
            "cheating_score": 0.0,
            "cheating_flags": [],
            "tab_switches": 0,
            "look_away_count": 0,
            "multi_face_count": 0,
            "gaze_ok": True,
            "ai_paused": iv.ai_paused,
            "status": iv.status.value,
        }

    # Compute averages from recent frames
    confs = [l.confidence_score for l in logs if l.confidence_score is not None]
    engs  = [l.engagement_score for l in logs if l.engagement_score is not None]
    strs  = [l.stress_score for l in logs if l.stress_score is not None]
    cheat = [l.cheating_score for l in logs if l.cheating_score is not None]
    latest = logs[0]  # most recent

    # Count alerts across ALL logs
    all_res = await db.execute(
        select(VisionLog).where(VisionLog.interview_id == iv.id)
    )
    all_logs = all_res.scalars().all()
    look_away = sum(1 for l in all_logs if (l.face_count or 1) == 0)
    multi_face = sum(1 for l in all_logs if (l.face_count or 1) > 1)
    all_flags = []
    for l in all_logs:
        if l.cheating_flags:
            flags = l.cheating_flags if isinstance(l.cheating_flags, list) else []
            all_flags.extend(flags)

    return {
        "frames_analyzed": len(all_logs),
        "confidence": round(sum(confs) / len(confs), 1) if confs else None,
        "engagement": round(sum(engs) / len(engs), 1) if engs else None,
        "stress": round(sum(strs) / len(strs), 1) if strs else None,
        "dominant_emotion": latest.dominant_emotion,
        "face_count": latest.face_count,
        "cheating_score": round(max(cheat), 1) if cheat else 0.0,
        "cheating_flags": list(set(all_flags))[-5:],
        "tab_switches": latest.tab_switch_count or 0,
        "look_away_count": look_away,
        "multi_face_count": multi_face,
        "gaze_ok": (latest.face_count or 1) > 0,
        "ai_paused": iv.ai_paused,
        "status": iv.status.value,
    }


# ── Interviewer controls ──────────────────────────────────────────────────────

@router.post("/pause-ai/{interview_token}")
async def pause_ai(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Interviewer pauses AI — they can now ask manual questions."""
    iv = await _get_iv(interview_token, db)
    if not _is_org_viewer(iv, current_user):
        raise HTTPException(403, "Only assigned interviewers / HR can control AI")
    iv.ai_paused = True
    await db.flush()
    return {"ai_paused": True}


@router.post("/resume-ai/{interview_token}")
async def resume_ai(
    interview_token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Interviewer resumes AI control."""
    iv = await _get_iv(interview_token, db)
    if not _is_org_viewer(iv, current_user):
        raise HTTPException(403, "Only assigned interviewers / HR can control AI")
    iv.ai_paused = False
    await db.flush()
    return {"ai_paused": False}


@router.post("/ask/{interview_token}")
async def interviewer_ask(
    interview_token: str,
    payload: InterviewerQuestion,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Interviewer injects a manual question visible to candidate."""
    iv = await _get_iv(interview_token, db)
    if not _is_org_viewer(iv, current_user):
        raise HTTPException(403, "Access denied")
    if iv.status != InterviewStatus.IN_PROGRESS:
        raise HTTPException(400, "Interview not in progress")

    # Store as "interviewer" role message
    msg = InterviewMessage(
        interview_id=iv.id,
        role="interviewer",
        content=f"[Interviewer — {current_user.full_name}]: {payload.question}",
    )
    db.add(msg)
    await db.flush()
    await db.refresh(msg)
    return MessageOut.model_validate(msg)


# ── Complete interview ────────────────────────────────────────────────────────

@router.post("/complete/{interview_token}", response_model=EvaluationResult)
async def complete_interview(
    interview_token: str,
    payload: CompleteInterviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    iv = await _get_iv(interview_token, db)
    if iv.candidate_id != current_user.id:
        raise HTTPException(403, "Only candidate can complete this interview")

    if iv.status == InterviewStatus.COMPLETED:
        stored = iv.emotion_scores or {}
        return EvaluationResult(
            overall_score=float(iv.overall_score or 0),
            answer_score=float(iv.answer_score or 0),
            code_score=float(iv.code_score) if iv.code_score is not None else None,
            emotion_score=float(iv.emotion_score) if iv.emotion_score is not None else None,
            integrity_score=float(iv.integrity_score) if iv.integrity_score is not None else None,
            passed=bool(iv.passed),
            ai_feedback=iv.ai_feedback or "",
            cheating_score=float(iv.cheating_score) if iv.cheating_score is not None else None,
            cheating_flags=stored.get("cheating_flags", []),
        )

    # Load vision logs
    logs_res = await db.execute(
        select(VisionLog).where(VisionLog.interview_id == iv.id).order_by(VisionLog.timestamp)
    )
    vision_logs = logs_res.scalars().all()
    vision_summary = aggregate_vision_logs(vision_logs) if vision_logs else None
    if not vision_summary and payload.emotion_data:
        vision_summary = payload.emotion_data.model_dump()

    # Add tab switches to vision summary
    if vision_summary and payload.tab_switches:
        vision_summary["tab_switches"] = payload.tab_switches

    final_cheating: Optional[float] = None
    if vision_logs:
        scores = [float(l.cheating_score) for l in vision_logs]
        final_cheating = round(max(scores), 1) if scores else None
    if final_cheating is None and payload.cheating_score is not None:
        final_cheating = float(payload.cheating_score)

    msgs = sorted(iv.messages, key=lambda m: m.timestamp)
    evaluation = await generate_final_evaluation(
        interview=iv, messages=msgs,
        emotion_data=vision_summary, cheating_score=final_cheating,
    )

    iv.status = InterviewStatus.COMPLETED
    iv.ended_at = datetime.now(timezone.utc)
    iv.answer_score = evaluation["answer_score"]
    iv.code_score = evaluation.get("code_score")
    iv.emotion_score = evaluation.get("emotion_score")
    iv.integrity_score = evaluation.get("integrity_score")
    iv.cheating_score = evaluation.get("cheating_score")
    iv.overall_score = evaluation["overall_score"]
    iv.passed = evaluation["passed"]
    iv.ai_feedback = evaluation["ai_feedback"]
    iv.emotion_scores = vision_summary
    await db.flush()

    return EvaluationResult(
        overall_score=evaluation["overall_score"],
        answer_score=evaluation["answer_score"],
        code_score=evaluation.get("code_score"),
        emotion_score=evaluation.get("emotion_score"),
        integrity_score=evaluation.get("integrity_score"),
        passed=evaluation["passed"],
        strengths=evaluation.get("strengths", []),
        weaknesses=evaluation.get("weaknesses", []),
        ai_feedback=evaluation["ai_feedback"],
        cheating_score=evaluation.get("cheating_score"),
        score_breakdown=ScoreBreakdown(
            answer_score=evaluation["answer_score"],
            code_score=evaluation.get("code_score"),
            emotion_score=evaluation.get("emotion_score"),
            integrity_score=evaluation.get("integrity_score"),
            overall_score=evaluation["overall_score"],
            passed=evaluation["passed"],
            weights_used=evaluation.get("weights_used", {}),
        ),
        cheating_flags=vision_summary.get("cheating_flags", []) if vision_summary else [],
    )
