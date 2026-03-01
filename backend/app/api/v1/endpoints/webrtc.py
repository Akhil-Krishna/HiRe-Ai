import asyncio
import json
import logging
from typing import Optional, Tuple

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import decode_token
from app.models.interview import Interview, InterviewInterviewer
from app.models.user import User, UserRole
from app.services.room_manager import RoomManager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webrtc"])

room_manager = RoomManager(
    capacity=settings.RTC_ROOM_CAPACITY,
    join_rate_limit=settings.RTC_JOIN_RATE_LIMIT,
    join_window_seconds=settings.RTC_JOIN_WINDOW_SECONDS,
)


async def _load_access_context(interview_token: str, user_id: str) -> Tuple[Optional[Interview], Optional[User]]:
    async with AsyncSessionLocal() as db:
        iv_res = await db.execute(
            select(Interview)
            .options(
                selectinload(Interview.hr).selectinload(User.organisation),
                selectinload(Interview.interviewers).selectinload(InterviewInterviewer.interviewer),
                selectinload(Interview.candidate),
            )
            .where(Interview.access_token == interview_token)
        )
        interview = iv_res.scalar_one_or_none()
        if not interview:
            return None, None

        user_res = await db.execute(
            select(User)
            .options(selectinload(User.organisation))
            .where(User.id == user_id)
        )
        user = user_res.scalar_one_or_none()
        return interview, user


def _server_role_for_user(interview: Interview, user: User) -> Optional[str]:
    if not user or not user.is_active:
        return None
    if user.role == UserRole.ADMIN:
        return "admin"
    if interview.candidate_id == user.id:
        return "candidate"
    if user.role == UserRole.HR:
        hr_org = interview.hr.organisation_id if interview.hr else None
        if hr_org and user.organisation_id == hr_org:
            return "hr"
        return None
    if user.role == UserRole.INTERVIEWER:
        assigned_ids = {ii.interviewer_id for ii in interview.interviewers}
        if user.id in assigned_ids:
            return "interviewer"
        return None
    return None


