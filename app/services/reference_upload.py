"""Local reference-image upload, validation, and normalization (Gate 4)."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import update
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
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

REFERENCE_FILENAME = "reference_image.png"
REFERENCE_BACKUP_FILENAME = "reference_image.backup.png"
REFERENCE_UPLOAD_PARTIAL = "reference_image.upload"
REFERENCE_NORMALIZE_STAGING = "reference_image.staging.png"
REFERENCE_STAGE = "reference_upload"
REFERENCE_READY_STAGE = "reference_ready"

REFERENCE_RELATIVE_TEMPLATE = "uploads/{job_id}/reference_image.png"

REFERENCE_ARTIFACT_NAMES = (
    REFERENCE_UPLOAD_PARTIAL,
    REFERENCE_NORMALIZE_STAGING,
    REFERENCE_BACKUP_FILENAME,
)

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "REFERENCE_UPLOAD_TOO_LARGE": "The reference image upload exceeded the size limit.",
    "REFERENCE_IMAGE_EMPTY": "The reference image upload was empty.",
    "REFERENCE_IMAGE_INVALID_FILE": "The reference image file is invalid.",
    "REFERENCE_IMAGE_TOO_SMALL": "The reference image dimensions are below the minimum.",
    "REFERENCE_IMAGE_TOO_LARGE": "The reference image exceeds the maximum pixel limit.",
    "REFERENCE_IMAGE_STORAGE_FAILED": "Failed to store the reference image.",
}


@dataclass(frozen=True)
class UploadClaimResult:
    prior_status: JobStatus
    had_valid_reference: bool
    prior_reference_path: str | None


def local_reference_image_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/reference-image/file"


def reference_relative_path(job_id: str) -> str:
    return REFERENCE_RELATIVE_TEMPLATE.format(job_id=job_id)


def reference_absolute_path(storage: StorageService, job_id: str) -> Path:
    return storage.upload_job_directory(job_id, create=True) / REFERENCE_FILENAME


def reference_paths(upload_dir: Path) -> dict[str, Path]:
    return {
        "partial": upload_dir / REFERENCE_UPLOAD_PARTIAL,
        "staging": upload_dir / REFERENCE_NORMALIZE_STAGING,
        "backup": upload_dir / REFERENCE_BACKUP_FILENAME,
        "final": upload_dir / REFERENCE_FILENAME,
    }


def _reference_file_valid(
    storage: StorageService,
    job_id: str,
    relative: str | None,
    *,
    max_pixels: int,
) -> bool:
    if relative != reference_relative_path(job_id):
        return False
    try:
        path = storage.resolve_safe(relative)
        inspect_reference_png(path, max_pixels=max_pixels)
        return True
    except Exception:
        return False


def _cleanup_artifacts(*paths: Path) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.error(
                "Failed cleaning reference upload artifact "
                "error_code=REFERENCE_IMAGE_STORAGE_FAILED"
            )


def _restore_reference_files(
    *,
    final: Path,
    backup: Path,
    had_prior_valid: bool,
    published_final: bool,
) -> None:
    if had_prior_valid:
        if backup.is_file():
            if final.exists():
                final.unlink()
            os.replace(backup, final)
        elif published_final and final.is_file():
            final.unlink()
    elif published_final and final.is_file():
        final.unlink()


def _path_valid_png(path: Path, *, max_pixels: int) -> bool:
    try:
        inspect_reference_png(path, max_pixels=max_pixels)
        return True
    except Exception:
        return False


def reconcile_waiting_for_reference_jobs(
    session: Session,
    storage: StorageService,
    settings: Settings,
) -> int:
    """Restore idle status after a crash left jobs in WAITING_FOR_REFERENCE.

    A present ``reference_image.backup.png`` is authoritative evidence of an
    interrupted replacement. When that backup validates, it is restored even if
    the current final is also a valid PNG (an uncommitted candidate).
    """
    jobs = (
        session.query(GenerationJob)
        .filter(GenerationJob.status == JobStatus.WAITING_FOR_REFERENCE)
        .all()
    )
    if not jobs:
        return 0

    reconciled = 0
    max_pixels = settings.reference_image_max_pixels
    for job in jobs:
        upload_dir = storage.upload_job_directory(job.id, create=False)
        paths = reference_paths(upload_dir)
        final = paths["final"]
        backup = paths["backup"]
        relative = reference_relative_path(job.id)
        restore_failed = False

        if backup.is_file():
            if _path_valid_png(backup, max_pixels=max_pixels):
                try:
                    if final.exists():
                        final.unlink()
                    os.replace(backup, final)
                    if not _path_valid_png(final, max_pixels=max_pixels):
                        restore_failed = True
                        logger.error(
                            "Reference restart restored file invalid "
                            "job_id=%s error_code=REFERENCE_IMAGE_STORAGE_FAILED",
                            job.id,
                        )
                except OSError:
                    restore_failed = True
                    logger.error(
                        "Reference restart restore failed job_id=%s "
                        "error_code=REFERENCE_IMAGE_STORAGE_FAILED",
                        job.id,
                    )
            else:
                # Invalid backup: keep a valid final; drop only the bad backup.
                logger.error(
                    "Reference restart found invalid backup job_id=%s "
                    "error_code=REFERENCE_IMAGE_STORAGE_FAILED",
                    job.id,
                )
                if not restore_failed:
                    _cleanup_artifacts(backup)

        _cleanup_artifacts(paths["partial"], paths["staging"])
        # Successful os.replace moves backup away; remove any leftover only after
        # a completed restore path (not after restore_failed).
        if not restore_failed and backup.is_file():
            _cleanup_artifacts(backup)

        if restore_failed:
            # Keep backup if present; do not falsely mark REFERENCE_READY.
            job.status = JobStatus.WAITING_FOR_REFERENCE
            job.current_stage = REFERENCE_STAGE
            job.error_code = "REFERENCE_IMAGE_STORAGE_FAILED"
            job.error_message = SAFE_ERROR_MESSAGES["REFERENCE_IMAGE_STORAGE_FAILED"]
            job.updated_at = utc_now()
            reconciled += 1
            logger.warning(
                "Reconciled WAITING_FOR_REFERENCE job_id=%s status=%s "
                "error_code=REFERENCE_IMAGE_STORAGE_FAILED",
                job.id,
                job.status.value,
            )
            continue

        if _reference_file_valid(
            storage,
            job.id,
            relative,
            max_pixels=max_pixels,
        ):
            job.status = JobStatus.REFERENCE_READY
            job.current_stage = REFERENCE_READY_STAGE
            job.reference_image_path = relative
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
        else:
            job.status = JobStatus.BASE_IMAGE_READY
            job.current_stage = "base_image_ready"
            job.reference_image_path = None
            if final.is_file():
                try:
                    final.unlink()
                except OSError:
                    logger.error(
                        "Reference restart cleanup failed job_id=%s "
                        "error_code=REFERENCE_IMAGE_STORAGE_FAILED",
                        job.id,
                    )
        job.updated_at = utc_now()
        reconciled += 1
        logger.warning(
            "Reconciled WAITING_FOR_REFERENCE job_id=%s status=%s",
            job.id,
            job.status.value,
        )

    if reconciled:
        session.commit()
    return reconciled


class ReferenceUploadService:
    """Accept multipart reference uploads and publish a normalized local PNG."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: StorageService,
        settings: Settings,
        after_claim_hook: Callable[[], None] | None = None,
        before_db_commit_hook: Callable[[], None] | None = None,
        claim_barrier: threading.Barrier | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._settings = settings
        self._after_claim_hook = after_claim_hook
        self._before_db_commit_hook = before_db_commit_hook
        self._claim_barrier = claim_barrier

    def upload_reference(
        self, job_id: str, upload: UploadFile
    ) -> tuple[GenerationJob, NormalizedImageInfo]:
        claim = self._atomic_claim(job_id)
        if self._after_claim_hook is not None:
            self._after_claim_hook()

        upload_dir = self._storage.upload_job_directory(job_id, create=True)
        paths = reference_paths(upload_dir)
        partial = paths["partial"]
        staging = paths["staging"]
        backup = paths["backup"]
        final = paths["final"]
        published_final = False

        try:
            self._stream_upload_to_partial(upload, partial)
            info = normalize_reference_image(
                partial,
                staging,
                max_pixels=self._settings.reference_image_max_pixels,
                min_width=self._settings.reference_image_min_width,
                min_height=self._settings.reference_image_min_height,
            )
            self._assert_still_reserved(job_id)
            had_prior_final = claim.had_valid_reference and final.is_file()
            if had_prior_final:
                os.replace(final, backup)
            os.replace(staging, final)
            published_final = True
            if not self._storage.is_under_root(final):
                raise ReferenceImageStorageFailedError()

            self._commit_reference_ready(job_id)
            _cleanup_artifacts(partial, staging, backup)
            with self._session_factory() as session:
                refreshed = session.get(GenerationJob, job_id)
                assert refreshed is not None
                session.expunge(refreshed)
                return refreshed, info
        except MediaError:
            self._rollback_upload(
                job_id,
                claim=claim,
                paths=paths,
                published_final=published_final,
            )
            raise
        except PermissionError:
            self._rollback_upload(
                job_id,
                claim=claim,
                paths=paths,
                published_final=published_final,
            )
            raise
        except Exception:
            logger.error(
                "Reference upload failed job_id=%s exception_class=Unexpected",
                job_id,
            )
            self._rollback_upload(
                job_id,
                claim=claim,
                paths=paths,
                published_final=published_final,
            )
            raise ReferenceImageStorageFailedError() from None

    def _atomic_claim(self, job_id: str) -> UploadClaimResult:
        if self._claim_barrier is not None:
            self._claim_barrier.wait(timeout=5)
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise LookupError("Job not found")
            prior_status = job.status
            prior_path = job.reference_image_path
            had_valid = _reference_file_valid(
                self._storage,
                job_id,
                prior_path,
                max_pixels=self._settings.reference_image_max_pixels,
            )
            if prior_status not in {
                JobStatus.BASE_IMAGE_READY,
                JobStatus.REFERENCE_READY,
            }:
                if prior_status == JobStatus.WAITING_FOR_REFERENCE:
                    raise PermissionError("Reference upload is already in progress")
                if prior_status == JobStatus.CHARACTER_EDITING:
                    raise PermissionError("Cannot replace reference while character editing")
                if prior_status == JobStatus.CHARACTER_EDIT_READY:
                    raise PermissionError(
                        "Cannot replace reference after character edit is ready"
                    )
                raise PermissionError("Job is not eligible for reference upload")

            now = utc_now()
            stmt = (
                update(GenerationJob)
                .where(
                    GenerationJob.id == job_id,
                    GenerationJob.status.in_(
                        (JobStatus.BASE_IMAGE_READY, JobStatus.REFERENCE_READY)
                    ),
                )
                .values(
                    status=JobStatus.WAITING_FOR_REFERENCE,
                    current_stage=REFERENCE_STAGE,
                    updated_at=now,
                )
            )
            result = session.execute(stmt)
            session.commit()
            if int(result.rowcount or 0) != 1:
                raise PermissionError("Job is not eligible for reference upload")
            return UploadClaimResult(
                prior_status=prior_status,
                had_valid_reference=had_valid,
                prior_reference_path=prior_path,
            )

    def _assert_still_reserved(self, job_id: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise LookupError("Job not found")
            if job.status != JobStatus.WAITING_FOR_REFERENCE:
                raise PermissionError("Reference upload reservation was lost")

    def _commit_reference_ready(self, job_id: str) -> None:
        relative = reference_relative_path(job_id)
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise LookupError("Job not found")
            if job.status != JobStatus.WAITING_FOR_REFERENCE:
                raise PermissionError("Reference upload reservation was lost")
            job.status = JobStatus.REFERENCE_READY
            job.current_stage = REFERENCE_READY_STAGE
            job.progress_percent = max(job.progress_percent, 40)
            job.reference_image_path = relative
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
            job.updated_at = utc_now()
            if self._before_db_commit_hook is not None:
                self._before_db_commit_hook()
            session.commit()

    def _rollback_upload(
        self,
        job_id: str,
        *,
        claim: UploadClaimResult,
        paths: dict[str, Path],
        published_final: bool,
    ) -> None:
        try:
            _restore_reference_files(
                final=paths["final"],
                backup=paths["backup"],
                had_prior_valid=claim.had_valid_reference,
                published_final=published_final,
            )
        except OSError:
            logger.error(
                "Reference upload file rollback failed job_id=%s "
                "error_code=REFERENCE_IMAGE_STORAGE_FAILED",
                job_id,
            )
        _cleanup_artifacts(paths["partial"], paths["staging"], paths["backup"])
        self._rollback_job_state(job_id, claim=claim)

    def _rollback_job_state(self, job_id: str, *, claim: UploadClaimResult) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if claim.had_valid_reference:
                job.status = JobStatus.REFERENCE_READY
                job.current_stage = REFERENCE_READY_STAGE
                job.reference_image_path = claim.prior_reference_path
            else:
                job.status = JobStatus.BASE_IMAGE_READY
                job.current_stage = "base_image_ready"
                job.reference_image_path = None
            job.updated_at = utc_now()
            session.commit()

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
            _cleanup_artifacts(partial)
            raise
        except Exception as exc:
            _cleanup_artifacts(partial)
            raise ReferenceImageStorageFailedError() from exc
