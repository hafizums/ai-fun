"""WaveSpeed OpenAI-compatible LLM provider (GPT-5.1).

Uses the official public openai.OpenAI client against WAVESPEED_LLM_BASE_URL.
Does not use private SDK methods. Does not assume JSON Schema / response_format.
"""

from __future__ import annotations

import logging
from typing import Any

from app.providers.llm_base import LLMCompletionResult, LLMProvider
from app.providers.llm_exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMRequestError,
    LLMTimeoutError,
)
from app.schemas.prompts import PromptGenerationRequest
from app.services.prompt_contract import build_chat_messages

logger = logging.getLogger(__name__)


class WaveSpeedLLMProvider(LLMProvider):
    """LLM adapter for WaveSpeed's OpenAI-compatible chat/completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or "").rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client: Any | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self) -> Any:
        if not self._api_key:
            raise LLMConfigurationError()
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise LLMConfigurationError() from exc
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout_seconds,
            )
        return self._client

    def generate_prompt_completion(
        self, request: PromptGenerationRequest
    ) -> LLMCompletionResult:
        """Call chat.completions.create once. Never log raw content."""
        client = self._ensure_client()
        messages = build_chat_messages(request)
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:
            raise self._map_exception(exc) from None

        try:
            content = None
            if response.choices:
                message = response.choices[0].message
                content = getattr(message, "content", None)
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage else None
            return LLMCompletionResult(
                content=content if isinstance(content, str) or content is None else str(content),
                response_id=getattr(response, "id", None),
                model=getattr(response, "model", None) or self._model,
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            )
        except LLMError:
            raise
        except Exception:
            logger.error(
                "Failed to read LLM completion fields (details withheld)",
            )
            raise LLMRequestError() from None

    def _map_exception(self, exc: Exception) -> LLMError:
        """Map public OpenAI SDK exceptions to sanitized application errors."""
        try:
            from openai import (
                APIConnectionError,
                APIStatusError,
                APITimeoutError,
                AuthenticationError,
            )
        except ImportError:
            APIConnectionError = ()  # type: ignore[assignment,misc]
            APITimeoutError = ()  # type: ignore[assignment,misc]
            AuthenticationError = ()  # type: ignore[assignment,misc]
            APIStatusError = ()  # type: ignore[assignment,misc]

        # Never log raw provider exception messages (may contain secrets).
        logger.error(
            "LLM provider call failed exception_class=%s",
            type(exc).__name__,
        )

        if isinstance(exc, APITimeoutError) or type(exc).__name__ in {
            "APITimeoutError",
            "TimeoutError",
        }:
            return LLMTimeoutError()
        if isinstance(exc, AuthenticationError) or type(exc).__name__ == "AuthenticationError":
            return LLMAuthenticationError()
        if isinstance(exc, APIConnectionError) or type(exc).__name__ == "APIConnectionError":
            return LLMConnectionError()
        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            if status in {401, 403}:
                return LLMAuthenticationError()
            return LLMRequestError()
        if isinstance(exc, TimeoutError):
            return LLMTimeoutError()
        return LLMRequestError()
