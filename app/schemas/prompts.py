"""Prompt package and generation request schemas."""

from __future__ import annotations

import enum
import math
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

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


def _require_nonblank_string(value: object, *, field_name: str) -> str:
    """Accept only real strings; reject None and non-string types."""
    if value is None:
        raise ValueError(f"{field_name} must not be null")
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = _normalize_text(value)
    if not text:
        raise ValueError(f"{field_name} must not be blank")
    return text


def _require_finite_number(value: object, *, field_name: str) -> float:
    """Accept finite int/float only; reject bool, NaN, and infinities."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


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
    def _normalize_and_require(cls, value: object, info: ValidationInfo) -> str:
        text = _require_nonblank_string(value, field_name=str(info.field_name))
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
        return _require_nonblank_string(value, field_name="event_description")

    @field_validator("start_seconds", "end_seconds", mode="before")
    @classmethod
    def _finite_seconds(cls, value: object, info: ValidationInfo) -> float:
        return _require_finite_number(value, field_name=str(info.field_name))

    @model_validator(mode="after")
    def _timing_order(self) -> TransitionHint:
        if self.start_seconds < 0:
            raise ValueError("start_seconds must be >= 0")
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


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
    def _normalize_prompts(cls, value: object, info: ValidationInfo) -> str:
        return _require_nonblank_string(value, field_name=str(info.field_name))

    def validate_timing(self, duration_seconds: int) -> None:
        hint = self.transition_hint
        if not math.isfinite(hint.start_seconds) or not math.isfinite(hint.end_seconds):
            raise ValueError("transition timing values must be finite")
        if hint.start_seconds < 0:
            raise ValueError("start_seconds must be >= 0")
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
