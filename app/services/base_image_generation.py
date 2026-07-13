"""Background base-image generation service (Gate 3)."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.base import MediaProvider
from app.providers.media_exceptions import (
    MediaError,
    PromptPackageCorruptedError,
)
from app.services.image_download import ImageDownloader
from app.services.image_normalize import normalize_base_image
from app.services.prompt_generation import load_prompt_envelope
from app.services.status_transitions import assert_can_transition
from app.services.storage import StorageService
from app.services.task_runner import TaskRunner

logger = logging.getLogger(__name__)

BASE_IMAGE_STAGE = "base_image_generation"
BASE_IMAGE_READY_STAGE = "base_image_ready"
INITIAL_PROGRESS = 20
BASE_IMAGE_FILENAME = "base_image.png"
BASE_IMAGE_PARTIAL = "base_image.download"
BASE_IMAGE_SOURCE = "base_image.source"

TASK_SUBMISSION_FAILED = "TASK_SUBMISSION_FAILED"
TASK_SUBMISSION_MESSAGE = "Failed to submit the local background base-image task."

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "MEDIA_NOT_CONFIGURED": "The media provider is not configured.",
    "MEDIA_AUTHENTICATION_FAILED": "Media provider authentication failed.",
    "MEDIA_TIMEOUT": "The media provider request timed out.",
    "MEDIA_CONNECTION_FAILED": "Could not connect to the media provider.",
    "MEDIA_REQUEST_FAILED": "The media provider request failed.",
    "MEDIA_INVALID_RESULT": "The media provider returned an invalid result.",
    "BASE_IMAGE_DOWNLOAD_FAILED": "Failed to download the generated base image.",
    "BASE_IMAGE_TOO_LARGE": "The generated base image exceeded the download size limit.",
    "BASE_IMAGE_INVALID_FILE": "The generated base image file is invalid.",
    "BASE_IMAGE_INVALID_ASPECT_RATIO": (
        "The generated base image does not have a valid 9:16 portrait ratio."
    ),
    "PROMPT_PACKAGE_CORRUPTED": "The stored prompt package is corrupted or incomplete.",
    TASK_SUBMISSION_FAILED: TASK_SUBMISSION_MESSAGE,
}


def local_base_image_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/base-image/file"


def base_image_path(storage: StorageService, job_id: str) -> Path:
    return storage.job_directory(job_id, create=True) / BASE_IMAGE_FILENAME


class BaseImageGenerationService:
    """Accept and run asynchronous base-image generation for jobs."""

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
        """Atomically claim a job for base-image generation, then enqueue work."""
        claimed = self._atomic_claim(job_id)
        if not claimed:
            with self._session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise LookupError("Job not found")
                if job.status == JobStatus.BASE_IMAGE_GENERATING:
                    raise PermissionError("Base image generation is already in progress")
                raise PermissionError("Job is not eligible for base image generation")

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
                    GenerationJob.status == JobStatus.PROMPT_READY,
                    and_(
                        GenerationJob.status == JobStatus.FAILED,
                        GenerationJob.failed_stage == BASE_IMAGE_STAGE,
                    ),
                ),
            )
            .values(
                status=JobStatus.BASE_IMAGE_GENERATING,
                current_stage=BASE_IMAGE_STAGE,
                progress_percent=INITIAL_PROGRESS,
                base_image_url=None,
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
        """Worker entrypoint: own DB session; one media call; download; normalize."""
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                logger.error("Base-image worker: job_id=%s not found", job_id)
                return
            if job.status != JobStatus.BASE_IMAGE_GENERATING:
                logger.warning(
                    "Base-image worker: job_id=%s unexpected status=%s; skipping",
                    job_id,
                    job.status.value,
                )
                return
            prompt_json = job.prompt_json

        try:
            image_prompt = self._extract_image_prompt(prompt_json)
            if not self._media.is_configured():
                from app.providers.media_exceptions import MediaConfigurationError

                raise MediaConfigurationError()

            input_params = {
                "prompt": image_prompt,
                "aspect_ratio": self._settings.wavespeed_base_image_aspect_ratio,
                "resolution": self._settings.wavespeed_base_image_resolution,
                "quality": self._settings.wavespeed_base_image_quality,
                "output_format": self._settings.wavespeed_base_image_output_format,
                "enable_sync_mode": False,
                "enable_base64_output": False,
            }
            result = self._media.run_model(
                self._settings.wavespeed_base_image_model,
                input_params,
                timeout=self._settings.wavespeed_media_timeout_seconds,
                poll_interval=self._settings.wavespeed_media_poll_interval_seconds,
                enable_sync_mode=False,
                max_task_retries=0,
            )
            provider_url = result.output_urls[0]
            job_dir = self._storage.job_directory(job_id, create=True)
            download_path = job_dir / BASE_IMAGE_PARTIAL
            source_path = job_dir / BASE_IMAGE_SOURCE
            final_path = job_dir / BASE_IMAGE_FILENAME
            self._cleanup_paths(download_path, source_path, final_path)

            self._downloader.download(provider_url, download_path)
            # Keep source bytes separate from final published PNG during normalize.
            download_path.replace(source_path)
            normalize_base_image(
                source_path,
                final_path,
                max_pixels=self._settings.base_image_max_pixels,
            )
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
                "Base-image worker unexpected failure job_id=%s exception_class=Unexpected",
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
            if job.status != JobStatus.BASE_IMAGE_GENERATING:
                logger.warning(
                    "Base-image worker: job_id=%s left generating before write; skipping",
                    job_id,
                )
                return
            assert_can_transition(job.status, JobStatus.BASE_IMAGE_READY)
            job.status = JobStatus.BASE_IMAGE_READY
            job.current_stage = BASE_IMAGE_READY_STAGE
            job.progress_percent = 100
            job.base_image_url = local_base_image_url(job_id)
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
            job.updated_at = utc_now()
            session.commit()
            logger.info("Base image generation completed job_id=%s", job_id)

    def _extract_image_prompt(self, prompt_json: str | None) -> str:
        try:
            envelope = load_prompt_envelope(prompt_json)
            prompt = envelope.prompts.image_prompt
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
            if job.status != JobStatus.BASE_IMAGE_GENERATING:
                return
            try:
                assert_can_transition(job.status, JobStatus.FAILED)
            except Exception:
                pass
            job.status = JobStatus.FAILED
            job.failed_stage = BASE_IMAGE_STAGE
            job.error_code = error_code
            job.error_message = error_message
            job.current_stage = BASE_IMAGE_STAGE
            job.base_image_url = None
            job.updated_at = utc_now()
            session.commit()
            logger.error(
                "Base image generation failed job_id=%s error_code=%s",
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
        for name in (BASE_IMAGE_PARTIAL, BASE_IMAGE_SOURCE, f"{BASE_IMAGE_FILENAME}.partial"):
            path = job_dir / name
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.error("Failed cleaning partial base image for job_id=%s", job_id)

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
