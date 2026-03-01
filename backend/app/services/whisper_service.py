import io
import logging
import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ── Dedicated thread pool for STT — isolated from the vision pool ─────────────
# max_workers=2: one transcribing, one ready. More workers won't help on CPU
# because Whisper is single-threaded per inference.
_stt_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt-worker")

# ── Model singleton ────────────────────────────────────────────────────────────
_model = None
_model_lock = asyncio.Lock()

def _stt_model_name() -> str:
    return os.getenv("STT_MODEL", "base")


def _load_model_sync(size: str, device: str, compute: str):
    """Synchronous model load — runs in thread pool during warmup."""
    from faster_whisper import WhisperModel
    logger.info("Loading faster-whisper model: %s on %s/%s", size, device, compute)
    model = WhisperModel(size, device=device, compute_type=compute)
    logger.info("✅ Whisper model ready (%s)", size)
    return model


async def warmup_model():
    """
    Pre-load the Whisper model at server startup.
    Call this from the FastAPI lifespan so the first STT request is instant.
    Safe to call multiple times — no-op if already loaded.
    """
    global _model
    if _model is not None:
        return

    size    = os.getenv("STT_MODEL",   "base")
    device  = os.getenv("STT_DEVICE",  "cpu")
    compute = os.getenv("STT_COMPUTE", "int8")

    async with _model_lock:
        if _model is not None:
            return
        try:
            loop = asyncio.get_running_loop()
            _model = await loop.run_in_executor(
                _stt_executor,
                _load_model_sync, size, device, compute
            )
        except ImportError:
            logger.warning(
                "faster-whisper not installed — STT disabled. "
                "Run: pip install faster-whisper"
            )
            _model = "unavailable"
        except Exception as e:
            logger.error("Whisper model load failed: %s", e)
            _model = "unavailable"


async def _get_model():
    """Return the loaded model, loading it if not yet done."""
    global _model
    if _model is not None:
        return _model
    await warmup_model()
    return _model


def _transcribe_sync(model, audio_bytes: bytes, language: str) -> tuple:
    """
    Pure synchronous transcription — runs inside _stt_executor.
    Returns (text, detected_language, duration_seconds).
    """
    started = time.perf_counter()
    audio_io = io.BytesIO(audio_bytes)
    segments, info = model.transcribe(
        audio_io,
        language=language if language != "auto" else None,
        beam_size=3,            # faster than default 5, near-identical accuracy
        vad_filter=True,        # skip silent frames inside the audio
        vad_parameters={"min_silence_duration_ms": 200},
        word_timestamps=False,  # not needed, skip extra work
    )
    # segments is a generator — must be consumed inside this thread
    text = " ".join(seg.text.strip() for seg in segments).strip()
    infer_ms = (time.perf_counter() - started) * 1000.0
    return text, info.language, info.duration, round(infer_ms, 1)


async def transcribe_audio(audio_bytes: bytes, language: str = "en") -> dict:
    """
    Transcribe raw audio bytes (webm / ogg / wav / mp4 — anything ffmpeg handles).
    Returns: {"text": str, "language": str, "duration": float, "available": bool}
    """
    started = time.perf_counter()
    model_name = _stt_model_name()

    if not audio_bytes:
        return {
            "text": "",
            "language": language,
            "duration": 0.0,
            "available": False,
            "processing_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "model": model_name,
        }

    model = await _get_model()

    if model == "unavailable" or model is None:
        return {
            "text": "",
            "language": language,
            "duration": 0.0,
            "available": False,
            "processing_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "model": model_name,
        }

    try:
        loop = asyncio.get_running_loop()
        text, detected_lang, duration, infer_ms = await loop.run_in_executor(
            _stt_executor,          # dedicated pool — not shared with vision
            _transcribe_sync, model, audio_bytes, language
        )
        total_ms = (time.perf_counter() - started) * 1000.0
        logger.debug(
            "STT transcribe bytes=%d lang=%s model=%s infer_ms=%.1f total_ms=%.1f",
            len(audio_bytes), language, model_name, infer_ms, total_ms
        )
        return {
            "text": text,
            "language": detected_lang,
            "duration": round(duration, 2),
            "available": True,
            "processing_ms": round(total_ms, 1),
            "model": model_name,
        }
    except Exception as e:
        logger.error("Whisper transcription error: %s", e)
        return {
            "text": "", "language": language, "duration": 0.0,
            "available": False, "error": str(e),
            "processing_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "model": model_name,
        }
