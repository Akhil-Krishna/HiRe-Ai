
import json
import logging
from collections import defaultdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.core.security import decode_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webrtc"])

# rooms[interview_token] = {"candidate": ws|None, "watchers": [ws, ...]}
rooms: dict = defaultdict(lambda: {"candidate": None, "watchers": []})


async def _broadcast_status(token: str):
    room = rooms[token]
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
    for ws in room["watchers"]:
        try:
            await ws.send_text(msg)
        except Exception:
            pass


@router.websocket("/ws/rtc/{token}")
async def rtc_signaling(
    websocket: WebSocket,
    token: str,
    role: str = Query(..., description="'candidate' or 'watcher'"),
    auth: str = Query(default=None, alias="token", description="JWT bearer token"),
):
    # Authenticate via query param JWT (WebSocket can't set Authorization header easily)
    if not auth:
        await websocket.close(code=4001, reason="Missing auth token")
        return
    payload = decode_token(auth)
    if not payload:
        await websocket.close(code=4003, reason="Invalid token")
        return

    if role not in ("candidate", "watcher"):
        await websocket.close(code=4000, reason="role must be 'candidate' or 'watcher'")
        return

    await websocket.accept()
    room = rooms[token]

    if role == "candidate":
        room["candidate"] = websocket
        logger.info(f"RTC: candidate joined room {token[:8]}")
    else:
        room["watchers"].append(websocket)
        logger.info(f"RTC: watcher joined room {token[:8]} ({len(room['watchers'])} watchers)")

    await _broadcast_status(token)

    # If a watcher joins and candidate already sent an offer, signal watcher to request offer
    if role == "watcher" and room["candidate"]:
        try:
            await room["candidate"].send_text(json.dumps({"type": "request_offer"}))
        except Exception:
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "offer" and role == "candidate":
                # Forward offer to all watchers
                for ws in room["watchers"]:
                    try:
                        await ws.send_text(raw)
                    except Exception:
                        pass

            elif msg_type == "answer" and role == "watcher":
                # Forward answer to candidate
                if room["candidate"]:
                    try:
                        await room["candidate"].send_text(raw)
                    except Exception:
                        pass

            elif msg_type == "ice":
                # Route ICE candidates: candidate→watchers or watcher→candidate
                if role == "candidate":
                    for ws in room["watchers"]:
                        try:
                            await ws.send_text(raw)
                        except Exception:
                            pass
                else:
                    if room["candidate"]:
                        try:
                            await room["candidate"].send_text(raw)
                        except Exception:
                            pass

            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    finally:
        if role == "candidate":
            room["candidate"] = None
            # Notify watchers candidate disconnected
            for ws in room["watchers"]:
                try:
                    await ws.send_text(json.dumps({"type": "candidate_left"}))
                except Exception:
                    pass
            logger.info(f"RTC: candidate left room {token[:8]}")
        else:
            if websocket in room["watchers"]:
                room["watchers"].remove(websocket)
            logger.info(f"RTC: watcher left room {token[:8]}")

        # Clean up empty rooms
        if not room["candidate"] and not room["watchers"]:
            del rooms[token]

        await _broadcast_status(token)
