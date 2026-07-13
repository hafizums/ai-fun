"""LLM provider interface for OpenAI-compatible chat completions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.schemas.prompts import PromptGenerationRequest


@dataclass(frozen=True)
class LLMCompletionResult:
    """Application-owned completion result (never expose SDK response objects)."""

    content: str | None
    response_id: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None


class LLMProvider(ABC):
    """Abstraction for WaveSpeed OpenAI-compatible LLM access."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True when credentials appear present (no network call)."""

    @abstractmethod
    def generate_prompt_completion(
        self, request: PromptGenerationRequest
    ) -> LLMCompletionResult:
        """Perform one chat completion for prompt generation."""
