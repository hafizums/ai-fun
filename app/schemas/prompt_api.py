"""API response schemas for prompt envelopes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.prompts import PreferredTransition


class TransitionHintResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_description: str
    start_seconds: float
    end_seconds: float
    preferred_transition: PreferredTransition


class PromptPackageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_prompt: str
    edit_prompt: str
    motion_prompt: str
    motion_negative_prompt: str
    transition_hint: TransitionHintResponse


class PromptRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_description: str
    scene_description: str
    motion_description: str
    duration_seconds: int


class PromptMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    generated_at: datetime


class PromptEnvelopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    request: PromptRequestResponse
    prompts: PromptPackageResponse
    metadata: PromptMetadataResponse


class GeneratePromptsAcceptedResponse(BaseModel):
    id: str
    status: str
    current_stage: str | None = None
    progress_percent: int = Field(default=0)
    detail: str = "Prompt generation accepted"
