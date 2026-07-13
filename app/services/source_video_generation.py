"""Background source-video generation service (Gate 5)."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.base import MediaProvider
from app.providers.media_exceptions import (
    BaseImageInvalidAspectRatioError,
    BaseImageInvalidFileError,
    BaseImageMissingOrInvalidError,
    EditedImageMissingOrInvalidError,
    EditImageInvalidAspectRatioError,
    EditImageInvalidFileError,
    MediaError,
    PromptPackageCorruptedError,
    SourceVideoDownloadError,
    SourceVideoTooLargeError,
)
from app.services.base_image_generation import BASE_IMAGE_FILENAME
from app.services.character_edit_generation import EDITED_IMAGE_FILENAME
from app.services.image_download import SecureArtifactDownloader
from app.services.image_normalize import inspect_edited_png, inspect_local_png
from app.services.prompt_generation import load_prompt_envelope
from app.services.status_transitions import assert_can_transition
from app.services.storage import StorageService
from app.services.task_runner import TaskRunner
from app.services.video_normalize import normalize_source_video
from app.services.video_probe import VideoMetadata, validate_source_video_probe

logger = logging.getLogger(__name__)

SOURCE_VIDEO_STAGE = "source_video_generation"
SOURCE_VIDEO_READY_STAGE = "source_video_ready"
INITIAL_PROGRESS = 20
SOURCE_VIDEO_FILENAME = "source_video.mp4"
SOURCE_VIDEO_PARTIAL = "source_video.download"
SOURCE_VIDEO_SOURCE = "source_video.source"

TASK_SUBMISSION_FAILED = "TASK_SUBMISSION_FAILED"
TASK_SUBMISSION_MESSAGE = "Failed to submit the local background source-video task."

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "MEDIA_NOT_CONFIGURED": "The media provider is not configured.",
    "MEDIA_AUTHENTICATION_FAILED": "Media provider authentication failed.",
    "MEDIA_TIMEOUT": "The media provider request timed out.",
    "MEDIA_CONNECTION_FAILED": "Could not connect to the media provider.",
    "MEDIA_REQUEST_FAILED": "The media provider request failed.",
    "MEDIA_INVALID_RESULT": "The media provider returned an invalid result.",
    "PROMPT_PACKAGE_CORRUPTED": "The stored prompt package is corrupted or incomplete.",
    "BASE_IMAGE_MISSING_OR_INVALID": "The base image is missing or invalid.",
    "EDITED_IMAGE_MISSING_OR_INVALID": "The edited image is missing or invalid.",
    "SOURCE_VIDEO_DOWNLOAD_FAILED": "Failed to download the source video.",
    "SOURCE_VIDEO_TOO_LARGE": "The source video exceeded the download size limit.",
    "SOURCE_VIDEO_INVALID_FILE": "The source video file is invalid.",
    "SOURCE_VIDEO_INVALID_DURATION": "The source video duration is invalid.",
    "SOURCE_VIDEO_INVALID_DIMENSIONS": "The source video dimensions are invalid.",
    "SOURCE_VIDEO_INVALID_FRAME_RATE": "The source video frame rate is invalid.",
    "FFPROBE_NOT_AVAILABLE": "ffprobe is not available.",
    "FFMPEG_NOT_AVAILABLE": "ffmpeg is not available.",
    "VIDEO_NORMALIZATION_FAILED": "Failed to normalize the source video.",
    TASK_SUBMISSION_FAILED: TASK_SUBMISSION_MESSAGE,
}


def local_source_video_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/source-video/file"


def source_video_path(storage: StorageService, job_id: str) -> Path:
    return storage.job_directory(job_id, create=True) / SOURCE_VIDEO_FILENAME


class SourceVideoGenerationService:
    """Accept and run asynchronous source-video generation for jobs."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        task_runner: TaskRunner,
        media_provider: MediaProvider,
        storage: StorageService,
        settings: Settings,
        downloader: SecureArtifactDownloader | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._task_runner = task_runner
        self._media = media_provider
        self._storage = storage
        self._settings = settings
        self._downloader = downloader or SecureArtifactDownloader(
            timeout_seconds=settings.source_video_download_timeout_seconds,
            max_bytes=settings.source_video_max_download_bytes,
            download_error_cls=SourceVideoDownloadError,
            too_large_error_cls=SourceVideoTooLargeError,
        )

    def accept_generation(self, job_id: str) -> GenerationJob:
        claimed = self._atomic_claim(job_id)
        if not claimed:
            with self._session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise LookupError("Job not found")
                if job.status == JobStatus.SOURCE_VIDEO_GENERATING:
                    raise PermissionError("Source video generation is already in progress")
                raise PermissionError("Job is not eligible for source video generation")

        self._cleanup_partials(job_id, remove_final=True)

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
                    GenerationJob.status == JobStatus.CHARACTER_EDIT_READY,
                    and_(
                        GenerationJob.status == JobStatus.FAILED,
                        GenerationJob.failed_stage == SOURCE_VIDEO_STAGE,
                    ),
                ),
            )
            .values(
                status=JobStatus.SOURCE_VIDEO_GENERATING,
                current_stage=SOURCE_VIDEO_STAGE,
                progress_percent=INITIAL_PROGRESS,
                source_video_url=None,
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
                logger.error("Source-video worker: job_id=%s not found", job_id)
                return
            if job.status != JobStatus.SOURCE_VIDEO_GENERATING:
                logger.warning(
                    "Source-video worker: job_id=%s unexpected status=%s; skipping",
                    job_id,
                    job.status.value,
                )
                return
            prompt_json = job.prompt_json

        try:
            motion_prompt, negative_prompt = self._extract_motion_prompts(prompt_json)
            base_path = (
                self._storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
            )
            edited_path = (
                self._storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
            )
            try:
                inspect_local_png(
                    base_path, max_pixels=self._settings.base_image_max_pixels
                )
            except (BaseImageInvalidFileError, BaseImageInvalidAspectRatioError) as exc:
                raise BaseImageMissingOrInvalidError() from exc
            try:
                inspect_edited_png(
                    edited_path, max_pixels=self._settings.base_image_max_pixels
                )
            except (EditImageInvalidFileError, EditImageInvalidAspectRatioError) as exc:
                raise EditedImageMissingOrInvalidError() from exc

            if not self._media.is_configured():
                from app.providers.media_exceptions import MediaConfigurationError

                raise MediaConfigurationError()

            uploaded_base_url = self._media.upload_file(base_path)
            input_params: dict = {
                "image": uploaded_base_url,
                "prompt": motion_prompt,
                "negative_prompt": negative_prompt,
                "duration": self._settings.wavespeed_source_video_duration_seconds,
                "seed": self._settings.wavespeed_source_video_seed,
            }
            result = self._media.run_model(
                self._settings.wavespeed_source_video_model,
                input_params,
                timeout=self._settings.wavespeed_media_timeout_seconds,
                poll_interval=self._settings.wavespeed_media_poll_interval_seconds,
                enable_sync_mode=False,
            )
            provider_url = result.output_urls[0]
            job_dir = self._storage.job_directory(job_id, create=True)
            download_path = job_dir / SOURCE_VIDEO_PARTIAL
            source_path = job_dir / SOURCE_VIDEO_SOURCE
            final_path = job_dir / SOURCE_VIDEO_FILENAME
            self._cleanup_paths(download_path, source_path, final_path)

            self._downloader.download(provider_url, download_path)
            download_path.replace(source_path)
            normalize_source_video(
                source_path,
                final_path,
                ffmpeg_binary=self._settings.ffmpeg_binary,
                ffprobe_binary=self._settings.ffprobe_binary,
                target_duration=float(
                    self._settings.wavespeed_source_video_duration_seconds
                ),
                min_duration=self._settings.source_video_min_duration_seconds,
                max_duration=self._settings.source_video_max_duration_seconds,
                duration_tolerance=self._settings.source_video_duration_tolerance_seconds,
                min_width=self._settings.source_video_min_width,
                min_height=self._settings.source_video_min_height,
                max_pixels=self._settings.source_video_max_pixels,
                max_fps=self._settings.source_video_max_fps,
            )
            self._cleanup_paths(source_path, download_path)
        except MediaError as exc:
            self._cleanup_partials(job_id, remove_final=True)
            self._mark_failed(
                job_id,
                error_code=exc.code,
                error_message=SAFE_ERROR_MESSAGES.get(exc.code, exc.public_message),
            )
            return
        except Exception:
            logger.error(
                "Source-video worker unexpected failure job_id=%s exception_class=Unexpected",
                job_id,
            )
            self._cleanup_partials(job_id, remove_final=True)
            self._mark_failed(
                job_id,
                error_code="MEDIA_REQUEST_FAILED",
                error_message=SAFE_ERROR_MESSAGES["MEDIA_REQUEST_FAILED"],
            )
            return

        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                self._cleanup_partials(job_id, remove_final=True)
                return
            if job.status != JobStatus.SOURCE_VIDEO_GENERATING:
                logger.warning(
                    "Source-video worker: job_id=%s left generating before write; skipping",
                    job_id,
                )
                self._cleanup_partials(job_id, remove_final=True)
                return
            assert_can_transition(job.status, JobStatus.SOURCE_VIDEO_READY)
            job.status = JobStatus.SOURCE_VIDEO_READY
            job.current_stage = SOURCE_VIDEO_READY_STAGE
            job.progress_percent = 100
            job.source_video_url = local_source_video_url(job_id)
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
            job.updated_at = utc_now()
            try:
                session.commit()
            except Exception:
                logger.error(
                    "Source-video DB commit failed after publish job_id=%s "
                    "exception_class=CommitFailure",
                    job_id,
                )
                session.rollback()
                # Remove uncommitted final so GENERATING/FAILED never looks ready.
                self._cleanup_partials(job_id, remove_final=True)
                self._mark_failed(
                    job_id,
                    error_code="MEDIA_REQUEST_FAILED",
                    error_message=SAFE_ERROR_MESSAGES["MEDIA_REQUEST_FAILED"],
                )
                return
            logger.info("Source video generation completed job_id=%s", job_id)

    def inspect_ready_video(self, job_id: str) -> VideoMetadata:
        path = self._storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
        return validate_source_video_probe(
            path,
            ffprobe_binary=self._settings.ffprobe_binary,
            target_duration=float(self._settings.wavespeed_source_video_duration_seconds),
            min_duration=self._settings.source_video_min_duration_seconds,
            max_duration=self._settings.source_video_max_duration_seconds,
            duration_tolerance=self._settings.source_video_duration_tolerance_seconds,
            min_width=self._settings.source_video_min_width,
            min_height=self._settings.source_video_min_height,
            max_pixels=self._settings.source_video_max_pixels,
            max_fps=self._settings.source_video_max_fps,
        )

    def _extract_motion_prompts(self, prompt_json: str | None) -> tuple[str, str]:
        try:
            envelope = load_prompt_envelope(prompt_json)
            motion = envelope.prompts.motion_prompt
            negative = envelope.prompts.motion_negative_prompt
            if not isinstance(motion, str) or not motion.strip():
                raise PromptPackageCorruptedError()
            if not isinstance(negative, str) or not negative.strip():
                raise PromptPackageCorruptedError()
            return motion, negative
        except PromptPackageCorruptedError:
            raise
        except Exception as exc:
            raise PromptPackageCorruptedError() from exc

    def _mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if job.status != JobStatus.SOURCE_VIDEO_GENERATING:
                return
            try:
                assert_can_transition(job.status, JobStatus.FAILED)
            except Exception:
                pass
            job.status = JobStatus.FAILED
            job.failed_stage = SOURCE_VIDEO_STAGE
            job.error_code = error_code
            job.error_message = error_message
            job.current_stage = SOURCE_VIDEO_STAGE
            job.source_video_url = None
            job.updated_at = utc_now()
            session.commit()
            logger.error(
                "Source video generation failed job_id=%s error_code=%s",
                job_id,
                error_code,
            )

    def _cleanup_partials(self, job_id: str, *, remove_final: bool = False) -> None:
        try:
            job_dir = self._storage.job_directory(job_id, create=False)
        except Exception:
            return
        if not job_dir.exists():
            return
        names = [
            SOURCE_VIDEO_PARTIAL,
            SOURCE_VIDEO_SOURCE,
            f"{SOURCE_VIDEO_FILENAME}.partial",
        ]
        if remove_final:
            names.append(SOURCE_VIDEO_FILENAME)
        for name in names:
            path = job_dir / name
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.error("Failed cleaning partial source video for job_id=%s", job_id)

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
