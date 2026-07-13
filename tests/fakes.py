"""Deterministic fake LLM providers and helpers for Gate 2 tests."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

from app.providers.llm_base import LLMCompletionResult, LLMProvider
from app.providers.llm_exceptions import (
    LLMAuthenticationError,
    LLMTimeoutError,
)
from app.schemas.prompts import PromptGenerationRequest

VALID_PACKAGE: dict[str, Any] = {
    "image_prompt": (
        "Photorealistic vertical 9:16 medium close-up of one young child, "
        "direct eye contact, static eye-level smartphone camera, relaxed shoulders, "
        "clear anatomically plausible hands, simple indoor background, soft natural "
        "light, ordinary age-appropriate clothing, no text or watermark."
    ),
    "edit_prompt": (
        "Image 1 is the composition and background canvas. Image 2 is identity "
        "reference only. Replace only the character. Preserve Image 1 background, "
        "crop, camera, pose, hand location, lighting, shadows, and aspect ratio. "
        "Do not copy Image 2 background or change background objects."
    ),
    "motion_prompt": (
        "Static camera, direct eye contact, subtle idle motion, one clear hand flick "
        "near the middle of the five-second clip that briefly crosses the face, then "
        "return to a stable pose. No scene change or background movement."
    ),
    "motion_negative_prompt": (
        "camera motion, scene changes, face instability, identity drift, extra limbs, "
        "fused hands, malformed anatomy, background warping, clothing changes, added "
        "people, text, watermark, excessive motion blur"
    ),
    "transition_hint": {
        "event_description": "brief hand occlusion across the face during the flick",
        "start_seconds": 2.0,
        "end_seconds": 3.0,
        "preferred_transition": "hard_cut",
    },
}


def package_json(**overrides: Any) -> str:
    data = json.loads(json.dumps(VALID_PACKAGE))
    for key, value in overrides.items():
        if key == "transition_hint" and isinstance(value, dict):
            data["transition_hint"].update(value)
        else:
            data[key] = value
    return json.dumps(data)


class FakeLLMProvider(LLMProvider):
    """Offline deterministic LLM stub."""

    def __init__(
        self,
        *,
        content: str | None | Callable[[PromptGenerationRequest], str | None] = None,
        raise_exc: Exception | None = None,
        delay_event: threading.Event | None = None,
        configured: bool = True,
        model: str = "openai/gpt-5.1",
        base_url: str = "https://llm.wavespeed.ai/v1",
        response_id: str | None = "resp_test_1",
        input_tokens: int | None = 10,
        output_tokens: int | None = 20,
    ) -> None:
        self._content = content if content is not None else package_json()
        self._raise_exc = raise_exc
        self._delay_event = delay_event
        self._configured = configured
        self._model = model
        self.base_url = base_url
        self.model = model
        self.calls: list[PromptGenerationRequest] = []
        self.session_open_marker: list[str] = []
        self._response_id = response_id
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.started = threading.Event()
        self.finished = threading.Event()

    def is_configured(self) -> bool:
        return self._configured

    def generate_prompt_completion(
        self, request: PromptGenerationRequest
    ) -> LLMCompletionResult:
        self.started.set()
        self.calls.append(request)
        if self._delay_event is not None:
            self._delay_event.wait(timeout=5)
        if self._raise_exc is not None:
            self.finished.set()
            raise self._raise_exc
        if callable(self._content):
            content = self._content(request)
        else:
            content = self._content
        self.finished.set()
        return LLMCompletionResult(
            content=content,
            response_id=self._response_id,
            model=self._model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )


def wait_for_job_status(
    client: Any,
    job_id: str,
    statuses: set[str],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in statuses:
            return last
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {statuses}; last={last}")


def install_fake_llm(app: Any, fake: FakeLLMProvider) -> FakeLLMProvider:
    """Replace the app LLM provider used by PromptGenerationService."""
    app.state.llm = fake
    app.state.prompt_generation._llm = fake
    app.state.prompt_generation._llm_model = fake.model
    return fake


# Re-export exception helpers for tests
__all__ = [
    "VALID_PACKAGE",
    "FakeLLMProvider",
    "LLMAuthenticationError",
    "LLMTimeoutError",
    "install_fake_llm",
    "package_json",
    "wait_for_job_status",
]
