"""Schemas for Gate 4 reference upload and character-edit APIs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReferenceImageMetadataResponse(BaseModel):
    job_id: str
    status: str
    url: str
    width: int
    height: int
    format: str = "PNG"
    size_bytes: int


class GenerateCharacterEditAcceptedResponse(BaseModel):
    id: str
    status: str
    current_stage: str | None = None
    progress_percent: int = Field(ge=0, le=100)


class EditedImageMetadataResponse(BaseModel):
    job_id: str
    status: str
    url: str
    width: int
    height: int
    format: str = "PNG"
    size_bytes: int
