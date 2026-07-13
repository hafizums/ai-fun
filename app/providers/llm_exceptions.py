"""Sanitized LLM provider exceptions (never include secrets or raw bodies)."""

from __future__ import annotations


class LLMError(Exception):
    """Base LLM error with a fixed public message and stable error code."""

    code: str = "LLM_REQUEST_FAILED"
    public_message: str = "The language model request failed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.public_message
        super().__init__(self.message)


class LLMConfigurationError(LLMError):
    code = "LLM_NOT_CONFIGURED"
    public_message = "The language model provider is not configured."


class LLMAuthenticationError(LLMError):
    code = "LLM_AUTHENTICATION_FAILED"
    public_message = "Language model authentication failed."


class LLMTimeoutError(LLMError):
    code = "LLM_TIMEOUT"
    public_message = "The language model request timed out."


class LLMConnectionError(LLMError):
    code = "LLM_CONNECTION_FAILED"
    public_message = "Could not connect to the language model provider."


class LLMRequestError(LLMError):
    code = "LLM_REQUEST_FAILED"
    public_message = "The language model request failed."


class LLMInvalidResponseError(LLMError):
    code = "LLM_INVALID_RESPONSE"
    public_message = "The language model returned an invalid response."
