"""WaveSpeedAI provider adapter.

Verified against wavespeed Python SDK public surface:
- Client(api_key=..., base_url=...)
- Client.upload(file) -> download URL string
- Client.run(model, input) -> {"outputs": [...]}

Gate 1 uses WAVESPEED_API_BASE_URL for the media SDK only.
WAVESPEED_LLM_BASE_URL is retained in settings for Gate 2 and is never passed
to this provider.

get_prediction is deferred: the public SDK has no polling helper, and Gate 1
does not rely on private SDK methods.

Safe check for POST /api/settings/test-wavespeed:
- Configuration-only: key present + Client constructible.
- No private SDK helpers; no paid or generative network request.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, BinaryIO

from app.providers.base import MediaProvider
from app.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRequestError,
    ProviderTimeoutError,
    sanitize_provider_message,
)

logger = logging.getLogger(__name__)


class WaveSpeedProvider(MediaProvider):
    """Adapter around the official wavespeed.Client (media API base URL only)."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or "").rstrip("/")
        self._client: Any | None = None

    @property
    def api_base_url(self) -> str:
        """Media API base URL passed to the WaveSpeed SDK."""
        return self._base_url

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self) -> Any:
        if not self._api_key:
            raise ProviderConfigurationError(
                "WAVESPEED_API_KEY is not set. Add it to your local .env file."
            )
        if self._client is None:
            try:
                from wavespeed import Client
            except ImportError as exc:
                raise ProviderConfigurationError(
                    "wavespeed package is not installed"
                ) from exc
            self._client = Client(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def check_configuration(self) -> dict[str, Any]:
        """Configuration-only check — no network generation request.

        Constructing the SDK client is sufficient. Private helpers such as
        _get_headers() are not used.
        """
        if not self.is_configured():
            return {
                "ok": False,
                "mode": "configuration_only",
                "configured": False,
                "message": (
                    "WAVESPEED_API_KEY is missing. Provider generation is unavailable "
                    "until a key is configured locally."
                ),
            }

        try:
            self._ensure_client()
        except ProviderError:
            raise
        except Exception as exc:
            raise self._map_exception(exc) from exc

        return {
            "ok": True,
            "mode": "configuration_only",
            "configured": True,
            "base_url": self._base_url,
            "message": (
                "WaveSpeed API key is present and the media SDK client can be "
                "constructed. No network authentication probe was performed because "
                "the SDK does not expose a verified lightweight auth-only endpoint. "
                "Generation calls are deferred to later gates."
            ),
        }

    def upload_file(self, file: str | Path | BinaryIO) -> str:
        """Upload via Client.upload (verified SDK method). Not used by Gate 1 routes."""
        client = self._ensure_client()
        try:
            path_or_file: str | BinaryIO
            if isinstance(file, Path):
                path_or_file = str(file)
            else:
                path_or_file = file
            return str(client.upload(path_or_file))
        except Exception as exc:
            raise self._map_exception(exc) from exc

    def run_model(self, model: str, input_params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a model via Client.run (verified). Not invoked by Gate 1 routes."""
        client = self._ensure_client()
        try:
            result = client.run(model, input_params or {})
            if not isinstance(result, dict):
                return {"outputs": result}
            return result
        except Exception as exc:
            raise self._map_exception(exc) from exc

    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        """Deferred until a public or intentionally implemented polling API exists.

        The public wavespeed SDK does not expose get_prediction(). Gate 1 does not
        call private Client methods (_get_result, etc.).
        """
        raise ProviderConfigurationError(
            "get_prediction is not available in Gate 1. The public WaveSpeed SDK "
            "does not expose a prediction polling method, and private SDK methods "
            "are not used. Implement polling in a later gate when a supported "
            "public mechanism is available."
        )

    def _map_exception(self, exc: Exception) -> ProviderError:
        message = sanitize_provider_message(str(exc), api_key=self._api_key)
        name = type(exc).__name__
        lowered = message.lower()

        if isinstance(exc, TimeoutError) or "timed out" in lowered or name == "Timeout":
            return ProviderTimeoutError(f"Provider timed out: {message}")

        if "api key" in lowered or "unauthorized" in lowered or "401" in lowered:
            return ProviderAuthenticationError("Provider authentication failed")

        if isinstance(exc, ValueError) and "api key" in lowered:
            return ProviderConfigurationError("Provider API key is not configured")

        logger.error("Provider error mapped: %s", message)
        return ProviderRequestError(f"Provider request failed: {message}")
