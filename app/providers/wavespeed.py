"""WaveSpeedAI media provider adapter.

Public SDK surface used (wavespeed 1.0.9):
- Client(
    api_key=...,
    base_url=...,
    max_retries=...,
    max_connection_retries=...,
  )
- Client.upload(file)
- Client.run(model, input, timeout=..., poll_interval=..., enable_sync_mode=...,
             max_retries=...)

Private methods are never called.

Retry policy:
- Upload client may keep SDK connection-retry defaults (upload is not a paid
  model prediction). In 1.0.9, Client.upload() itself is a single POST.
- Generation client always uses max_retries=0 and max_connection_retries=0 so
  a lost response cannot trigger a second paid submission POST.
- run_model always passes max_retries=0 at the task layer as well.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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

ClientFactory = Callable[..., Any]


def _default_client_factory(**kwargs: Any) -> Any:
    from wavespeed import Client

    return Client(**kwargs)


class WaveSpeedProvider(MediaProvider):
    """Adapter around the official wavespeed.Client (media API base URL only)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or "").rstrip("/")
        self._client_factory = client_factory or _default_client_factory
        self._upload_client: Any | None = None
        self._generation_client: Any | None = None

    @property
    def api_base_url(self) -> str:
        """Media API base URL passed to the WaveSpeed SDK."""
        return self._base_url

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _require_api_key(self) -> None:
        if not self._api_key:
            raise ProviderConfigurationError(
                "WAVESPEED_API_KEY is not set. Add it to your local .env file."
            )

    def _ensure_upload_client(self) -> Any:
        """Client for file uploads — may retain bounded connection retries."""
        self._require_api_key()
        if self._upload_client is None:
            try:
                # Leave max_connection_retries at SDK default (5 in 1.0.9).
                # Upload is not a paid prediction; connection retries are safe.
                self._upload_client = self._client_factory(
                    api_key=self._api_key,
                    base_url=self._base_url,
                )
            except ImportError as exc:
                raise ProviderConfigurationError(
                    "wavespeed package is not installed"
                ) from exc
        return self._upload_client

    def _ensure_generation_client(self) -> Any:
        """Client for paid model runs — both retry layers forced to zero."""
        self._require_api_key()
        if self._generation_client is None:
            try:
                self._generation_client = self._client_factory(
                    api_key=self._api_key,
                    base_url=self._base_url,
                    max_retries=0,
                    max_connection_retries=0,
                )
            except ImportError as exc:
                raise ProviderConfigurationError(
                    "wavespeed package is not installed"
                ) from exc
        return self._generation_client

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
            self._ensure_generation_client()
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
        client = self._ensure_upload_client()
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
        max_task_retries: int = 0,
    ) -> MediaRunResult:
        """Run a model via public Client.run and return a validated app result.

        Paid generation never resubmits automatically. ``max_task_retries`` defaults
        to 0 and is clamped to 0 for safety (explicit user retries happen at the
        application claim layer).
        """
        if max_task_retries != 0:
            logger.warning(
                "Ignoring non-zero max_task_retries for paid generation; forcing 0"
            )
            max_task_retries = 0

        client = self._ensure_generation_client()
        try:
            raw = client.run(
                model,
                input_params or {},
                timeout=timeout,
                poll_interval=poll_interval,
                enable_sync_mode=enable_sync_mode,
                max_retries=max_task_retries,
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
