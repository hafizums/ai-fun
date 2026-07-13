"""API schemas for base-image endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateBaseImageAcceptedResponse(BaseModel):
    id: str
    status: str
    current_stage: str | None = None
    progress_percent: int = Field(default=0)
    detail: str = "Base image generation accepted"


class BaseImageMetadataResponse(BaseModel):
    job_id: str
    status: str
    url: str
    width: int
    height: int
    format: str
    size_bytes: int
