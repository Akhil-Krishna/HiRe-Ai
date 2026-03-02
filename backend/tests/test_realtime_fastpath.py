import asyncio
from io import BytesIO
from types import SimpleNamespace

from starlette.datastructures import UploadFile

from app.api.v1.endpoints import stt as stt_endpoint
from app.services import interview_orchestrator


def test_chat_turn_uses_local_fastpath_when_realtime_celery_disabled(monkeypatch):
    monkeypatch.setattr(interview_orchestrator.settings, "AI_LOCAL_FASTPATH_ENABLED", True)
    monkeypatch.setattr(interview_orchestrator.settings, "CELERY_REALTIME_ENABLED", False)

    async def _fake_get_ai_response(**kwargs):
        return "ok", False

    async def _runner_should_not_be_called(*args, **kwargs):
        raise AssertionError("run_task_with_fallback should not be called in local fastpath")

    monkeypatch.setattr(interview_orchestrator, "get_ai_response", _fake_get_ai_response)
    monkeypatch.setattr(interview_orchestrator, "run_task_with_fallback", _runner_should_not_be_called)

    async def _run():
        out = await interview_orchestrator.chat_turn(
            iv=SimpleNamespace(),
            messages=[],
            candidate_message="hello",
            code_snippet=None,
        )
        assert out == {"text": "ok", "is_complete": False}

    asyncio.run(_run())


def test_stt_endpoint_uses_local_fastpath_when_realtime_celery_disabled(monkeypatch):
    monkeypatch.setattr(stt_endpoint.settings, "STT_LOCAL_FASTPATH_ENABLED", True)
    monkeypatch.setattr(stt_endpoint.settings, "CELERY_REALTIME_ENABLED", False)

    async def _fake_transcribe_audio(audio_bytes, language="en"):
        return {
            "text": "test",
            "language": language,
            "duration": 1.0,
            "available": True,
            "processing_ms": 10.0,
            "model": "base",
        }

    async def _runner_should_not_be_called(*args, **kwargs):
        raise AssertionError("run_task_with_fallback should not be called in local fastpath")

    monkeypatch.setattr(stt_endpoint, "transcribe_audio", _fake_transcribe_audio)
    monkeypatch.setattr(stt_endpoint, "run_task_with_fallback", _runner_should_not_be_called)

    upload = UploadFile(filename="audio.webm", file=BytesIO(b"fake-audio-bytes"))
    user = SimpleNamespace(id="user-1")

    async def _run():
        out = await stt_endpoint.transcribe(audio=upload, language="en", current_user=user)
        assert out["text"] == "test"
        assert out["available"] is True

    asyncio.run(_run())
