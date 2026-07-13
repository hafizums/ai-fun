"""Schemas for Gate 5 source-video APIs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateSourceVideoAcceptedResponse(BaseModel):
    id: str
    status: str
    current_stage: str | None = None
    progress_percent: int = Field(ge=0, le=100)


class SourceVideoMetadataResponse(BaseModel):
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
