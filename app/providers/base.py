"""Provider interface for media generation backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


@dataclass(frozen=True)
class MediaRunResult:
    """Application-owned media generation result (never expose SDK objects)."""

    output_urls: tuple[str, ...]
    prediction_id: str | None
    model: str | None


class MediaProvider(ABC):
    """Abstraction over WaveSpeed (and future providers)."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when credentials appear present (no network call)."""

    @abstractmethod
    def check_configuration(self) -> dict[str, Any]:
        """Safe configuration/auth check without paid generation."""

    @abstractmethod
    def upload_file(self, file: str | Path | BinaryIO) -> str:
        """Upload a local file and return a provider URL."""

    @abstractmethod
    def run_model(
        self,
        model: str,
        input_params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        enable_sync_mode: bool = False,
        max_task_retries: int = 0,
    ) -> MediaRunResult:
        """Submit and wait for a model run using public SDK methods only.

        ``max_task_retries`` defaults to 0. Paid generation must not automatically
        resubmit a model POST after timeout or connection loss.
        """

    @abstractmethod
    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        """Fetch prediction status/result by id (deferred)."""
