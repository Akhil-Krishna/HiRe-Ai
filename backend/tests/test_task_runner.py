import asyncio
import time

from app.core import task_runner


class _ResultOk:
    def __init__(self, value):
        self._value = value

    def get(self, timeout=None):
        return self._value


class _ResultErr:
    def get(self, timeout=None):
        raise RuntimeError("result failed")


class _ResultSlow:
    def get(self, timeout=None):
        time.sleep(0.2)
        return {"ok": "late"}


class _TaskOk:
    name = "ok-task"

    def apply_async(self, kwargs=None):
        return _ResultOk({"ok": True, "payload": kwargs})


class _TaskEnqueueErr:
    name = "enqueue-err-task"

    def apply_async(self, kwargs=None):
        raise RuntimeError("enqueue failed")


class _TaskResultErr:
    name = "result-err-task"

    def apply_async(self, kwargs=None):
        return _ResultErr()


class _TaskResultSlow:
    name = "result-slow-task"

    def apply_async(self, kwargs=None):
        return _ResultSlow()


def test_run_task_with_fallback_success(monkeypatch):
    monkeypatch.setattr(task_runner.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(task_runner.settings, "CELERY_ENQUEUE_TIMEOUT_SECONDS", 1.0)

    async def _run():
        out = await task_runner.run_task_with_fallback(
            _TaskOk(),
            payload={"x": 1},
            fallback_callable=lambda: {"fallback": True},
            endpoint_name="/test",
        )
        assert out["ok"] is True
        assert out["payload"]["payload"]["x"] == 1

    asyncio.run(_run())


def test_run_task_with_fallback_on_enqueue_failure(monkeypatch):
    monkeypatch.setattr(task_runner.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(task_runner.settings, "CELERY_ENQUEUE_TIMEOUT_SECONDS", 1.0)

    async def _run():
        out = await task_runner.run_task_with_fallback(
            _TaskEnqueueErr(),
            payload={"x": 1},
            fallback_callable=lambda: {"fallback": True},
            endpoint_name="/test",
        )
        assert out == {"fallback": True}

    asyncio.run(_run())


def test_run_task_with_fallback_on_result_failure(monkeypatch):
    monkeypatch.setattr(task_runner.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(task_runner.settings, "CELERY_ENQUEUE_TIMEOUT_SECONDS", 1.0)

    async def _run():
        out = await task_runner.run_task_with_fallback(
            _TaskResultErr(),
            payload={"x": 1},
            fallback_callable=lambda: {"fallback": True},
            endpoint_name="/test",
        )
        assert out == {"fallback": True}

    asyncio.run(_run())


def test_run_task_with_fallback_on_result_timeout(monkeypatch):
    monkeypatch.setattr(task_runner.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(task_runner.settings, "CELERY_ENQUEUE_TIMEOUT_SECONDS", 1.0)

    async def _run():
        out = await task_runner.run_task_with_fallback(
            _TaskResultSlow(),
            payload={"x": 1},
            fallback_callable=lambda: {"fallback": True},
            endpoint_name="/test",
            wait_timeout=0.01,
        )
        assert out == {"fallback": True}

    asyncio.run(_run())


def test_run_task_with_fallback_when_celery_disabled(monkeypatch):
    monkeypatch.setattr(task_runner.settings, "CELERY_ENABLED", False)

    async def _run():
        out = await task_runner.run_task_with_fallback(
            _TaskOk(),
            payload={"x": 1},
            fallback_callable=lambda: {"fallback": True},
            endpoint_name="/test",
        )
        assert out == {"fallback": True}

    asyncio.run(_run())


def test_enqueue_task_with_fallback_on_enqueue_failure(monkeypatch):
    monkeypatch.setattr(task_runner.settings, "CELERY_ENABLED", True)
    monkeypatch.setattr(task_runner.settings, "CELERY_ENQUEUE_TIMEOUT_SECONDS", 1.0)

    async def _run():
        out = await task_runner.enqueue_task_with_fallback(
            _TaskEnqueueErr(),
            payload={"x": 1},
            fallback_callable=lambda: {"fallback": True},
            endpoint_name="/test",
        )
        assert out == {"fallback": True}

    asyncio.run(_run())
