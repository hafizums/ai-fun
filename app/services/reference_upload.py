"""Local reference-image upload, validation, and normalization (Gate 4)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.media_exceptions import (
    MediaError,
    ReferenceImageEmptyError,
    ReferenceImageStorageFailedError,
    ReferenceUploadTooLargeError,
)
from app.services.image_normalize import (
    NormalizedImageInfo,
    inspect_reference_png,
    normalize_reference_image,
)
from app.services.status_transitions import assert_can_transition
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

REFERENCE_FILENAME = "reference_image.png"
REFERENCE_UPLOAD_PARTIAL = "reference_image.upload"
REFERENCE_NORMALIZE_STAGING = "reference_image.staging.png"
REFERENCE_STAGE = "reference_upload"
REFERENCE_READY_STAGE = "reference_ready"

REFERENCE_RELATIVE_TEMPLATE = "uploads/{job_id}/reference_image.png"

UPLOAD_ELIGIBLE = frozenset(
    {
        JobStatus.BASE_IMAGE_READY,
        JobStatus.WAITING_FOR_REFERENCE,
        JobStatus.REFERENCE_READY,
    }
)

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "REFERENCE_UPLOAD_TOO_LARGE": "The reference image upload exceeded the size limit.",
    "REFERENCE_IMAGE_EMPTY": "The reference image upload was empty.",
    "REFERENCE_IMAGE_INVALID_FILE": "The reference image file is invalid.",
    "REFERENCE_IMAGE_TOO_SMALL": "The reference image dimensions are below the minimum.",
    "REFERENCE_IMAGE_TOO_LARGE": "The reference image exceeds the maximum pixel limit.",
    "REFERENCE_IMAGE_STORAGE_FAILED": "Failed to store the reference image.",
}


def local_reference_image_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/reference-image/file"


def reference_relative_path(job_id: str) -> str:
    return REFERENCE_RELATIVE_TEMPLATE.format(job_id=job_id)


def reference_absolute_path(storage: StorageService, job_id: str) -> Path:
    return storage.upload_job_directory(job_id, create=True) / REFERENCE_FILENAME


class ReferenceUploadService:
    """Accept multipart reference uploads and publish a normalized local PNG."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: StorageService,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._settings = settings

    def upload_reference(
        self, job_id: str, upload: UploadFile
    ) -> tuple[GenerationJob, NormalizedImageInfo]:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise LookupError("Job not found")
            if job.status == JobStatus.CHARACTER_EDITING:
                raise PermissionError("Cannot replace reference while character editing")
            if job.status == JobStatus.CHARACTER_EDIT_READY:
                raise PermissionError("Cannot replace reference after character edit is ready")
            if job.status not in UPLOAD_ELIGIBLE:
                raise PermissionError("Job is not eligible for reference upload")
            prior_status = job.status
            had_valid_reference = self._has_valid_reference(job_id, job.reference_image_path)

            if prior_status == JobStatus.BASE_IMAGE_READY:
                assert_can_transition(job.status, JobStatus.WAITING_FOR_REFERENCE)
                job.status = JobStatus.WAITING_FOR_REFERENCE
                job.current_stage = REFERENCE_STAGE
                job.updated_at = utc_now()
                session.commit()

        upload_dir = self._storage.upload_job_directory(job_id, create=True)
        partial = upload_dir / REFERENCE_UPLOAD_PARTIAL
        staging = upload_dir / REFERENCE_NORMALIZE_STAGING
        final = upload_dir / REFERENCE_FILENAME
        info: NormalizedImageInfo | None = None

        try:
            self._stream_upload_to_partial(upload, partial)
            info = normalize_reference_image(
                partial,
                staging,
                max_pixels=self._settings.reference_image_max_pixels,
                min_width=self._settings.reference_image_min_width,
                min_height=self._settings.reference_image_min_height,
            )
            # Preserve prior valid reference until replace succeeds.
            os.replace(staging, final)
            if not self._storage.is_under_root(final):
                raise ReferenceImageStorageFailedError()
            relative = reference_relative_path(job_id)
            with self._session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise LookupError("Job not found")
                if job.status not in {
                    JobStatus.WAITING_FOR_REFERENCE,
                    JobStatus.REFERENCE_READY,
                }:
                    raise PermissionError("Job is not eligible for reference upload")
                if job.status == JobStatus.WAITING_FOR_REFERENCE:
                    assert_can_transition(job.status, JobStatus.REFERENCE_READY)
                job.status = JobStatus.REFERENCE_READY
                job.current_stage = REFERENCE_READY_STAGE
                job.progress_percent = max(job.progress_percent, 40)
                job.reference_image_path = relative
                job.error_code = None
                job.error_message = None
                job.failed_stage = None
                job.updated_at = utc_now()
                session.commit()
                session.refresh(job)
                session.expunge(job)
                return job, info
        except MediaError:
            self._cleanup_paths(partial, staging)
            self._revert_after_failure(
                job_id,
                prior_status=prior_status,
                had_valid_reference=had_valid_reference,
            )
            raise
        except Exception:
            logger.error(
                "Reference upload failed job_id=%s exception_class=Unexpected",
                job_id,
            )
            self._cleanup_paths(partial, staging)
            self._revert_after_failure(
                job_id,
                prior_status=prior_status,
                had_valid_reference=had_valid_reference,
            )
            raise ReferenceImageStorageFailedError() from None
        finally:
            self._cleanup_paths(partial, staging)

    def _stream_upload_to_partial(self, upload: UploadFile, partial: Path) -> None:
        max_bytes = self._settings.reference_image_max_upload_bytes
        total = 0
        try:
            if partial.exists():
                partial.unlink()
            with partial.open("wb") as out:
                while True:
                    chunk = upload.file.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ReferenceUploadTooLargeError()
                    out.write(chunk)
            if total == 0:
                raise ReferenceImageEmptyError()
        except MediaError:
            self._cleanup_paths(partial)
            raise
        except Exception as exc:
            self._cleanup_paths(partial)
            raise ReferenceImageStorageFailedError() from exc

    def _has_valid_reference(self, job_id: str, relative: str | None) -> bool:
        if not relative:
            return False
        try:
            path = self._storage.resolve_safe(relative)
            inspect_reference_png(
                path, max_pixels=self._settings.reference_image_max_pixels
            )
            return True
        except Exception:
            return False

    def _revert_after_failure(
        self,
        job_id: str,
        *,
        prior_status: JobStatus,
        had_valid_reference: bool,
    ) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if had_valid_reference and prior_status == JobStatus.REFERENCE_READY:
                job.status = JobStatus.REFERENCE_READY
                job.current_stage = REFERENCE_READY_STAGE
            elif prior_status == JobStatus.BASE_IMAGE_READY or (
                job.status == JobStatus.WAITING_FOR_REFERENCE and not had_valid_reference
            ):
                if job.status == JobStatus.WAITING_FOR_REFERENCE:
                    try:
                        assert_can_transition(job.status, JobStatus.BASE_IMAGE_READY)
                    except Exception:
                        pass
                job.status = JobStatus.BASE_IMAGE_READY
                job.current_stage = "base_image_ready"
                if not had_valid_reference:
                    job.reference_image_path = None
            job.updated_at = utc_now()
            session.commit()

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.error("Failed cleaning reference upload partial")
