"""In-process single-worker (configurable) task runner using ThreadPoolExecutor."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Fixed safe description — never include exception args/message (may contain secrets).
_SAFE_TASK_FAILURE_MESSAGE = (
    "Background task failed. Exception details are withheld from logs to avoid "
    "secret leakage; the error is available on the returned Future."
)


class TaskRunner:
    """Non-persistent in-process background task executor.

    Tasks do not survive process restarts. Callers must not block HTTP handlers
    on long-running work — use submit() and return immediately.
    """

    def __init__(self, max_workers: int = 1) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._started = False

    @property
    def is_running(self) -> bool:
        return self._started and self._executor is not None

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="local-task",
            )
            self._started = True
            logger.info("Local task runner started (workers=%s)", self._max_workers)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
            self._started = False
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=False)
            logger.info("Local task runner shut down")

    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future[T]:
        """Submit work to the pool. Exceptions are caught at the task boundary."""
        if not self.is_running or self._executor is None:
            raise RuntimeError("Task runner is not running")

        def _wrapped() -> T:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                # Do not use logger.exception / exc_info: formatted tracebacks can
                # embed secret-bearing exception messages.
                logger.error(
                    "%s exception_class=%s",
                    _SAFE_TASK_FAILURE_MESSAGE,
                    type(exc).__name__,
                )
                raise

        return self._executor.submit(_wrapped)
