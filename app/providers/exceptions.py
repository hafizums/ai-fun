"""Sanitized provider exceptions (never include API keys)."""

from __future__ import annotations


class ProviderError(Exception):
    """Base provider error with a safe public message."""

    def __init__(self, message: str, *, code: str = "PROVIDER_ERROR") -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def public_dict(self) -> dict[str, str]:
        return {"error_code": self.code, "error_message": self.message}


class ProviderConfigurationError(ProviderError):
    def __init__(self, message: str = "Provider is not configured") -> None:
        super().__init__(message, code="PROVIDER_CONFIGURATION_ERROR")


class ProviderAuthenticationError(ProviderError):
    def __init__(self, message: str = "Provider authentication failed") -> None:
        super().__init__(message, code="PROVIDER_AUTHENTICATION_ERROR")


class ProviderRequestError(ProviderError):
    def __init__(self, message: str = "Provider request failed") -> None:
        super().__init__(message, code="PROVIDER_REQUEST_ERROR")


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str = "Provider request timed out") -> None:
        super().__init__(message, code="PROVIDER_TIMEOUT_ERROR")


def sanitize_provider_message(raw: str, *, api_key: str | None = None) -> str:
    """Remove API key material from an exception/message string."""
    text = str(raw)
    if api_key and api_key.strip():
        text = text.replace(api_key, "[REDACTED]")
    # Common header leakage patterns
    lowered = text
    for token in ("Bearer ", "bearer "):
        if token in lowered:
            # Redact anything after Bearer up to whitespace
            parts = text.split(token)
            rebuilt = parts[0]
            for part in parts[1:]:
                rest = part.split(None, 1)
                rebuilt += token + "[REDACTED]"
                if len(rest) > 1:
                    rebuilt += " " + rest[1]
            text = rebuilt
    return text
