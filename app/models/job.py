"""Generation job ORM model and status enum."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStatus(enum.StrEnum):
    """Lifecycle status for a generation job."""

    DRAFT = "DRAFT"
    PROMPT_GENERATING = "PROMPT_GENERATING"
    PROMPT_READY = "PROMPT_READY"
    BASE_IMAGE_GENERATING = "BASE_IMAGE_GENERATING"
    BASE_IMAGE_READY = "BASE_IMAGE_READY"
    WAITING_FOR_REFERENCE = "WAITING_FOR_REFERENCE"
    REFERENCE_READY = "REFERENCE_READY"
    CHARACTER_EDITING = "CHARACTER_EDITING"
    CHARACTER_EDIT_READY = "CHARACTER_EDIT_READY"
    SOURCE_VIDEO_GENERATING = "SOURCE_VIDEO_GENERATING"
    SOURCE_VIDEO_READY = "SOURCE_VIDEO_READY"
    CONTROL_VIDEO_GENERATING = "CONTROL_VIDEO_GENERATING"
    CONTROL_VIDEO_READY = "CONTROL_VIDEO_READY"
    ANALYZING_TRANSITION = "ANALYZING_TRANSITION"
    MERGING = "MERGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GenerationJob(Base):
    """Persisted generation job for the local application."""

    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False),
        nullable=False,
        default=JobStatus.DRAFT,
        index=True,
    )
    current_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    controlled_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    transition_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    transition_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_prediction_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "current_stage": self.current_stage,
            "progress_percent": self.progress_percent,
            "prompt_json": self.prompt_json,
            "base_image_url": self.base_image_url,
            "reference_image_path": self.reference_image_path,
            "edited_image_url": self.edited_image_url,
            "source_video_url": self.source_video_url,
            "controlled_video_url": self.controlled_video_url,
            "transition_time_seconds": self.transition_time_seconds,
            "transition_score": self.transition_score,
            "final_video_path": self.final_video_path,
            "provider_prediction_ids_json": self.provider_prediction_ids_json,
            "failed_stage": self.failed_stage,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
