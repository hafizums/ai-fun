"""Schemas for Gate 7 final-video assembly APIs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AssembleFinalVideoAcceptedResponse(BaseModel):
    id: str
    status: str
    current_stage: str | None = None
    progress_percent: int = Field(ge=0, le=100)


class TransitionMetadataResponse(BaseModel):
    job_id: str
    transition_seconds: float
    method: str
    confidence: float


class FinalVideoMetadataResponse(BaseModel):
    job_id: str
    status: str
    url: str
    transition_seconds: float
    width: int
    height: int
    duration_seconds: float
    fps: float
    codec: str
    container: str = "mp4"
    size_bytes: int
    has_audio: bool = False
