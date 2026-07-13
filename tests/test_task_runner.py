"""Local task runner tests."""

from __future__ import annotations

import io
import logging
import threading
import time
from concurrent.futures import Future

import pytest

from app.services.task_runner import _SAFE_TASK_FAILURE_MESSAGE, TaskRunner


def test_background_submission_does_not_block_calling_thread() -> None:
    runner = TaskRunner(max_workers=1)
    runner.start()
    try:
        started = threading.Event()
        release = threading.Event()

        def slow_task() -> str:
            started.set()
            release.wait(timeout=5)
            return "done"

        t0 = time.perf_counter()
        future = runner.submit(slow_task)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.5, "submit() blocked the calling thread"
        assert started.wait(timeout=2), "task did not start"
        assert future.done() is False
        release.set()
        assert future.result(timeout=2) == "done"
    finally:
        runner.shutdown(wait=True)


def test_application_shutdown_closes_task_runner_cleanly(client, app) -> None:
    # TestClient lifespan starts the runner.
    runner = app.state.task_runner
    assert runner.is_running
    runner.shutdown(wait=True)
    assert runner.is_running is False
    # Leave runner restarted so the TestClient exit path can shut down cleanly.
    runner.start()
    assert runner.is_running


def test_task_boundary_log_does_not_leak_secret_in_formatted_output() -> None:
    """Inspect fully formatted log output (including any traceback text).

    logger.exception() would attach exc_info; Formatter.format() then appends the
    traceback containing the raw exception message. This test uses a real
    StreamHandler + Formatter and asserts the fake key never appears in that
    rendered text — not merely LogRecord.getMessage().
    """
    fake_key = "ws-fake-key-ABCDEF123456-do-not-leak"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    # Include fields that would surface exception text if exc_info were set.
    handler.setFormatter(
        logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    )
    handler.setLevel(logging.ERROR)

    task_logger = logging.getLogger("app.services.task_runner")
    previous_level = task_logger.level
    task_logger.addHandler(handler)
    task_logger.setLevel(logging.ERROR)
    # Avoid double-propagation into root handlers during the assertion window.
    previous_propagate = task_logger.propagate
    task_logger.propagate = False

    runner = TaskRunner(max_workers=1)
    runner.start()
    try:

        def boom() -> None:
            raise RuntimeError(f"provider failed with Authorization: Bearer {fake_key}")

        future: Future[None] = runner.submit(boom)
        with pytest.raises(RuntimeError) as raised:
            future.result(timeout=5)
        assert fake_key in str(raised.value)

        # Force handler flush and format all records through the real formatter.
        handler.flush()
        formatted = stream.getvalue()
        # Also re-format any records captured via handle to be thorough.
        assert fake_key not in formatted
        assert "Bearer" not in formatted
        assert "Traceback" not in formatted
        assert _SAFE_TASK_FAILURE_MESSAGE in formatted
        assert "exception_class=RuntimeError" in formatted
    finally:
        runner.shutdown(wait=True)
        task_logger.removeHandler(handler)
        task_logger.setLevel(previous_level)
        task_logger.propagate = previous_propagate
        handler.close()
