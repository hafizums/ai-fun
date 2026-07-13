"""Local MP4 normalization / remux for Gate 5/6 videos."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from app.providers.media_exceptions import (
    FfmpegNotAvailableError,
    MediaError,
    VideoNormalizationFailedError,
)
from app.services.ffmpeg import detect_binary
from app.services.video_probe import (
    CONTROL_VIDEO_ERRORS,
    SOURCE_VIDEO_ERRORS,
    VideoMetadata,
    VideoValidationErrors,
    validate_video_probe,
)

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT_SECONDS = 120


def normalize_video(
    source_path: Path,
    final_path: Path,
    *,
    ffmpeg_binary: str,
    ffprobe_binary: str,
    target_duration: float,
    min_duration: float,
    max_duration: float,
    duration_tolerance: float,
    min_width: int,
    min_height: int,
    max_pixels: int,
    max_fps: float,
    errors: VideoValidationErrors,
) -> VideoMetadata:
    """Validate provider video, remux/normalize to MP4, validate final, publish atomically."""
    validate_video_probe(
        source_path,
        ffprobe_binary=ffprobe_binary,
        target_duration=target_duration,
        min_duration=min_duration,
        max_duration=max_duration,
        duration_tolerance=duration_tolerance,
        min_width=min_width,
        min_height=min_height,
        max_pixels=max_pixels,
        max_fps=max_fps,
        errors=errors,
    )

    check = detect_binary(ffmpeg_binary, label="ffmpeg")
    if not check.available or not check.resolved_path:
        raise FfmpegNotAvailableError()

    partial = final_path.with_suffix(final_path.suffix + ".partial")
    try:
        if partial.exists():
            partial.unlink()
        if not _try_stream_copy(check.resolved_path, source_path, partial):
            _reencode_h264(check.resolved_path, source_path, partial)
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise VideoNormalizationFailedError()
        metadata = validate_video_probe(
            partial,
            ffprobe_binary=ffprobe_binary,
            target_duration=target_duration,
            min_duration=min_duration,
            max_duration=max_duration,
            duration_tolerance=duration_tolerance,
            min_width=min_width,
            min_height=min_height,
            max_pixels=max_pixels,
            max_fps=max_fps,
            errors=errors,
        )
        os.replace(partial, final_path)
        return VideoMetadata(
            width=metadata.width,
            height=metadata.height,
            duration_seconds=metadata.duration_seconds,
            fps=metadata.fps,
            codec=metadata.codec,
            container="mp4",
            size_bytes=final_path.stat().st_size,
            has_audio=metadata.has_audio,
        )
    except (FfmpegNotAvailableError, VideoNormalizationFailedError):
        _cleanup(partial)
        raise
    except Exception as exc:
        _cleanup(partial)
        if isinstance(exc, MediaError):
            raise
        logger.error("Video normalization failed exception_class=Unexpected")
        raise VideoNormalizationFailedError() from None


def normalize_source_video(
    source_path: Path,
    final_path: Path,
    *,
    ffmpeg_binary: str,
    ffprobe_binary: str,
    target_duration: float,
    min_duration: float,
    max_duration: float,
    duration_tolerance: float,
    min_width: int,
    min_height: int,
    max_pixels: int,
    max_fps: float,
) -> VideoMetadata:
    """Gate 5 source-video normalization wrapper."""
    return normalize_video(
        source_path,
        final_path,
        ffmpeg_binary=ffmpeg_binary,
        ffprobe_binary=ffprobe_binary,
        target_duration=target_duration,
        min_duration=min_duration,
        max_duration=max_duration,
        duration_tolerance=duration_tolerance,
        min_width=min_width,
        min_height=min_height,
        max_pixels=max_pixels,
        max_fps=max_fps,
        errors=SOURCE_VIDEO_ERRORS,
    )


def normalize_control_video(
    source_path: Path,
    final_path: Path,
    *,
    ffmpeg_binary: str,
    ffprobe_binary: str,
    target_duration: float,
    min_duration: float,
    max_duration: float,
    duration_tolerance: float,
    min_width: int,
    min_height: int,
    max_pixels: int,
    max_fps: float,
) -> VideoMetadata:
    """Gate 6 controlled-video normalization wrapper."""
    return normalize_video(
        source_path,
        final_path,
        ffmpeg_binary=ffmpeg_binary,
        ffprobe_binary=ffprobe_binary,
        target_duration=target_duration,
        min_duration=min_duration,
        max_duration=max_duration,
        duration_tolerance=duration_tolerance,
        min_width=min_width,
        min_height=min_height,
        max_pixels=max_pixels,
        max_fps=max_fps,
        errors=CONTROL_VIDEO_ERRORS,
    )


def _try_stream_copy(ffmpeg: str, source: Path, dest: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "mp4",
                "-i",
                str(source),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _cleanup(dest)
        return False
    if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        _cleanup(dest)
        return False
    return True


def _reencode_h264(ffmpeg: str, source: Path, dest: Path) -> None:
    try:
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "mp4",
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup(dest)
        raise VideoNormalizationFailedError() from exc
    except OSError as exc:
        _cleanup(dest)
        raise FfmpegNotAvailableError() from exc
    if completed.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        _cleanup(dest)
        raise VideoNormalizationFailedError()


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.error("Failed to remove partial normalized video")
