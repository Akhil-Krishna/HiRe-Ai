
import io
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded model
_model = None
_model_lock = asyncio.Lock()
_MODEL_SIZE = "base"   # tiny | base | small | medium — set via STT_MODEL env var
_DEVICE = "cpu"         # cpu | cuda
_COMPUTE = "int8"       # int8 (fastest on CPU) | float16 (GPU)


async def _get_model():
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
            import os
            size = os.getenv("STT_MODEL", _MODEL_SIZE)
            device = os.getenv("STT_DEVICE", _DEVICE)
            compute = os.getenv("STT_COMPUTE", _COMPUTE)
            logger.info(f"Loading faster-whisper model: {size} on {device}/{compute}")
            # Run in thread to avoid blocking event loop
            loop = asyncio.get_event_loop()
            _model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(size, device=device, compute_type=compute)
            )
            logger.info("✅ Whisper model loaded")
        except ImportError:
            logger.warning("faster-whisper not installed — STT will return empty. Run: pip install faster-whisper")
            _model = "unavailable"
    return _model


async def transcribe_audio(audio_bytes: bytes, language: str = "en") -> dict:
    """
    Transcribe audio bytes (webm/ogg/wav/mp3 — anything ffmpeg handles).
    Returns: {"text": str, "language": str, "duration": float, "available": bool}
    """
    model = await _get_model()

    if model == "unavailable" or not audio_bytes:
        return {"text": "", "language": language, "duration": 0.0, "available": False}

    try:
        loop = asyncio.get_event_loop()

        def _transcribe():
            # faster-whisper accepts file-like objects
            audio_io = io.BytesIO(audio_bytes)
            segments, info = model.transcribe(
                audio_io,
                language=language if language != "auto" else None,
                beam_size=3,
                vad_filter=True,          # Skip silent chunks
                vad_parameters={"min_silence_duration_ms": 300},
                word_timestamps=False,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return text, info.language, info.duration

        text, detected_lang, duration = await loop.run_in_executor(None, _transcribe)
        return {
            "text": text,
            "language": detected_lang,
            "duration": round(duration, 2),
            "available": True,
        }
    except Exception as e:
        logger.error(f"Whisper transcription error: {e}")
        return {"text": "", "language": language, "duration": 0.0, "available": False, "error": str(e)}
