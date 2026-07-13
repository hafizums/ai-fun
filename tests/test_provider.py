"""WaveSpeed provider safety and settings tests (no paid requests)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, clear_settings_cache
from app.providers.exceptions import (
    ProviderConfigurationError,
    ProviderRequestError,
    sanitize_provider_message,
)
from app.providers.wavespeed import WaveSpeedProvider

API_DEFAULT = "https://api.wavespeed.ai"
LLM_DEFAULT = "https://llm.wavespeed.ai/v1"


def test_missing_wavespeed_key_reported_safely(
    client: TestClient, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.settings, "wavespeed_api_key", "")
    app.state.wavespeed = WaveSpeedProvider(
        api_key="",
        base_url="https://api.wavespeed.ai",
    )
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
    provider = WaveSpeedProvider(api_key=secret, base_url=API_DEFAULT)

    sanitized = sanitize_provider_message(
        f"Authorization: Bearer {secret} failed",
        api_key=secret,
    )
    assert secret not in sanitized
    assert "[REDACTED]" in sanitized

    mapped = provider._map_provider_exception(RuntimeError(f"boom key={secret}"))
    assert isinstance(mapped, ProviderRequestError)
    assert secret not in mapped.message
    assert secret not in str(mapped)


def test_configured_key_configuration_only_mode() -> None:
    provider = WaveSpeedProvider(api_key="test-local-key", base_url=API_DEFAULT)
    result = provider.check_configuration()
    assert result["ok"] is True
    assert result["mode"] == "configuration_only"
    assert result["configured"] is True
    assert result["base_url"] == API_DEFAULT
    assert "test-local-key" not in str(result)
    assert "api_key" not in result


def test_wavespeed_base_url_defaults_are_correct_and_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_settings_cache()
    monkeypatch.delenv("WAVESPEED_API_BASE_URL", raising=False)
    monkeypatch.delenv("WAVESPEED_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    # Avoid picking up a local .env that may override defaults during development.
    monkeypatch.setenv("WAVESPEED_API_BASE_URL", API_DEFAULT)
    monkeypatch.setenv("WAVESPEED_LLM_BASE_URL", LLM_DEFAULT)
    settings = Settings()
    assert settings.wavespeed_api_base_url == API_DEFAULT
    assert settings.wavespeed_llm_base_url == LLM_DEFAULT
    assert settings.wavespeed_api_base_url != settings.wavespeed_llm_base_url

    # Field defaults themselves are distinct and match documented values.
    assert Settings.model_fields["wavespeed_api_base_url"].default == API_DEFAULT
    assert Settings.model_fields["wavespeed_llm_base_url"].default == LLM_DEFAULT


def test_media_provider_receives_api_base_url_not_llm(client: TestClient, app) -> None:
    settings = app.state.settings
    provider: WaveSpeedProvider = app.state.wavespeed
    assert provider.api_base_url == settings.wavespeed_api_base_url
    assert provider.api_base_url == API_DEFAULT
    assert provider.api_base_url != settings.wavespeed_llm_base_url
    assert settings.wavespeed_llm_base_url == LLM_DEFAULT

    result = provider.check_configuration()
    assert "api_key" not in result
    assert settings.wavespeed_api_key not in str(result) or not settings.wavespeed_api_key
    # LLM URL must not appear as the provider base_url in config-check response.
    if result.get("base_url"):
        assert result["base_url"] == API_DEFAULT
        assert result["base_url"] != LLM_DEFAULT


def test_get_prediction_is_deferred_without_private_sdk() -> None:
    provider = WaveSpeedProvider(api_key="test-local-key", base_url=API_DEFAULT)
    with pytest.raises(ProviderConfigurationError) as exc_info:
        provider.get_prediction("pred-123")
    message = exc_info.value.message
    assert "Gate 1" in message or "not available" in message.lower()
    assert "test-local-key" not in message
