"""WaveSpeedAI provider adapter.

Verified against wavespeed Python SDK 1.0.9 (Client.run, Client.upload).

Public SDK surface used here:
- Client(api_key=..., base_url=...)
- Client.upload(file) -> download URL string
- Client.run(model, input) -> {"outputs": [...]}

Prediction polling:
- The public SDK does not expose get_prediction().
- Client._get_result(request_id) hits GET /api/v3/predictions/{id}/result
  (verified in installed package source). Gate 1 wraps that verified endpoint
  for the abstraction; generation is not invoked from Gate 1 routes.

Safe check for POST /api/settings/test-wavespeed:
- No lightweight authenticated account endpoint is documented in the SDK.
- Gate 1 therefore returns a configuration-only result (key present + client
  constructible) without making a paid or generative request.
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
    """Adapter around the official wavespeed.Client."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or "").rstrip("/")
        self._client: Any | None = None

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

        The installed WaveSpeed SDK (1.0.9) exposes Client.run / Client.upload
        but no documented lightweight authenticated probe. Returning a clearly
        labeled configuration-only result avoids inventing an API call.
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
            client = self._ensure_client()
            # Touch headers builder to confirm key is accepted by the SDK
            # without performing a network request.
            headers = client._get_headers()
            auth = headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                raise ProviderAuthenticationError("Provider auth header was not formed")
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
                "WaveSpeed API key is present and the client can be constructed. "
                "No network authentication probe was performed because the SDK "
                "does not expose a verified lightweight auth-only endpoint. "
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
        """Fetch prediction result via verified Client._get_result endpoint wrapper.

        Public SDK has no get_prediction(); this uses the installed client's
        result endpoint helper. Deferred for actual use until later gates.
        """
        if not prediction_id or ".." in prediction_id:
            raise ProviderRequestError("Invalid prediction id")
        client = self._ensure_client()
        try:
            get_result = getattr(client, "_get_result", None)
            if get_result is None:
                raise ProviderConfigurationError(
                    "Installed wavespeed Client does not expose a verified "
                    "prediction result method; defer get_prediction to a later gate."
                )
            result = get_result(prediction_id)
            if not isinstance(result, dict):
                return {"data": result}
            return result
        except ProviderError:
            raise
        except Exception as exc:
            raise self._map_exception(exc) from exc

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
