"""Background character-edit generation service (Gate 4)."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.base import MediaProvider
from app.providers.media_exceptions import (
    BaseImageDownloadError,
    BaseImageInvalidAspectRatioError,
    BaseImageInvalidFileError,
    BaseImageMissingOrInvalidError,
    BaseImageTooLargeError,
    EditImageDownloadError,
    EditImageInvalidAspectRatioError,
    EditImageInvalidFileError,
    EditImageTooLargeError,
    MediaError,
    PromptPackageCorruptedError,
    ReferenceImageInvalidFileError,
    ReferenceImageMissingOrInvalidError,
)
from app.services.base_image_generation import BASE_IMAGE_FILENAME
from app.services.image_download import ImageDownloader
from app.services.image_normalize import (
    inspect_local_png,
    inspect_reference_png,
    normalize_edited_image,
)
from app.services.prompt_generation import load_prompt_envelope
from app.services.reference_upload import reference_relative_path
from app.services.status_transitions import assert_can_transition
from app.services.storage import StoragePathError, StorageService
from app.services.task_runner import TaskRunner

logger = logging.getLogger(__name__)

CHARACTER_EDIT_STAGE = "character_editing"
CHARACTER_EDIT_READY_STAGE = "character_edit_ready"
INITIAL_PROGRESS = 20
EDITED_IMAGE_FILENAME = "edited_image.png"
EDITED_IMAGE_PARTIAL = "edited_image.download"
EDITED_IMAGE_SOURCE = "edited_image.source"

TASK_SUBMISSION_FAILED = "TASK_SUBMISSION_FAILED"
TASK_SUBMISSION_MESSAGE = "Failed to submit the local background character-edit task."

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "MEDIA_NOT_CONFIGURED": "The media provider is not configured.",
    "MEDIA_AUTHENTICATION_FAILED": "Media provider authentication failed.",
    "MEDIA_TIMEOUT": "The media provider request timed out.",
    "MEDIA_CONNECTION_FAILED": "Could not connect to the media provider.",
    "MEDIA_REQUEST_FAILED": "The media provider request failed.",
    "MEDIA_INVALID_RESULT": "The media provider returned an invalid result.",
    "BASE_IMAGE_MISSING_OR_INVALID": "The base image is missing or invalid.",
    "REFERENCE_IMAGE_MISSING_OR_INVALID": "The reference image is missing or invalid.",
    "PROMPT_PACKAGE_CORRUPTED": "The stored prompt package is corrupted or incomplete.",
    "EDIT_IMAGE_DOWNLOAD_FAILED": "Failed to download the edited image.",
    "EDIT_IMAGE_TOO_LARGE": "The edited image exceeded the download size limit.",
    "EDIT_IMAGE_INVALID_FILE": "The edited image file is invalid.",
    "EDIT_IMAGE_INVALID_ASPECT_RATIO": (
        "The edited image does not have a valid 9:16 portrait ratio."
    ),
    TASK_SUBMISSION_FAILED: TASK_SUBMISSION_MESSAGE,
}


def local_edited_image_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/edited-image/file"


def edited_image_path(storage: StorageService, job_id: str) -> Path:
    return storage.job_directory(job_id, create=True) / EDITED_IMAGE_FILENAME


class CharacterEditGenerationService:
    """Accept and run asynchronous character-edit generation for jobs."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        task_runner: TaskRunner,
        media_provider: MediaProvider,
        storage: StorageService,
        settings: Settings,
        downloader: ImageDownloader | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._task_runner = task_runner
        self._media = media_provider
        self._storage = storage
        self._settings = settings
        self._downloader = downloader or ImageDownloader(
            timeout_seconds=settings.base_image_download_timeout_seconds,
            max_bytes=settings.base_image_max_download_bytes,
        )

    def accept_generation(self, job_id: str) -> GenerationJob:
        claimed = self._atomic_claim(job_id)
        if not claimed:
            with self._session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise LookupError("Job not found")
                if job.status == JobStatus.CHARACTER_EDITING:
                    raise PermissionError("Character editing is already in progress")
                raise PermissionError("Job is not eligible for character editing")

        self._cleanup_partials(job_id)

        try:
            self._task_runner.submit(self.run_generation_task, job_id)
        except Exception:
            logger.error(
                "TaskRunner.submit failed for job_id=%s exception_class=submit_failure",
                job_id,
            )
            self._mark_failed(
                job_id,
                error_code=TASK_SUBMISSION_FAILED,
                error_message=TASK_SUBMISSION_MESSAGE,
            )
            raise RuntimeError(TASK_SUBMISSION_MESSAGE) from None

        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            assert job is not None
            session.expunge(job)
            return job

    def _atomic_claim(self, job_id: str) -> bool:
        now = utc_now()
        stmt = (
            update(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                or_(
                    GenerationJob.status == JobStatus.REFERENCE_READY,
                    and_(
                        GenerationJob.status == JobStatus.FAILED,
                        GenerationJob.failed_stage == CHARACTER_EDIT_STAGE,
                    ),
                ),
            )
            .values(
                status=JobStatus.CHARACTER_EDITING,
                current_stage=CHARACTER_EDIT_STAGE,
                progress_percent=INITIAL_PROGRESS,
                edited_image_url=None,
                error_code=None,
                error_message=None,
                failed_stage=None,
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0) == 1

    def run_generation_task(self, job_id: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                logger.error("Character-edit worker: job_id=%s not found", job_id)
                return
            if job.status != JobStatus.CHARACTER_EDITING:
                logger.warning(
                    "Character-edit worker: job_id=%s unexpected status=%s; skipping",
                    job_id,
                    job.status.value,
                )
                return
            prompt_json = job.prompt_json
            reference_rel = job.reference_image_path

        try:
            edit_prompt = self._extract_edit_prompt(prompt_json)
            base_path = self._storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
            try:
                inspect_local_png(
                    base_path, max_pixels=self._settings.base_image_max_pixels
                )
            except (BaseImageInvalidFileError, BaseImageInvalidAspectRatioError) as exc:
                raise BaseImageMissingOrInvalidError() from exc

            if not reference_rel:
                raise ReferenceImageMissingOrInvalidError()
            expected = reference_relative_path(job_id)
            if reference_rel != expected:
                raise ReferenceImageMissingOrInvalidError()
            try:
                reference_path = self._storage.resolve_safe(reference_rel)
                inspect_reference_png(
                    reference_path,
                    max_pixels=self._settings.reference_image_max_pixels,
                )
            except (ReferenceImageInvalidFileError, StoragePathError) as exc:
                raise ReferenceImageMissingOrInvalidError() from exc
            except Exception as exc:
                raise ReferenceImageMissingOrInvalidError() from exc
            if not self._media.is_configured():
                from app.providers.media_exceptions import MediaConfigurationError

                raise MediaConfigurationError()

            base_url = self._media.upload_file(base_path)
            reference_url = self._media.upload_file(reference_path)

            input_params = {
                "prompt": edit_prompt,
                "images": [base_url, reference_url],
                "aspect_ratio": self._settings.wavespeed_character_edit_aspect_ratio,
                "resolution": self._settings.wavespeed_character_edit_resolution,
                "quality": self._settings.wavespeed_character_edit_quality,
                "output_format": self._settings.wavespeed_character_edit_output_format,
                "enable_sync_mode": False,
                "enable_base64_output": False,
            }
            result = self._media.run_model(
                self._settings.wavespeed_character_edit_model,
                input_params,
                timeout=self._settings.wavespeed_media_timeout_seconds,
                poll_interval=self._settings.wavespeed_media_poll_interval_seconds,
                enable_sync_mode=False,
            )
            provider_url = result.output_urls[0]
            job_dir = self._storage.job_directory(job_id, create=True)
            download_path = job_dir / EDITED_IMAGE_PARTIAL
            source_path = job_dir / EDITED_IMAGE_SOURCE
            final_path = job_dir / EDITED_IMAGE_FILENAME
            self._cleanup_paths(download_path, source_path, final_path)

            try:
                self._downloader.download(provider_url, download_path)
            except BaseImageTooLargeError as exc:
                raise EditImageTooLargeError() from exc
            except BaseImageDownloadError as exc:
                raise EditImageDownloadError() from exc
            except MediaError:
                raise
            except Exception as exc:
                raise EditImageDownloadError() from exc

            download_path.replace(source_path)
            try:
                normalize_edited_image(
                    source_path,
                    final_path,
                    max_pixels=self._settings.base_image_max_pixels,
                )
            except EditImageInvalidFileError:
                raise
            except EditImageInvalidAspectRatioError:
                raise
            self._cleanup_paths(source_path, download_path)
        except MediaError as exc:
            self._cleanup_partials(job_id)
            self._mark_failed(
                job_id,
                error_code=exc.code,
                error_message=SAFE_ERROR_MESSAGES.get(exc.code, exc.public_message),
            )
            return
        except Exception:
            logger.error(
                "Character-edit worker unexpected failure job_id=%s exception_class=Unexpected",
                job_id,
            )
            self._cleanup_partials(job_id)
            self._mark_failed(
                job_id,
                error_code="MEDIA_REQUEST_FAILED",
                error_message=SAFE_ERROR_MESSAGES["MEDIA_REQUEST_FAILED"],
            )
            return

        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if job.status != JobStatus.CHARACTER_EDITING:
                logger.warning(
                    "Character-edit worker: job_id=%s left editing before write; skipping",
                    job_id,
                )
                return
            assert_can_transition(job.status, JobStatus.CHARACTER_EDIT_READY)
            job.status = JobStatus.CHARACTER_EDIT_READY
            job.current_stage = CHARACTER_EDIT_READY_STAGE
            job.progress_percent = 100
            job.edited_image_url = local_edited_image_url(job_id)
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
            job.updated_at = utc_now()
            session.commit()
            logger.info("Character edit completed job_id=%s", job_id)

    def _extract_edit_prompt(self, prompt_json: str | None) -> str:
        try:
            envelope = load_prompt_envelope(prompt_json)
            prompt = envelope.prompts.edit_prompt
            if not isinstance(prompt, str) or not prompt.strip():
                raise PromptPackageCorruptedError()
            return prompt
        except PromptPackageCorruptedError:
            raise
        except Exception as exc:
            raise PromptPackageCorruptedError() from exc

    def _mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if job.status != JobStatus.CHARACTER_EDITING:
                return
            try:
                assert_can_transition(job.status, JobStatus.FAILED)
            except Exception:
                pass
            job.status = JobStatus.FAILED
            job.failed_stage = CHARACTER_EDIT_STAGE
            job.error_code = error_code
            job.error_message = error_message
            job.current_stage = CHARACTER_EDIT_STAGE
            job.edited_image_url = None
            job.updated_at = utc_now()
            session.commit()
            logger.error(
                "Character edit failed job_id=%s error_code=%s",
                job_id,
                error_code,
            )

    def _cleanup_partials(self, job_id: str) -> None:
        try:
            job_dir = self._storage.job_directory(job_id, create=False)
        except Exception:
            return
        if not job_dir.exists():
            return
        for name in (
            EDITED_IMAGE_PARTIAL,
            EDITED_IMAGE_SOURCE,
            f"{EDITED_IMAGE_FILENAME}.partial",
        ):
            path = job_dir / name
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.error("Failed cleaning partial edited image for job_id=%s", job_id)

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
