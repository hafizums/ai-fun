"""Local final-video assembly: transition detect + FFmpeg splice (Gate 7).

Entirely offline — no WaveSpeed, LLM, uploads, or network.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.media_exceptions import (
    ControlVideoInvalidDimensionsError,
    ControlVideoInvalidDurationError,
    ControlVideoInvalidFileError,
    ControlVideoInvalidFrameRateError,
    ControlVideoMissingOrInvalidError,
    FfmpegNotAvailableError,
    FfprobeNotAvailableError,
    FinalVideoAssemblyFailedError,
    FinalVideoInvalidFileError,
    MediaError,
    SourceVideoInvalidDimensionsError,
    SourceVideoInvalidDurationError,
    SourceVideoInvalidFileError,
    SourceVideoInvalidFrameRateError,
    SourceVideoMissingOrInvalidError,
    TransitionAnalysisFailedError,
    VideoInputDimensionMismatchError,
    VideoInputDurationMismatchError,
)
from app.services.control_video_generation import CONTROL_VIDEO_FILENAME
from app.services.ffmpeg import detect_binary
from app.services.source_video_generation import SOURCE_VIDEO_FILENAME
from app.services.status_transitions import assert_can_transition
from app.services.storage import StorageService
from app.services.task_runner import TaskRunner
from app.services.transition_detector import (
    METHOD_MIDPOINT,
    TransitionResult,
    detect_transition,
)
from app.services.video_probe import (
    VideoMetadata,
    validate_control_video_probe,
    validate_final_video_probe,
    validate_source_video_probe,
)

logger = logging.getLogger(__name__)

FINAL_ASSEMBLY_STAGE = "final_video_assembly"
FINAL_READY_STAGE = "completed"
INITIAL_PROGRESS = 20

FINAL_VIDEO_FILENAME = "final_video.mp4"
FINAL_VIDEO_ASSEMBLING = "final_video.assembling.mp4"
TRANSITION_META_FILENAME = "transition.json"

RELATIVE_FINAL_VIDEO_PATH_TEMPLATE = "final/{job_id}/final_video.mp4"

TASK_SUBMISSION_FAILED = "TASK_SUBMISSION_FAILED"
TASK_SUBMISSION_MESSAGE = "Failed to submit the local background final-assembly task."

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "SOURCE_VIDEO_MISSING_OR_INVALID": "The source video is missing or invalid.",
    "CONTROL_VIDEO_MISSING_OR_INVALID": "The controlled video is missing or invalid.",
    "VIDEO_INPUT_DURATION_MISMATCH": (
        "The source and controlled videos have incompatible durations."
    ),
    "VIDEO_INPUT_DIMENSION_MISMATCH": (
        "The source and controlled videos have incompatible dimensions."
    ),
    "TRANSITION_ANALYSIS_FAILED": "Transition analysis failed.",
    "FINAL_VIDEO_ASSEMBLY_FAILED": "Failed to assemble the final video.",
    "FINAL_VIDEO_INVALID_FILE": "The final video file is invalid.",
    "FINAL_VIDEO_INVALID_DURATION": "The final video duration is invalid.",
    "FINAL_VIDEO_INVALID_DIMENSIONS": "The final video dimensions are invalid.",
    "FINAL_VIDEO_INVALID_FRAME_RATE": "The final video frame rate is invalid.",
    "FFMPEG_NOT_AVAILABLE": "ffmpeg is not available.",
    "FFPROBE_NOT_AVAILABLE": "ffprobe is not available.",
    TASK_SUBMISSION_FAILED: TASK_SUBMISSION_MESSAGE,
}


def local_final_video_url(job_id: str) -> str:
    return f"/api/jobs/{job_id}/final-video/file"


def relative_final_video_path(job_id: str) -> str:
    return RELATIVE_FINAL_VIDEO_PATH_TEMPLATE.format(job_id=job_id)


class FinalVideoAssemblyService:
    """Accept and run asynchronous local final-video assembly."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        task_runner: TaskRunner,
        storage: StorageService,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._task_runner = task_runner
        self._storage = storage
        self._settings = settings

    def accept_assembly(self, job_id: str) -> GenerationJob:
        claimed = self._atomic_claim(job_id)
        if not claimed:
            with self._session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise LookupError("Job not found")
                if job.status == JobStatus.FINAL_VIDEO_ASSEMBLING:
                    raise PermissionError("Final video assembly is already in progress")
                raise PermissionError("Job is not eligible for final video assembly")

        self._cleanup_assembly_artifacts(job_id, remove_final=True)

        try:
            self._task_runner.submit(self.run_assembly_task, job_id)
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
                    GenerationJob.status == JobStatus.CONTROL_VIDEO_READY,
                    and_(
                        GenerationJob.status == JobStatus.FAILED,
                        GenerationJob.failed_stage == FINAL_ASSEMBLY_STAGE,
                    ),
                ),
            )
            .values(
                status=JobStatus.FINAL_VIDEO_ASSEMBLING,
                current_stage=FINAL_ASSEMBLY_STAGE,
                progress_percent=INITIAL_PROGRESS,
                final_video_path=None,
                transition_time_seconds=None,
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

    def run_assembly_task(self, job_id: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                logger.error("Final-assembly worker: job_id=%s not found", job_id)
                return
            if job.status != JobStatus.FINAL_VIDEO_ASSEMBLING:
                logger.warning(
                    "Final-assembly worker: job_id=%s unexpected status=%s; skipping",
                    job_id,
                    job.status.value,
                )
                return

        analysis_dir = self._storage.temporary_job_directory(job_id, create=True)
        final_dir = self._storage.final_job_directory(job_id, create=True)
        assembling_path = final_dir / FINAL_VIDEO_ASSEMBLING
        published_path = final_dir / FINAL_VIDEO_FILENAME
        meta_path = final_dir / TRANSITION_META_FILENAME

        try:
            source_meta, control_meta = self._validate_inputs(job_id)
            transition = detect_transition(
                self._storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME,
                duration_seconds=source_meta.duration_seconds,
                ffmpeg_binary=self._settings.ffmpeg_binary,
                work_dir=analysis_dir,
                analysis_fps=self._settings.transition_analysis_fps,
                search_start_ratio=self._settings.transition_search_start_ratio,
                search_end_ratio=self._settings.transition_search_end_ratio,
                min_seconds_from_edge=self._settings.transition_min_seconds_from_edge,
                confidence_threshold=self._settings.transition_confidence_threshold,
            )
            self._assemble_final(
                job_id,
                assembling_path=assembling_path,
                transition_seconds=transition.transition_seconds,
                source_duration=source_meta.duration_seconds,
                control_duration=control_meta.duration_seconds,
            )
            validate_final_video_probe(
                assembling_path,
                ffprobe_binary=self._settings.ffprobe_binary,
                target_duration=source_meta.duration_seconds,
                min_duration=self._settings.source_video_min_duration_seconds,
                max_duration=min(
                    self._settings.source_video_max_duration_seconds,
                    self._settings.final_video_max_duration_seconds,
                ),
                duration_tolerance=(
                    self._settings.source_video_duration_tolerance_seconds
                    + self._settings.transition_crossfade_seconds
                ),
                min_width=self._settings.source_video_min_width,
                min_height=self._settings.source_video_min_height,
                max_pixels=self._settings.final_video_max_pixels,
                max_fps=self._settings.final_video_max_fps,
                require_no_audio=True,
            )
            self._publish_transition_meta(meta_path, job_id, transition)
            os.replace(assembling_path, published_path)
            validate_final_video_probe(
                published_path,
                ffprobe_binary=self._settings.ffprobe_binary,
                target_duration=source_meta.duration_seconds,
                min_duration=self._settings.source_video_min_duration_seconds,
                max_duration=min(
                    self._settings.source_video_max_duration_seconds,
                    self._settings.final_video_max_duration_seconds,
                ),
                duration_tolerance=(
                    self._settings.source_video_duration_tolerance_seconds
                    + self._settings.transition_crossfade_seconds
                ),
                min_width=self._settings.source_video_min_width,
                min_height=self._settings.source_video_min_height,
                max_pixels=self._settings.final_video_max_pixels,
                max_fps=self._settings.final_video_max_fps,
                require_no_audio=True,
            )
        except MediaError as exc:
            self._cleanup_assembly_artifacts(job_id, remove_final=True)
            self._cleanup_analysis(job_id)
            self._mark_failed(
                job_id,
                error_code=exc.code,
                error_message=SAFE_ERROR_MESSAGES.get(exc.code, exc.public_message),
            )
            return
        except Exception as exc:
            logger.error(
                "Final-assembly worker unexpected failure job_id=%s "
                "exception_class=%s",
                job_id,
                type(exc).__name__,
            )
            self._cleanup_assembly_artifacts(job_id, remove_final=True)
            self._cleanup_analysis(job_id)
            self._mark_failed(
                job_id,
                error_code="FINAL_VIDEO_ASSEMBLY_FAILED",
                error_message=SAFE_ERROR_MESSAGES["FINAL_VIDEO_ASSEMBLY_FAILED"],
            )
            return
        finally:
            self._cleanup_analysis(job_id)
            if assembling_path.exists():
                try:
                    assembling_path.unlink()
                except OSError:
                    pass

        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                self._cleanup_assembly_artifacts(job_id, remove_final=True)
                return
            if job.status != JobStatus.FINAL_VIDEO_ASSEMBLING:
                logger.warning(
                    "Final-assembly worker: job_id=%s left assembling before write; "
                    "skipping",
                    job_id,
                )
                self._cleanup_assembly_artifacts(job_id, remove_final=True)
                return
            assert_can_transition(job.status, JobStatus.COMPLETED)
            job.status = JobStatus.COMPLETED
            job.current_stage = FINAL_READY_STAGE
            job.progress_percent = 100
            job.final_video_path = relative_final_video_path(job_id)
            job.transition_time_seconds = float(transition.transition_seconds)
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
            job.updated_at = utc_now()
            try:
                session.commit()
            except Exception:
                logger.error(
                    "Final-assembly DB commit failed after publish job_id=%s "
                    "exception_class=CommitFailure",
                    job_id,
                )
                session.rollback()
                self._cleanup_assembly_artifacts(job_id, remove_final=True)
                self._mark_failed(
                    job_id,
                    error_code="FINAL_VIDEO_ASSEMBLY_FAILED",
                    error_message=SAFE_ERROR_MESSAGES["FINAL_VIDEO_ASSEMBLY_FAILED"],
                )
                return
            logger.info(
                "Final video assembly completed job_id=%s transition=%.3f method=%s",
                job_id,
                transition.transition_seconds,
                transition.method,
            )

    def _validate_inputs(self, job_id: str) -> tuple[VideoMetadata, VideoMetadata]:
        job_dir = self._storage.job_directory(job_id, create=False)
        source_path = job_dir / SOURCE_VIDEO_FILENAME
        control_path = job_dir / CONTROL_VIDEO_FILENAME
        try:
            source_meta = validate_source_video_probe(
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
            FfprobeNotAvailableError,
        ) as exc:
            if isinstance(exc, FfprobeNotAvailableError):
                raise
            raise SourceVideoMissingOrInvalidError() from exc

        try:
            control_meta = validate_control_video_probe(
                control_path,
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
        except (
            ControlVideoInvalidFileError,
            ControlVideoInvalidDurationError,
            ControlVideoInvalidDimensionsError,
            ControlVideoInvalidFrameRateError,
            FfprobeNotAvailableError,
        ) as exc:
            if isinstance(exc, FfprobeNotAvailableError):
                raise
            raise ControlVideoMissingOrInvalidError() from exc

        duration_delta = abs(
            source_meta.duration_seconds - control_meta.duration_seconds
        )
        if duration_delta > self._settings.final_video_max_input_duration_delta_seconds:
            raise VideoInputDurationMismatchError()

        dim_delta = self._settings.final_video_max_dimension_delta_pixels
        if (
            abs(source_meta.width - control_meta.width) > dim_delta
            or abs(source_meta.height - control_meta.height) > dim_delta
        ):
            raise VideoInputDimensionMismatchError()

        return source_meta, control_meta

    def _assemble_final(
        self,
        job_id: str,
        *,
        assembling_path: Path,
        transition_seconds: float,
        source_duration: float,
        control_duration: float,
    ) -> None:
        check = detect_binary(self._settings.ffmpeg_binary, label="ffmpeg")
        if not check.available or not check.resolved_path:
            raise FfmpegNotAvailableError()

        job_dir = self._storage.job_directory(job_id, create=False)
        source_path = job_dir / SOURCE_VIDEO_FILENAME
        control_path = job_dir / CONTROL_VIDEO_FILENAME

        fade = float(self._settings.transition_crossfade_seconds)
        # Keep splice within both clips; leave room for crossfade overlap.
        t = float(transition_seconds)
        max_t = min(source_duration, control_duration) - fade - 0.05
        min_t = fade + 0.05
        if max_t <= min_t:
            # Extremely short clips: hard cut at midpoint, no crossfade.
            fade = 0.0
            t = min(source_duration, control_duration) / 2.0
            offset = t
            filter_complex = (
                f"[0:v]trim=0:{t},setpts=PTS-STARTPTS[v0];"
                f"[1:v]trim={t}:{control_duration},setpts=PTS-STARTPTS[v1];"
                f"[v0][v1]concat=n=2:v=1:a=0[vout]"
            )
        else:
            t = min(max(t, min_t), max_t)
            offset = max(0.0, t - fade)
            # Source: 0 → t; Controlled: t-fade → end; xfade overlap of `fade`.
            filter_complex = (
                f"[0:v]trim=0:{t},setpts=PTS-STARTPTS[v0];"
                f"[1:v]trim={t - fade}:{control_duration},setpts=PTS-STARTPTS[v1];"
                f"[v0][v1]xfade=transition=fade:duration={fade}:offset={offset}[vout]"
            )

        if assembling_path.exists():
            assembling_path.unlink()

        cmd = [
            check.resolved_path,
            "-y",
            "-i",
            str(source_path),
            "-i",
            str(control_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(assembling_path),
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._settings.final_video_ffmpeg_timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FinalVideoAssemblyFailedError() from exc
        except OSError as exc:
            raise FfmpegNotAvailableError() from exc

        if completed.returncode != 0:
            logger.error(
                "Final assembly ffmpeg failed job_id=%s exception_class=FfmpegFail",
                job_id,
            )
            raise FinalVideoAssemblyFailedError()
        if not assembling_path.is_file() or assembling_path.stat().st_size <= 0:
            raise FinalVideoAssemblyFailedError()

    def _publish_transition_meta(
        self, meta_path: Path, job_id: str, transition: TransitionResult
    ) -> None:
        payload = {
            "job_id": job_id,
            "transition_seconds": float(transition.transition_seconds),
            "method": transition.method,
            "confidence": float(transition.confidence),
            "analysis_fps": float(transition.analysis_fps),
            "frames_analyzed": int(transition.frames_analyzed),
        }
        if not math.isfinite(payload["transition_seconds"]) or payload[
            "transition_seconds"
        ] < 0:
            raise TransitionAnalysisFailedError()
        if payload["method"] not in {"motion_peak", "midpoint_fallback"}:
            raise TransitionAnalysisFailedError()
        if not math.isfinite(payload["confidence"]) or not (
            0.0 <= payload["confidence"] <= 1.0
        ):
            raise TransitionAnalysisFailedError()

        partial = meta_path.with_suffix(".json.partial")
        partial.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, meta_path)

    def inspect_ready_video(self, job_id: str) -> VideoMetadata:
        path = self._storage.final_job_directory(job_id, create=False) / FINAL_VIDEO_FILENAME
        return validate_final_video_probe(
            path,
            ffprobe_binary=self._settings.ffprobe_binary,
            target_duration=float(
                self._settings.wavespeed_source_video_duration_seconds
            ),
            min_duration=self._settings.source_video_min_duration_seconds,
            max_duration=min(
                self._settings.source_video_max_duration_seconds,
                self._settings.final_video_max_duration_seconds,
            ),
            duration_tolerance=(
                self._settings.source_video_duration_tolerance_seconds
                + self._settings.transition_crossfade_seconds
            ),
            min_width=self._settings.source_video_min_width,
            min_height=self._settings.source_video_min_height,
            max_pixels=self._settings.final_video_max_pixels,
            max_fps=self._settings.final_video_max_fps,
            require_no_audio=True,
        )

    def load_transition_meta(self, job_id: str) -> dict:
        """Load and validate transition.json for COMPLETED jobs."""
        path = (
            self._storage.final_job_directory(job_id, create=False)
            / TRANSITION_META_FILENAME
        )
        if not path.is_file():
            raise FinalVideoInvalidFileError()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FinalVideoInvalidFileError() from exc
        if not isinstance(data, dict):
            raise FinalVideoInvalidFileError()
        required = {
            "job_id",
            "transition_seconds",
            "method",
            "confidence",
        }
        if not required.issubset(data.keys()):
            raise FinalVideoInvalidFileError()
        if data.get("job_id") != job_id:
            raise FinalVideoInvalidFileError()
        try:
            seconds = float(data["transition_seconds"])
            confidence = float(data["confidence"])
        except (TypeError, ValueError) as exc:
            raise FinalVideoInvalidFileError() from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise FinalVideoInvalidFileError()
        if not math.isfinite(confidence) or not (0.0 <= confidence <= 1.0):
            raise FinalVideoInvalidFileError()
        method = data.get("method")
        if method not in {"motion_peak", METHOD_MIDPOINT}:
            raise FinalVideoInvalidFileError()
        return {
            "job_id": job_id,
            "transition_seconds": seconds,
            "method": method,
            "confidence": confidence,
        }

    def _mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if job.status != JobStatus.FINAL_VIDEO_ASSEMBLING:
                return
            try:
                assert_can_transition(job.status, JobStatus.FAILED)
            except Exception:
                pass
            job.status = JobStatus.FAILED
            job.failed_stage = FINAL_ASSEMBLY_STAGE
            job.error_code = error_code
            job.error_message = error_message
            job.current_stage = FINAL_ASSEMBLY_STAGE
            job.final_video_path = None
            job.transition_time_seconds = None
            job.updated_at = utc_now()
            session.commit()
            logger.error(
                "Final video assembly failed job_id=%s error_code=%s",
                job_id,
                error_code,
            )

    def _cleanup_assembly_artifacts(
        self, job_id: str, *, remove_final: bool = False
    ) -> None:
        try:
            final_dir = self._storage.final_job_directory(job_id, create=False)
        except Exception:
            return
        if not final_dir.exists():
            return
        names = [FINAL_VIDEO_ASSEMBLING, f"{TRANSITION_META_FILENAME}.partial"]
        if remove_final:
            names.extend([FINAL_VIDEO_FILENAME, TRANSITION_META_FILENAME])
        for name in names:
            path = final_dir / name
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                logger.error(
                    "Failed cleaning final-assembly artifact for job_id=%s", job_id
                )

    def _cleanup_analysis(self, job_id: str) -> None:
        try:
            analysis_dir = self._storage.temporary_job_directory(job_id, create=False)
        except Exception:
            return
        if analysis_dir.exists():
            shutil.rmtree(analysis_dir, ignore_errors=True)
