
import json
import asyncio
import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.security import decode_token
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.interview import Interview, InterviewInterviewer

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webrtc"])


# ── Authorization helper ──────────────────────────────────────────────────────

async def _authorize(interview_token: str, jwt_payload: dict, role: str) -> Optional[str]:
    """
    Returns None if authorized.
    Returns a rejection reason string if denied.
    Opens and closes its own DB session (WS connections stay open long-term).
    """
    user_id: str = jwt_payload.get("sub")
    if not user_id:
        return "Invalid token: missing sub"

    async with AsyncSessionLocal() as db:
        # Load interview by access_token with all needed relationships
        res = await db.execute(
            select(Interview)
            .options(
                selectinload(Interview.hr).selectinload(User.organisation),
                selectinload(Interview.interviewers).selectinload(InterviewInterviewer.interviewer),
            )
            .where(Interview.access_token == interview_token)
        )
        iv = res.scalar_one_or_none()
        if not iv:
            return "Interview not found"

        # Load user
        ures = await db.execute(
            select(User)
            .options(selectinload(User.organisation))
            .where(User.id == user_id)
        )
        user = ures.scalar_one_or_none()
        if not user or not user.is_active:
            return "User not found or inactive"

        if role == "candidate":
            if user.role == UserRole.ADMIN:
                return None  # admin can join as candidate for testing
            if user.id == iv.candidate_id:
                return None
            return "Not authorized: not the candidate for this interview"

        elif role == "watcher":
            if user.role == UserRole.ADMIN:
                return None

            if user.role == UserRole.HR:
                # HR must be in the same organisation as the interview's HR
                hr_org = iv.hr.organisation_id if iv.hr else None
                if hr_org and user.organisation_id == hr_org:
                    return None
                return "Not authorized: HR is not in the same organisation"

            if user.role == UserRole.INTERVIEWER:
                assigned_ids = {ii.interviewer_id for ii in iv.interviewers}
                if user.id in assigned_ids:
                    return None
                return "Not authorized: Interviewer not assigned to this interview"

            return "Not authorized: insufficient role"

        return "Invalid role"


# ── Signaling backends ────────────────────────────────────────────────────────

class InMemoryBackend:
    """
    Single-worker in-memory signaling.
    Works perfectly for single uvicorn worker (--workers 1, default dev).
    NOT safe for multi-worker deployments — each worker has isolated memory.
    For multi-worker: set REDIS_URL in .env and install redis[asyncio].
    """
    def __init__(self):
        # rooms[token] = {"candidate": ws|None, "watchers": [ws, ...]}
        self._rooms: dict = defaultdict(lambda: {"candidate": None, "watchers": []})

    def get_room(self, token: str) -> dict:
        return self._rooms[token]

    def set_candidate(self, token: str, ws: Optional[WebSocket]):
        self._rooms[token]["candidate"] = ws

    def add_watcher(self, token: str, ws: WebSocket):
        self._rooms[token]["watchers"].append(ws)

    def remove_watcher(self, token: str, ws: WebSocket):
        if ws in self._rooms[token]["watchers"]:
            self._rooms[token]["watchers"].remove(ws)

    def is_empty(self, token: str) -> bool:
        r = self._rooms.get(token)
        return r is None or (not r["candidate"] and not r["watchers"])

    def cleanup(self, token: str):
        if token in self._rooms:
            del self._rooms[token]

    async def broadcast_to_watchers(self, token: str, raw: str, exclude: WebSocket = None):
        for ws in list(self._rooms[token]["watchers"]):
            if ws is exclude:
                continue
            try:
                await ws.send_text(raw)
            except Exception:
                pass

    async def send_to_candidate(self, token: str, raw: str):
        cand = self._rooms[token]["candidate"]
        if cand:
            try:
                await cand.send_text(raw)
            except Exception:
                pass

    async def broadcast_status(self, token: str):
        room = self._rooms.get(token, {"candidate": None, "watchers": []})
        msg = json.dumps({
            "type": "status",
            "candidates": 1 if room["candidate"] else 0,
            "watchers": len(room["watchers"]),
        })
        if room["candidate"]:
            try:
                await room["candidate"].send_text(msg)
            except Exception:
                pass
        for ws in list(room["watchers"]):
            try:
                await ws.send_text(msg)
            except Exception:
                pass

    # Lifecycle hooks (no-ops for in-memory)
    async def on_connect(self, token: str, role: str, ws: WebSocket): pass
    async def on_disconnect(self, token: str, role: str, ws: WebSocket): pass


