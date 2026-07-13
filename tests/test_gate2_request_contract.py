"""Prompt request schema and system prompt contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.prompts import (
    DEFAULT_MOTION,
    DEFAULT_SCENE,
    DEFAULT_SUBJECT,
    MVP_DURATION_SECONDS,
    PromptGenerationRequest,
)
from app.services.prompt_contract import SYSTEM_PROMPT, build_chat_messages


def test_default_request_values() -> None:
    req = PromptGenerationRequest()
    assert req.subject_description == DEFAULT_SUBJECT
    assert req.scene_description == DEFAULT_SCENE
    assert req.motion_description == DEFAULT_MOTION
    assert req.duration_seconds == MVP_DURATION_SECONDS == 5


def test_request_rejects_blank_strings() -> None:
    with pytest.raises(ValidationError):
        PromptGenerationRequest(subject_description="   ")
    with pytest.raises(ValidationError):
        PromptGenerationRequest(scene_description="")
    with pytest.raises(ValidationError):
        PromptGenerationRequest(motion_description="\n\t")


def test_request_rejects_excessive_lengths() -> None:
    with pytest.raises(ValidationError):
        PromptGenerationRequest(subject_description="x" * 501)


def test_system_prompt_contains_mandatory_requirements() -> None:
    lowered = SYSTEM_PROMPT.lower()
    assert "9:16" in SYSTEM_PROMPT
    assert "exactly one" in lowered or "exactly ONE" in SYSTEM_PROMPT
    assert "static" in lowered and "camera" in lowered
    assert "age-appropriate" in lowered
    assert "hand" in lowered and ("occlusion" in lowered or "cross" in lowered)
    assert "background" in lowered and "preserve" in lowered
    messages = build_chat_messages(PromptGenerationRequest())
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
