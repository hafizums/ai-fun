"""WaveSpeed provider safety tests (no paid requests)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.providers.exceptions import ProviderRequestError, sanitize_provider_message
from app.providers.wavespeed import WaveSpeedProvider


def test_missing_wavespeed_key_reported_safely(client: TestClient) -> None:
    response = client.post("/api/settings/test-wavespeed")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["configured"] is False
    assert body["mode"] == "configuration_only"
    assert "WAVESPEED_API_KEY" in body["message"] or "missing" in body["message"].lower()
    # Never echo a key field
    assert "api_key" not in body
    raw = response.text.lower()
    assert "sk-" not in raw


def test_provider_errors_do_not_expose_api_key() -> None:
    secret = "ws-super-secret-key-do-not-leak"
    provider = WaveSpeedProvider(api_key=secret, base_url="https://api.wavespeed.ai")

    sanitized = sanitize_provider_message(
        f"Authorization: Bearer {secret} failed",
        api_key=secret,
    )
    assert secret not in sanitized
    assert "[REDACTED]" in sanitized

    mapped = provider._map_exception(RuntimeError(f"boom key={secret}"))
    assert isinstance(mapped, ProviderRequestError)
    assert secret not in mapped.message
    assert secret not in str(mapped)


def test_configured_key_configuration_only_mode() -> None:
    provider = WaveSpeedProvider(api_key="test-local-key", base_url="https://api.wavespeed.ai")
    result = provider.check_configuration()
    assert result["ok"] is True
    assert result["mode"] == "configuration_only"
    assert result["configured"] is True
    assert "test-local-key" not in str(result)
