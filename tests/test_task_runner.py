"""Local task runner tests."""

from __future__ import annotations

import threading
import time

from app.services.task_runner import TaskRunner


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