class RedisBackend:
    """
    Multi-worker Redis pub/sub signaling.
    Each worker holds its own WebSocket connections locally.
    Messages are relayed through Redis so any worker can deliver to its local WS.

    Channel per interview: hireai:rtc:{token}
    Message envelope:      {"dir": "to_watchers"|"to_candidate"|"status", "data": <raw JSON string>}
    """
    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._redis = None
        self._pubsub_tasks: dict[str, asyncio.Task] = {}
        # Local WS registry — only this worker's connections
        self._rooms: dict = defaultdict(lambda: {"candidate": None, "watchers": []})

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _channel(self, token: str) -> str:
        return f"hireai:rtc:{token}"

    def get_room(self, token: str) -> dict:
        return self._rooms[token]

    def set_candidate(self, token: str, ws: Optional[WebSocket]):
        self._rooms[token]["candidate"] = ws

    def add_watcher(self, token: str, ws: WebSocket):
        self._rooms[token]["watchers"].append(ws)

    def remove_watcher(self, token: str, ws: WebSocket):
        if ws in self._rooms[token]["watchers"]:
            self._rooms[token]["watchers"].remove(ws)

    def is_empty(self, token: str) -> bool:
        r = self._rooms.get(token)
        return r is None or (not r["candidate"] and not r["watchers"])

    def cleanup(self, token: str):
        if token in self._rooms:
            del self._rooms[token]
        if token in self._pubsub_tasks:
            self._pubsub_tasks[token].cancel()
            del self._pubsub_tasks[token]

    async def _publish(self, token: str, direction: str, raw: str):
        r = await self._get_redis()
        envelope = json.dumps({"dir": direction, "data": raw})
        await r.publish(self._channel(token), envelope)

    async def _subscribe_loop(self, token: str):
        """Background task: listen for Redis messages and deliver to local WS connections."""
        try:
            import redis.asyncio as aioredis
            r = await aioredis.from_url(self._redis_url, decode_responses=True)
            ps = r.pubsub()
            await ps.subscribe(self._channel(token))
            async for raw_msg in ps.listen():
                if raw_msg["type"] != "message":
                    continue
                try:
                    env = json.loads(raw_msg["data"])
                    direction = env["dir"]
                    data = env["data"]
                    room = self._rooms.get(token, {"candidate": None, "watchers": []})
                    if direction == "to_watchers":
                        for ws in list(room["watchers"]):
                            try:
                                await ws.send_text(data)
                            except Exception:
                                pass
                    elif direction == "to_candidate":
                        if room["candidate"]:
                            try:
                                await room["candidate"].send_text(data)
                            except Exception:
                                pass
                except Exception as e:
                    logger.warning(f"Redis pubsub error: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Redis subscribe loop crashed for {token[:8]}: {e}")

    async def broadcast_to_watchers(self, token: str, raw: str, exclude: WebSocket = None):
        await self._publish(token, "to_watchers", raw)

    async def send_to_candidate(self, token: str, raw: str):
        await self._publish(token, "to_candidate", raw)

    async def broadcast_status(self, token: str):
        room = self._rooms.get(token, {"candidate": None, "watchers": []})
        msg = json.dumps({
            "type": "status",
            "candidates": 1 if room["candidate"] else 0,
            "watchers": len(room["watchers"]),
        })
        await self._publish(token, "to_watchers", msg)
        await self._publish(token, "to_candidate", msg)

    async def on_connect(self, token: str, role: str, ws: WebSocket):
        # Start subscribe loop for this room if not already running
        if token not in self._pubsub_tasks:
            task = asyncio.create_task(self._subscribe_loop(token))
            self._pubsub_tasks[token] = task

    async def on_disconnect(self, token: str, role: str, ws: WebSocket): pass


