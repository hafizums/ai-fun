"""Schemas for Gate 6 controlled-video APIs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateControlledVideoAcceptedResponse(BaseModel):
    id: str
    status: str
    current_stage: str | None = None
    progress_percent: int = Field(ge=0, le=100)


class ControlledVideoMetadataResponse(BaseModel):
    job_id: str
    status: str
    url: str
    width: int
    height: int
    duration_seconds: float
    fps: float
    codec: str
    container: str = "mp4"
    size_bytes: int
    has_audio: bool = False
