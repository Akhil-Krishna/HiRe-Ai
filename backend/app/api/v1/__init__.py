from fastapi import APIRouter
from app.api.v1.endpoints import auth, users, interviews, interview_session, vision, recordings, stt, webrtc

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(users.org_router)
api_router.include_router(interviews.router)
api_router.include_router(interview_session.router)
api_router.include_router(vision.router)
api_router.include_router(recordings.router)
api_router.include_router(stt.router)
api_router.include_router(webrtc.router)  # WebSocket — no prefix, mounts at /ws/rtc/{token}
