"""WaveSpeedAI media provider adapter.

Public SDK surface used:
- Client(api_key=..., base_url=...)
- Client.upload(file)
- Client.run(model, input, timeout=..., poll_interval=..., enable_sync_mode=...)

Private methods are never called.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

from app.providers.base import MediaProvider, MediaRunResult
from app.providers.exceptions import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderError,
    ProviderRequestError,
    ProviderTimeoutError,
)
from app.providers.media_exceptions import (
    MediaAuthenticationError,
    MediaConfigurationError,
    MediaConnectionError,
    MediaError,
    MediaInvalidResultError,
    MediaRequestError,
    MediaTimeoutError,
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
        """Configuration-only check — no network generation request."""
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
            raise self._map_provider_exception(exc) from None

        return {
            "ok": True,
            "mode": "configuration_only",
            "configured": True,
            "base_url": self._base_url,
            "message": (
                "WaveSpeed API key is present and the media SDK client can be "
                "constructed. No network authentication probe was performed because "
                "the SDK does not expose a verified lightweight auth-only endpoint."
            ),
        }

    def upload_file(self, file: str | Path | BinaryIO) -> str:
        client = self._ensure_client()
        try:
            path_or_file: str | BinaryIO
            if isinstance(file, Path):
                path_or_file = str(file)
            else:
                path_or_file = file
            return str(client.upload(path_or_file))
        except Exception as exc:
            raise self._map_provider_exception(exc) from None

    def run_model(
        self,
        model: str,
        input_params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        enable_sync_mode: bool = False,
    ) -> MediaRunResult:
        """Run a model via public Client.run and return a validated app result."""
        client = self._ensure_client()
        try:
            raw = client.run(
                model,
                input_params or {},
                timeout=timeout,
                poll_interval=poll_interval,
                enable_sync_mode=enable_sync_mode,
            )
        except Exception as exc:
            raise self._map_media_exception(exc) from None

        try:
            return validate_media_run_result(raw, model=model)
        except MediaError:
            raise
        except Exception:
            logger.error(
                "Failed to validate media result exception_class=Unexpected"
            )
            raise MediaInvalidResultError() from None

    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        raise ProviderConfigurationError(
            "get_prediction is not available. The public WaveSpeed SDK does not "
            "expose a prediction polling method, and private SDK methods are not used."
        )

    def _map_provider_exception(self, exc: Exception) -> ProviderError:
        logger.error(
            "Media provider call failed exception_class=%s",
            type(exc).__name__,
        )
        name = type(exc).__name__
        lowered = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timed out" in lowered or name == "Timeout":
            return ProviderTimeoutError("Provider timed out")
        if "api key" in lowered or "unauthorized" in lowered or "401" in lowered:
            return ProviderAuthenticationError("Provider authentication failed")
        if isinstance(exc, ValueError) and "api key" in lowered:
            return ProviderConfigurationError("Provider API key is not configured")
        if "connection" in lowered:
            return ProviderRequestError("Provider request failed")
        return ProviderRequestError("Provider request failed")

    def _map_media_exception(self, exc: Exception) -> MediaError:
        logger.error(
            "Media generation failed exception_class=%s",
            type(exc).__name__,
        )
        name = type(exc).__name__
        lowered = str(exc).lower()
        if isinstance(exc, TimeoutError) or "timed out" in lowered or name == "Timeout":
            return MediaTimeoutError()
        if "api key" in lowered or "unauthorized" in lowered or "401" in lowered:
            return MediaAuthenticationError()
        if isinstance(exc, ValueError) and "api key" in lowered:
            return MediaConfigurationError()
        if "connection" in lowered:
            return MediaConnectionError()
        return MediaRequestError()


def validate_media_run_result(raw: Any, *, model: str | None = None) -> MediaRunResult:
    """Validate a public SDK run() return value into MediaRunResult."""
    if not isinstance(raw, dict):
        raise MediaInvalidResultError()
    outputs = raw.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise MediaInvalidResultError()

    urls: list[str] = []
    for item in outputs:
        if not isinstance(item, str) or not item.strip():
            raise MediaInvalidResultError()
        url = item.strip()
        if url.lower().startswith("data:"):
            raise MediaInvalidResultError()
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise MediaInvalidResultError()
        urls.append(url)

    prediction_id = raw.get("id") or raw.get("prediction_id") or raw.get("task_id")
    if prediction_id is not None and not isinstance(prediction_id, str):
        prediction_id = None
    result_model = raw.get("model") if isinstance(raw.get("model"), str) else model
    return MediaRunResult(
        output_urls=tuple(urls),
        prediction_id=prediction_id,
        model=result_model,
    )
