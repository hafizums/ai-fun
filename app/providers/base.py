"""Provider interface for media generation backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, BinaryIO


class MediaProvider(ABC):
    """Abstraction over WaveSpeed (and future providers).

    Gate 1 must not call generation models from API routes.
    """

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
    def run_model(self, model: str, input_params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Submit and wait for a model run (later gates)."""

    @abstractmethod
    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        """Fetch prediction status/result by id (later gates)."""
