"""LLM provider configuration and mapping tests (offline)."""

from __future__ import annotations

import io
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.providers.llm_exceptions import (
    LLMAuthenticationError,
    LLMRequestError,
    LLMTimeoutError,
)
from app.providers.wavespeed_llm import WaveSpeedLLMProvider
from app.schemas.prompts import PromptGenerationRequest


def test_llm_provider_uses_llm_base_url_not_media() -> None:
    provider = WaveSpeedLLMProvider(
        api_key="secret-key",
        base_url="https://llm.wavespeed.ai/v1",
        model="openai/gpt-5.1",
        timeout_seconds=120,
    )
    assert provider.base_url == "https://llm.wavespeed.ai/v1"
    assert "api.wavespeed.ai" not in provider.base_url


def test_llm_provider_uses_configured_model() -> None:
    provider = WaveSpeedLLMProvider(
        api_key="secret-key",
        base_url="https://llm.wavespeed.ai/v1",
        model="openai/gpt-5.1",
        timeout_seconds=30,
    )
    assert provider.model == "openai/gpt-5.1"


def test_llm_provider_uses_api_key_without_exposing_it() -> None:
    secret = "ws-llm-secret-do-not-leak"
    provider = WaveSpeedLLMProvider(
        api_key=secret,
        base_url="https://llm.wavespeed.ai/v1",
        model="openai/gpt-5.1",
        timeout_seconds=30,
    )
    assert provider.is_configured()
    assert secret not in repr(provider)
    # Ensure client construction receives key but public attrs do not leak it.
    with patch("openai.OpenAI") as openai_cls:
        client = MagicMock()
        openai_cls.return_value = client
        completion = MagicMock()
        completion.choices = [MagicMock(message=MagicMock(content="{}"))]
        completion.id = "id1"
        completion.model = "openai/gpt-5.1"
        completion.usage = MagicMock(prompt_tokens=1, completion_tokens=2)
        client.chat.completions.create.return_value = completion
        provider.generate_prompt_completion(PromptGenerationRequest())
        kwargs = openai_cls.call_args.kwargs
        assert kwargs["api_key"] == secret
        assert kwargs["base_url"] == "https://llm.wavespeed.ai/v1"
        create_kwargs = client.chat.completions.create.call_args.kwargs
        assert create_kwargs["model"] == "openai/gpt-5.1"


def test_provider_timeout_maps_safely() -> None:
    from openai import APITimeoutError

    provider = WaveSpeedLLMProvider(
        api_key="k",
        base_url="https://llm.wavespeed.ai/v1",
        model="openai/gpt-5.1",
        timeout_seconds=1,
    )
    with patch.object(provider, "_ensure_client") as ensure:
        client = MagicMock()
        ensure.return_value = client
        client.chat.completions.create.side_effect = APITimeoutError(request=MagicMock())
        with pytest.raises(LLMTimeoutError) as exc:
            provider.generate_prompt_completion(PromptGenerationRequest())
        assert exc.value.code == "LLM_TIMEOUT"
        assert "k" not in exc.value.message


def test_authentication_failure_maps_safely() -> None:
    from openai import AuthenticationError

    provider = WaveSpeedLLMProvider(
        api_key="secret-auth-key",
        base_url="https://llm.wavespeed.ai/v1",
        model="openai/gpt-5.1",
        timeout_seconds=1,
    )
    with patch.object(provider, "_ensure_client") as ensure:
        client = MagicMock()
        ensure.return_value = client
        client.chat.completions.create.side_effect = AuthenticationError(
            message="bad key secret-auth-key",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
        with pytest.raises(LLMAuthenticationError) as exc:
            provider.generate_prompt_completion(PromptGenerationRequest())
        assert exc.value.code == "LLM_AUTHENTICATION_FAILED"
        assert "secret-auth-key" not in exc.value.message


def test_provider_exception_with_fake_key_not_logged() -> None:
    fake_key = "ws-fake-llm-key-ABCDEF-do-not-leak"
    provider = WaveSpeedLLMProvider(
        api_key=fake_key,
        base_url="https://llm.wavespeed.ai/v1",
        model="openai/gpt-5.1",
        timeout_seconds=1,
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
    log = logging.getLogger("app.providers.wavespeed_llm")
    log.addHandler(handler)
    log.setLevel(logging.ERROR)
    prev = log.propagate
    log.propagate = False
    try:
        with patch.object(provider, "_ensure_client") as ensure:
            client = MagicMock()
            ensure.return_value = client
            client.chat.completions.create.side_effect = RuntimeError(
                f"Authorization: Bearer {fake_key}"
            )
            with pytest.raises(LLMRequestError):
                provider.generate_prompt_completion(PromptGenerationRequest())
            formatted = stream.getvalue()
            assert fake_key not in formatted
            assert "Bearer" not in formatted
    finally:
        log.removeHandler(handler)
        log.propagate = prev
        handler.close()
