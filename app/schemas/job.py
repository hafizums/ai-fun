"""Pydantic API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    current_stage: str | None = None
    progress_percent: int = 0
    prompt_json: str | None = None
    base_image_url: str | None = None
    reference_image_path: str | None = None
    edited_image_url: str | None = None
    source_video_url: str | None = None
    controlled_video_url: str | None = None
    transition_time_seconds: float | None = None
    transition_score: float | None = None
    final_video_path: str | None = None
    provider_prediction_ids_json: str | None = None
    failed_stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int


class HealthComponent(BaseModel):
    name: str
    status: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    checks: list[HealthComponent]


class WaveSpeedTestResponse(BaseModel):
    ok: bool
    mode: str
    configured: bool
    message: str
    base_url: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    error_code: str | None = None


class DeleteResponse(BaseModel):
    deleted: bool
    id: str


class TaskSubmitTestResponse(BaseModel):
    accepted: bool = True
    detail: str = Field(
        default="Test task submitted to local runner (internal test helper)."
    )
