"""Prompt package and generation request schemas."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_SUBJECT = "one young Asian child looking directly at the camera"
DEFAULT_SCENE = "a simple ordinary indoor room with soft natural daylight"
DEFAULT_MOTION = "a quick playful hand flick that briefly crosses the face"
MVP_DURATION_SECONDS = 5

MAX_REQUEST_FIELD_LEN = 500
MAX_PROMPT_FIELD_LEN = 4000
MAX_EVENT_DESC_LEN = 500
MIN_PROMPT_LEN = 1


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


class PreferredTransition(enum.StrEnum):
    HARD_CUT = "hard_cut"
    SHORT_CROSSFADE = "short_crossfade"
    FLASH = "flash"


class PromptGenerationRequest(BaseModel):
    """Validated request for prompt generation."""

    model_config = ConfigDict(extra="forbid")

    subject_description: str = Field(default=DEFAULT_SUBJECT, max_length=MAX_REQUEST_FIELD_LEN)
    scene_description: str = Field(default=DEFAULT_SCENE, max_length=MAX_REQUEST_FIELD_LEN)
    motion_description: str = Field(default=DEFAULT_MOTION, max_length=MAX_REQUEST_FIELD_LEN)
    duration_seconds: Literal[5] = MVP_DURATION_SECONDS

    @field_validator(
        "subject_description",
        "scene_description",
        "motion_description",
        mode="before",
    )
    @classmethod
    def _normalize_and_require(cls, value: object) -> str:
        if value is None:
            raise ValueError("Field is required")
        text = _normalize_text(str(value))
        if not text:
            raise ValueError("Field must not be blank")
        if len(text) > MAX_REQUEST_FIELD_LEN:
            raise ValueError(f"Field exceeds maximum length of {MAX_REQUEST_FIELD_LEN}")
        return text


class TransitionHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_description: str = Field(min_length=MIN_PROMPT_LEN, max_length=MAX_EVENT_DESC_LEN)
    start_seconds: float = Field(ge=0)
    end_seconds: float
    preferred_transition: PreferredTransition

    @field_validator("event_description", mode="before")
    @classmethod
    def _normalize_event(cls, value: object) -> str:
        text = _normalize_text(str(value))
        if not text:
            raise ValueError("event_description must not be blank")
        return text


class PromptPackage(BaseModel):
    """Strict LLM prompt package (prompts object only)."""

    model_config = ConfigDict(extra="forbid")

    image_prompt: str = Field(min_length=MIN_PROMPT_LEN, max_length=MAX_PROMPT_FIELD_LEN)
    edit_prompt: str = Field(min_length=MIN_PROMPT_LEN, max_length=MAX_PROMPT_FIELD_LEN)
    motion_prompt: str = Field(min_length=MIN_PROMPT_LEN, max_length=MAX_PROMPT_FIELD_LEN)
    motion_negative_prompt: str = Field(
        min_length=MIN_PROMPT_LEN, max_length=MAX_PROMPT_FIELD_LEN
    )
    transition_hint: TransitionHint

    @field_validator(
        "image_prompt",
        "edit_prompt",
        "motion_prompt",
        "motion_negative_prompt",
        mode="before",
    )
    @classmethod
    def _normalize_prompts(cls, value: object) -> str:
        text = _normalize_text(str(value))
        if not text:
            raise ValueError("Prompt field must not be blank")
        return text

    def validate_timing(self, duration_seconds: int) -> None:
        hint = self.transition_hint
        if hint.end_seconds <= hint.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        if hint.end_seconds > duration_seconds:
            raise ValueError("end_seconds must be <= duration_seconds")


class PromptRequestSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_description: str
    scene_description: str
    motion_description: str
    duration_seconds: int


class PromptMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["wavespeed"] = "wavespeed"
    model: str
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    generated_at: datetime


class PromptEnvelope(BaseModel):
    """Canonical stored prompt envelope."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    request: PromptRequestSnapshot
    prompts: PromptPackage
    metadata: PromptMetadata

    @model_validator(mode="after")
    def _check_timing(self) -> PromptEnvelope:
        self.prompts.validate_timing(self.request.duration_seconds)
        return self