async def _send_json_safe(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_text(json.dumps(payload))
        return True
    except Exception:
        return False


async def _send_snapshot(interview_token: str, to_ws: Optional[WebSocket] = None) -> None:
    participants = await room_manager.snapshot(interview_token)
    payload = {
        "type": "participants_snapshot",
        "participants": participants,
        "participant_count": len(participants),
    }
    if to_ws is not None:
        await _send_json_safe(to_ws, payload)
        return
    for ws in await room_manager.others_ws(interview_token):
        await _send_json_safe(ws, payload)


async def _broadcast(interview_token: str, payload: dict, exclude_pid: Optional[str] = None) -> None:
    sockets = await room_manager.others_ws(interview_token, exclude_participant_id=exclude_pid)
    for ws in sockets:
        await _send_json_safe(ws, payload)


@router.websocket("/ws/rtc/{interview_token}")
async def rtc_signaling(
    websocket: WebSocket,
    interview_token: str,
    role: str = Query(default="participant", description="client hint only"),
    token: str = Query(default=None, description="JWT bearer token"),
):
    participant = None
    joined = False

    if not token:
        await websocket.close(code=4001, reason="Missing auth token")
        return

    jwt_payload = decode_token(token)
    if not jwt_payload:
        await websocket.close(code=4003, reason="Invalid or expired JWT")
        return

    user_id = jwt_payload.get("sub")
    if not user_id:
        await websocket.close(code=4003, reason="Invalid token payload")
        return

    if not await room_manager.allow_join(interview_token, user_id):
        await websocket.close(code=4008, reason="Join rate limit exceeded")
        return

    interview, user = await _load_access_context(interview_token, user_id)
    if not interview:
        await websocket.close(code=4004, reason="Interview not found")
        return
    if not user:
        await websocket.close(code=4003, reason="User not found")
        return

    server_role = _server_role_for_user(interview, user)
    if not server_role:
        await websocket.close(code=4003, reason="Not authorized for this room")
        return

    await websocket.accept()

    participant, join_err = await room_manager.join(
        room_token=interview_token,
        user_id=user.id,
        role=server_role,
        display_name=user.full_name or user.email,
        ws=websocket,
    )
    if join_err == "duplicate_join":
        await _send_json_safe(websocket, {"type": "error", "detail": "Duplicate join detected"})
        await websocket.close(code=4009, reason="Duplicate join")
        return
    if join_err == "room_full":
        await _send_json_safe(websocket, {"type": "error", "detail": "Room capacity reached"})
        await websocket.close(code=4010, reason="Room full")
        return
    if not participant:
        await websocket.close(code=1011, reason="Could not join room")
        return

    joined = True
    logger.info(
        "RTC join room=%s participant=%s user=%s role=%s client_role=%s",
        interview_token[:8], participant.participant_id, user.id, server_role, role
    )

    await _send_json_safe(websocket, {
        "type": "joined",
        "participant": participant.as_public(),
        "room_capacity": settings.RTC_ROOM_CAPACITY,
    })

    participants_now = await room_manager.snapshot(interview_token)
    await _send_json_safe(websocket, {
        "type": "participants_snapshot",
        "participants": participants_now,
        "participant_count": len(participants_now),
    })

    await _broadcast(
        interview_token,
        {
            "type": "participant_joined",
            "participant": participant.as_public(),
            "participant_count": len(participants_now),
        },
        exclude_pid=participant.participant_id,
    )

    for peer in participants_now:
        peer_pid = peer.get("participant_id")
        if not peer_pid or peer_pid == participant.participant_id:
            continue
        peer_ws = await room_manager.get_ws(interview_token, peer_pid)
        if peer_ws:
            await _send_json_safe(peer_ws, {"type": "negotiate_with", "participant_id": participant.participant_id})

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=float(settings.RTC_SIGNAL_TIMEOUT_SECONDS),
                )
            except asyncio.TimeoutError:
                await _send_json_safe(websocket, {"type": "ping"})
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_json_safe(websocket, {"type": "error", "detail": "Invalid JSON"})
                continue

            msg_type = msg.get("type")
            if msg_type == "ping":
                await _send_json_safe(websocket, {"type": "pong"})
                continue
            if msg_type == "pong":
                continue

            if msg_type in ("offer", "answer", "ice"):
                target_pid = msg.get("to")
                if not target_pid:
                    await _send_json_safe(websocket, {"type": "error", "detail": "Missing target participant id"})
                    continue
                target_ws = await room_manager.get_ws(interview_token, target_pid)
                if not target_ws:
                    await _send_json_safe(websocket, {"type": "error", "detail": "Target participant not found"})
                    continue
                relay = {"type": msg_type, "from": participant.participant_id, "to": target_pid}
                if msg_type in ("offer", "answer"):
                    relay["sdp"] = msg.get("sdp")
                else:
                    relay["candidate"] = msg.get("candidate")
                await _send_json_safe(target_ws, relay)
                continue

            if msg_type == "media_state":
                mic_on = bool(msg.get("mic_on", True))
                cam_on = bool(msg.get("cam_on", True))
                await room_manager.update_media(interview_token, participant.participant_id, mic_on, cam_on)
                participants = await room_manager.snapshot(interview_token)
                await _broadcast(
                    interview_token,
                    {
                        "type": "participant_media",
                        "participant_id": participant.participant_id,
                        "mic_on": mic_on,
                        "cam_on": cam_on,
                        "participant_count": len(participants),
                    },
                    exclude_pid=participant.participant_id,
                )
                continue

            if msg_type == "speaking_state":
                speaking = bool(msg.get("speaking", False))
                await room_manager.update_speaking(interview_token, participant.participant_id, speaking)
                await _broadcast(
                    interview_token,
                    {"type": "speaking_state", "participant_id": participant.participant_id, "speaking": speaking},
                    exclude_pid=participant.participant_id,
                )
                continue

            await _send_json_safe(websocket, {"type": "error", "detail": f"Unsupported message type: {msg_type}"})

    except WebSocketDisconnect:
        logger.info("RTC disconnect room=%s participant=%s", interview_token[:8], participant.participant_id if participant else "-")
    except Exception as exc:
        logger.exception(
            "RTC error room=%s participant=%s err=%s",
            interview_token[:8],
            participant.participant_id if participant else "-",
            exc,
        )
        await _send_json_safe(websocket, {"type": "error", "detail": "Server signaling error"})
    finally:
        if joined and participant:
            await room_manager.leave(interview_token, participant.participant_id)
            remaining = await room_manager.snapshot(interview_token)
            await _broadcast(
                interview_token,
                {
                    "type": "participant_left",
                    "participant_id": participant.participant_id,
                    "participant_count": len(remaining),
                },
                exclude_pid=None,
            )
            await _send_snapshot(interview_token)
