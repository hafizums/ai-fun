"""Background Fun Control / controlled-video generation service (Gate 6)."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.base import MediaProvider
from app.providers.media_exceptions import (
    ControlVideoDownloadError,
    ControlVideoTooLargeError,
    EditedImageMissingOrInvalidError,
    EditImageInvalidAspectRatioError,
    EditImageInvalidFileError,
    MediaError,
    PromptPackageCorruptedError,
    SourceVideoInvalidDimensionsError,
    SourceVideoInvalidDurationError,
    SourceVideoInvalidFileError,
    SourceVideoInvalidFrameRateError,
    SourceVideoMissingOrInvalidError,
)
from app.services.character_edit_generation import EDITED_IMAGE_FILENAME
from app.services.image_download import SecureArtifactDownloader
from app.services.image_normalize import inspect_edited_png
from app.services.prompt_generation import load_prompt_envelope
from app.services.source_video_generation import SOURCE_VIDEO_FILENAME
from app.services.status_transitions import assert_can_transition
from app.services.storage import StorageService
from app.services.task_runner import TaskRunner
from app.services.video_normalize import normalize_control_video
from app.services.video_probe import (
    VideoMetadata,
    validate_control_video_probe,
    validate_source_video_probe,
)

logger = logging.getLogger(__name__)

CONTROL_VIDEO_STAGE = "control_video_generation"
CONTROL_VIDEO_READY_STAGE = "control_video_ready"
INITIAL_PROGRESS = 20
CONTROL_VIDEO_FILENAME = "controlled_video.mp4"
CONTROL_VIDEO_PARTIAL = "controlled_video.download"
CONTROL_VIDEO_SOURCE = "controlled_video.source"

TASK_SUBMISSION_FAILED = "TASK_SUBMISSION_FAILED"
TASK_SUBMISSION_MESSAGE = "Failed to submit the local background controlled-video task."

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "MEDIA_NOT_CONFIGURED": "The media provider is not configured.",
    "MEDIA_AUTHENTICATION_FAILED": "Media provider authentication failed.",
    "MEDIA_TIMEOUT": "The media provider request timed out.",
    "MEDIA_CONNECTION_FAILED": "Could not connect to the media provider.",
    "MEDIA_REQUEST_FAILED": "The media provider request failed.",
    "MEDIA_INVALID_RESULT": "The media provider returned an invalid result.",
    "PROMPT_PACKAGE_CORRUPTED": "The stored prompt package is corrupted or incomplete.",
    "EDITED_IMAGE_MISSING_OR_INVALID": "The edited image is missing or invalid.",
    "SOURCE_VIDEO_MISSING_OR_INVALID": "The source video is missing or invalid.",
    "CONTROL_VIDEO_DOWNLOAD_FAILED": "Failed to download the controlled video.",
    "CONTROL_VIDEO_TOO_LARGE": "The controlled video exceeded the download size limit.",
    "CONTROL_VIDEO_INVALID_FILE": "The controlled video file is invalid.",
    "CONTROL_VIDEO_INVALID_DURATION": "The controlled video duration is invalid.",
    "CONTROL_VIDEO_INVALID_DIMENSIONS": "The controlled video dimensions are invalid.",
    "CONTROL_VIDEO_INVALID_FRAME_RATE": "The controlled video frame rate is invalid.",
    "FFPROBE_NOT_AVAILABLE": "ffprobe is not available.",
    "FFMPEG_NOT_AVAILABLE": "ffmpeg is not available.",
    "VIDEO_NORMALIZATION_FAILED": "Failed to normalize the source video.",
    TASK_SUBMISSION_FAILED: TASK_SUBMISSION_MESSAGE,
}


def local_controlled_video_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/controlled-video/file"


def controlled_video_path(storage: StorageService, job_id: str) -> Path:
    return storage.job_directory(job_id, create=True) / CONTROL_VIDEO_FILENAME


class ControlVideoGenerationService:
    """Accept and run asynchronous Fun Control generation for jobs."""

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
            timeout_seconds=settings.control_video_download_timeout_seconds,
            max_bytes=settings.control_video_max_download_bytes,
            download_error_cls=ControlVideoDownloadError,
            too_large_error_cls=ControlVideoTooLargeError,
        )

    def accept_generation(self, job_id: str) -> GenerationJob:
        claimed = self._atomic_claim(job_id)
        if not claimed:
            with self._session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise LookupError("Job not found")
                if job.status == JobStatus.CONTROL_VIDEO_GENERATING:
                    raise PermissionError(
                        "Controlled video generation is already in progress"
                    )
                raise PermissionError(
                    "Job is not eligible for controlled video generation"
                )

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
                    GenerationJob.status == JobStatus.SOURCE_VIDEO_READY,
                    and_(
                        GenerationJob.status == JobStatus.FAILED,
                        GenerationJob.failed_stage == CONTROL_VIDEO_STAGE,
                    ),
                ),
            )
            .values(
                status=JobStatus.CONTROL_VIDEO_GENERATING,
                current_stage=CONTROL_VIDEO_STAGE,
                progress_percent=INITIAL_PROGRESS,
                controlled_video_url=None,
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
                logger.error("Control-video worker: job_id=%s not found", job_id)
                return
            if job.status != JobStatus.CONTROL_VIDEO_GENERATING:
                logger.warning(
                    "Control-video worker: job_id=%s unexpected status=%s; skipping",
                    job_id,
                    job.status.value,
                )
                return
            prompt_json = job.prompt_json

        try:
            motion_prompt = self._extract_motion_prompt(prompt_json)
            job_dir = self._storage.job_directory(job_id, create=False)
            edited_path = job_dir / EDITED_IMAGE_FILENAME
            source_path = job_dir / SOURCE_VIDEO_FILENAME
            try:
                inspect_edited_png(
                    edited_path, max_pixels=self._settings.base_image_max_pixels
                )
            except (EditImageInvalidFileError, EditImageInvalidAspectRatioError) as exc:
                raise EditedImageMissingOrInvalidError() from exc
            try:
                validate_source_video_probe(
                    source_path,
                    ffprobe_binary=self._settings.ffprobe_binary,
                    target_duration=float(
                        self._settings.wavespeed_source_video_duration_seconds
                    ),
                    min_duration=self._settings.source_video_min_duration_seconds,
                    max_duration=self._settings.source_video_max_duration_seconds,
                    duration_tolerance=(
                        self._settings.source_video_duration_tolerance_seconds
                    ),
                    min_width=self._settings.source_video_min_width,
                    min_height=self._settings.source_video_min_height,
                    max_pixels=self._settings.source_video_max_pixels,
                    max_fps=self._settings.source_video_max_fps,
                )
            except (
                SourceVideoInvalidFileError,
                SourceVideoInvalidDurationError,
                SourceVideoInvalidDimensionsError,
                SourceVideoInvalidFrameRateError,
            ) as exc:
                raise SourceVideoMissingOrInvalidError() from exc

            if not self._media.is_configured():
                from app.providers.media_exceptions import MediaConfigurationError

                raise MediaConfigurationError()

            # Exact order: edited image first, source video second.
            uploaded_edited_url = self._media.upload_file(edited_path)
            uploaded_source_url = self._media.upload_file(source_path)
            input_params: dict = {
                "image": uploaded_edited_url,
                "video": uploaded_source_url,
                "prompt": motion_prompt,
                "resolution": self._settings.wavespeed_control_video_resolution,
                "seed": self._settings.wavespeed_control_video_seed,
            }
            result = self._media.run_model(
                self._settings.wavespeed_control_video_model,
                input_params,
                timeout=self._settings.wavespeed_media_timeout_seconds,
                poll_interval=self._settings.wavespeed_media_poll_interval_seconds,
                enable_sync_mode=False,
                max_task_retries=0,
            )
            provider_url = result.output_urls[0]
            out_dir = self._storage.job_directory(job_id, create=True)
            download_path = out_dir / CONTROL_VIDEO_PARTIAL
            provider_source = out_dir / CONTROL_VIDEO_SOURCE
            final_path = out_dir / CONTROL_VIDEO_FILENAME
            self._cleanup_paths(download_path, provider_source, final_path)

            self._downloader.download(provider_url, download_path)
            download_path.replace(provider_source)
            normalize_control_video(
                provider_source,
                final_path,
                ffmpeg_binary=self._settings.ffmpeg_binary,
                ffprobe_binary=self._settings.ffprobe_binary,
                target_duration=float(
                    self._settings.wavespeed_control_video_duration_seconds
                ),
                min_duration=self._settings.control_video_min_duration_seconds,
                max_duration=self._settings.control_video_max_duration_seconds,
                duration_tolerance=(
                    self._settings.control_video_duration_tolerance_seconds
                ),
                min_width=self._settings.control_video_min_width,
                min_height=self._settings.control_video_min_height,
                max_pixels=self._settings.control_video_max_pixels,
                max_fps=self._settings.control_video_max_fps,
            )
            self._cleanup_paths(provider_source, download_path)
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
                "Control-video worker unexpected failure job_id=%s "
                "exception_class=Unexpected",
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
            if job.status != JobStatus.CONTROL_VIDEO_GENERATING:
                logger.warning(
                    "Control-video worker: job_id=%s left generating before write; "
                    "skipping",
                    job_id,
                )
                self._cleanup_partials(job_id, remove_final=True)
                return
            assert_can_transition(job.status, JobStatus.CONTROL_VIDEO_READY)
            job.status = JobStatus.CONTROL_VIDEO_READY
            job.current_stage = CONTROL_VIDEO_READY_STAGE
            job.progress_percent = 100
            job.controlled_video_url = local_controlled_video_url(job_id)
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
            job.updated_at = utc_now()
            try:
                session.commit()
            except Exception:
                logger.error(
                    "Control-video DB commit failed after publish job_id=%s "
                    "exception_class=CommitFailure",
                    job_id,
                )
                session.rollback()
                self._cleanup_partials(job_id, remove_final=True)
                self._mark_failed(
                    job_id,
                    error_code="MEDIA_REQUEST_FAILED",
                    error_message=SAFE_ERROR_MESSAGES["MEDIA_REQUEST_FAILED"],
                )
                return
            logger.info("Controlled video generation completed job_id=%s", job_id)

    def inspect_ready_video(self, job_id: str) -> VideoMetadata:
        path = (
            self._storage.job_directory(job_id, create=False) / CONTROL_VIDEO_FILENAME
        )
        return validate_control_video_probe(
            path,
            ffprobe_binary=self._settings.ffprobe_binary,
            target_duration=float(
                self._settings.wavespeed_control_video_duration_seconds
            ),
            min_duration=self._settings.control_video_min_duration_seconds,
            max_duration=self._settings.control_video_max_duration_seconds,
            duration_tolerance=self._settings.control_video_duration_tolerance_seconds,
            min_width=self._settings.control_video_min_width,
            min_height=self._settings.control_video_min_height,
            max_pixels=self._settings.control_video_max_pixels,
            max_fps=self._settings.control_video_max_fps,
        )

    def _extract_motion_prompt(self, prompt_json: str | None) -> str:
        try:
            envelope = load_prompt_envelope(prompt_json)
            motion = envelope.prompts.motion_prompt
            if not isinstance(motion, str) or not motion.strip():
                raise PromptPackageCorruptedError()
            return motion
        except PromptPackageCorruptedError:
            raise
        except Exception as exc:
            raise PromptPackageCorruptedError() from exc

    def _mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if job.status != JobStatus.CONTROL_VIDEO_GENERATING:
                return
            try:
                assert_can_transition(job.status, JobStatus.FAILED)
            except Exception:
                pass
            job.status = JobStatus.FAILED
            job.failed_stage = CONTROL_VIDEO_STAGE
            job.error_code = error_code
            job.error_message = error_message
            job.current_stage = CONTROL_VIDEO_STAGE
            job.controlled_video_url = None
            job.updated_at = utc_now()
            session.commit()
            logger.error(
                "Controlled video generation failed job_id=%s error_code=%s",
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
            CONTROL_VIDEO_PARTIAL,
            CONTROL_VIDEO_SOURCE,
            f"{CONTROL_VIDEO_FILENAME}.partial",
        ]
        if remove_final:
            names.append(CONTROL_VIDEO_FILENAME)
        for name in names:
            path = job_dir / name
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.error(
                    "Failed cleaning partial controlled video for job_id=%s", job_id
                )

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
