
from fastapi import APIRouter, UploadFile, File, Depends, Form
from app.core.deps import get_current_user
from app.models.user import User
from app.services.whisper_service import transcribe_audio

router = APIRouter(prefix="/stt", tags=["stt"])


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(..., description="Audio blob — webm/ogg/wav"),
    language: str = Form(default="en"),
    current_user: User = Depends(get_current_user),
):
    """
    Receive audio chunk from browser, transcribe with Whisper, return text.
    Called every time the candidate stops speaking (silence detected client-side).
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        return {"text": "", "available": False}

    result = await transcribe_audio(audio_bytes, language=language)
    return result