# ── Instantiate backend based on config ──────────────────────────────────────

def _make_backend():
    if settings.REDIS_URL:
        try:
            import redis.asyncio  # noqa
            logger.info(f"WebRTC: using Redis backend ({settings.REDIS_URL})")
            return RedisBackend(settings.REDIS_URL)
        except ImportError:
            logger.warning(
                "REDIS_URL is set but redis[asyncio] is not installed. "
                "Falling back to in-memory backend. "
                "Run: pip install 'redis[asyncio]'"
            )
    logger.info("WebRTC: using in-memory backend (single-worker mode)")
    return InMemoryBackend()


_backend = _make_backend()


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/rtc/{interview_token}")
async def rtc_signaling(
    websocket: WebSocket,
    interview_token: str,
    role: str = Query(..., description="'candidate' or 'watcher'"),
    token: str = Query(default=None, description="JWT bearer token"),
):
    """
    WebRTC signaling relay with full authorization.

    URL: ws://host/ws/rtc/{interview_access_token}?role=candidate|watcher&token={JWT}

    Authorization is enforced BEFORE accept().
    """
    # ── 1. Basic validation ──
    if not token:
        await websocket.close(code=4001, reason="Missing auth token")
        return

    jwt_payload = decode_token(token)
    if not jwt_payload:
        await websocket.close(code=4003, reason="Invalid or expired JWT")
        return

    if role not in ("candidate", "watcher"):
        await websocket.close(code=4000, reason="role must be 'candidate' or 'watcher'")
        return

    # ── 2. Authorization check (DB lookup, before accept) ──
    denial = await _authorize(interview_token, jwt_payload, role)
    if denial:
        logger.warning(f"RTC auth denied [{role}] room={interview_token[:8]}: {denial}")
        await websocket.close(code=4003, reason=denial)
        return

    # ── 3. Accept and register ──
    await websocket.accept()
    await _backend.on_connect(interview_token, role, websocket)

    if role == "candidate":
        _backend.set_candidate(interview_token, websocket)
        logger.info(f"RTC: candidate joined room {interview_token[:8]}")
    else:
        _backend.add_watcher(interview_token, websocket)
        logger.info(f"RTC: watcher joined room {interview_token[:8]}")

    await _backend.broadcast_status(interview_token)

    # If new watcher joins and candidate is present, ask candidate to re-offer
    if role == "watcher":
        room = _backend.get_room(interview_token)
        if room["candidate"]:
            try:
                await room["candidate"].send_text(json.dumps({"type": "request_offer"}))
            except Exception:
                pass

    # ── 4. Message relay loop ──
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "offer" and role == "candidate":
                await _backend.broadcast_to_watchers(interview_token, raw, exclude=websocket)

            elif msg_type == "answer" and role == "watcher":
                await _backend.send_to_candidate(interview_token, raw)

            elif msg_type == "ice":
                if role == "candidate":
                    await _backend.broadcast_to_watchers(interview_token, raw, exclude=websocket)
                else:
                    await _backend.send_to_candidate(interview_token, raw)

            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    finally:
        if role == "candidate":
            _backend.set_candidate(interview_token, None)
            # Notify watchers
            await _backend.broadcast_to_watchers(
                interview_token,
                json.dumps({"type": "candidate_left"})
            )
            logger.info(f"RTC: candidate left room {interview_token[:8]}")
        else:
            _backend.remove_watcher(interview_token, websocket)
            logger.info(f"RTC: watcher left room {interview_token[:8]}")

        await _backend.on_disconnect(interview_token, role, websocket)

        if _backend.is_empty(interview_token):
            _backend.cleanup(interview_token)

        await _backend.broadcast_status(interview_token)
