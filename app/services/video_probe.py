"""ffprobe-based video inspection and validation."""

from __future__ import annotations

import json
import logging
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from app.providers.media_exceptions import (
    ControlVideoInvalidDimensionsError,
    ControlVideoInvalidDurationError,
    ControlVideoInvalidFileError,
    ControlVideoInvalidFrameRateError,
    FfprobeNotAvailableError,
    FinalVideoInvalidDimensionsError,
    FinalVideoInvalidDurationError,
    FinalVideoInvalidFileError,
    FinalVideoInvalidFrameRateError,
    MediaError,
    SourceVideoInvalidDimensionsError,
    SourceVideoInvalidDurationError,
    SourceVideoInvalidFileError,
    SourceVideoInvalidFrameRateError,
)
from app.services.ffmpeg import detect_binary

logger = logging.getLogger(__name__)

FFPROBE_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    duration_seconds: float
    fps: float
    codec: str
    container: str
    size_bytes: int
    has_audio: bool


@dataclass(frozen=True)
class VideoValidationErrors:
    invalid_file: type[MediaError]
    invalid_dimensions: type[MediaError]
    invalid_duration: type[MediaError]
    invalid_frame_rate: type[MediaError]


SOURCE_VIDEO_ERRORS = VideoValidationErrors(
    invalid_file=SourceVideoInvalidFileError,
    invalid_dimensions=SourceVideoInvalidDimensionsError,
    invalid_duration=SourceVideoInvalidDurationError,
    invalid_frame_rate=SourceVideoInvalidFrameRateError,
)

CONTROL_VIDEO_ERRORS = VideoValidationErrors(
    invalid_file=ControlVideoInvalidFileError,
    invalid_dimensions=ControlVideoInvalidDimensionsError,
    invalid_duration=ControlVideoInvalidDurationError,
    invalid_frame_rate=ControlVideoInvalidFrameRateError,
)

FINAL_VIDEO_ERRORS = VideoValidationErrors(
    invalid_file=FinalVideoInvalidFileError,
    invalid_dimensions=FinalVideoInvalidDimensionsError,
    invalid_duration=FinalVideoInvalidDurationError,
    invalid_frame_rate=FinalVideoInvalidFrameRateError,
)


def _parse_fps(rate: object, *, errors: VideoValidationErrors) -> float:
    if rate is None:
        raise errors.invalid_frame_rate()
    text = str(rate).strip()
    if not text or text in {"0/0", "N/A"}:
        raise errors.invalid_frame_rate()
    try:
        value = float(Fraction(text))
    except (ValueError, ZeroDivisionError) as exc:
        raise errors.invalid_frame_rate() from exc
    if not math.isfinite(value) or value <= 0:
        raise errors.invalid_frame_rate()
    return value


def probe_video(
    path: Path,
    *,
    ffprobe_binary: str,
    invalid_file_cls: type[MediaError] = SourceVideoInvalidFileError,
) -> dict:
    """Run ffprobe and return parsed JSON. Never logs stderr contents."""
    check = detect_binary(ffprobe_binary, label="ffprobe")
    if not check.available or not check.resolved_path:
        raise FfprobeNotAvailableError()
    try:
        completed = subprocess.run(
            [
                check.resolved_path,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise invalid_file_cls() from exc
    except OSError as exc:
        raise FfprobeNotAvailableError() from exc
    if completed.returncode != 0:
        raise invalid_file_cls()
    try:
        return json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise invalid_file_cls() from exc


def validate_video_probe(
    path: Path,
    *,
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
    """Validate a local portrait video against configured Gate bounds."""
    if not path.is_file() or path.stat().st_size <= 0:
        raise errors.invalid_file()

    data = probe_video(
        path,
        ffprobe_binary=ffprobe_binary,
        invalid_file_cls=errors.invalid_file,
    )
    streams = data.get("streams") or []
    if not isinstance(streams, list):
        raise errors.invalid_file()

    video_streams = [
        s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"
    ]
    audio_streams = [
        s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"
    ]
    if len(video_streams) != 1:
        raise errors.invalid_file()
    video = video_streams[0]

    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError) as exc:
        raise errors.invalid_dimensions() from exc

    rotation = 0
    try:
        tags = video.get("tags") or {}
        if isinstance(tags, dict) and "rotate" in tags:
            rotation = int(float(tags["rotate"]))
        side_data = video.get("side_data_list") or []
        if isinstance(side_data, list):
            for item in side_data:
                if isinstance(item, dict) and "rotation" in item:
                    rotation = int(float(item["rotation"]))
    except (TypeError, ValueError):
        rotation = 0
    if abs(rotation) % 180 == 90:
        width, height = height, width

    if width <= 0 or height <= 0:
        raise errors.invalid_dimensions()
    if width < min_width or height < min_height:
        raise errors.invalid_dimensions()
    if height <= width:
        raise errors.invalid_dimensions()
    if width * height > max_pixels:
        raise errors.invalid_dimensions()

    format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
    duration_raw = video.get("duration") or format_info.get("duration")
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError) as exc:
        raise errors.invalid_duration() from exc
    if not math.isfinite(duration) or duration <= 0:
        raise errors.invalid_duration()
    if duration < min_duration or duration > max_duration:
        raise errors.invalid_duration()
    if abs(duration - target_duration) > duration_tolerance:
        raise errors.invalid_duration()

    fps = _parse_fps(
        video.get("avg_frame_rate") or video.get("r_frame_rate"),
        errors=errors,
    )
    if fps > max_fps:
        raise errors.invalid_frame_rate()

    codec = str(video.get("codec_name") or "unknown")
    container = str(format_info.get("format_name") or path.suffix.lstrip(".") or "unknown")
    if "," in container:
        names = {part.strip().lower() for part in container.split(",")}
        container = "mp4" if "mp4" in names else next(iter(names))

    return VideoMetadata(
        width=width,
        height=height,
        duration_seconds=duration,
        fps=fps,
        codec=codec,
        container=container,
        size_bytes=path.stat().st_size,
        has_audio=len(audio_streams) > 0,
    )


def validate_source_video_probe(
    path: Path,
    *,
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
    """Validate a Gate 5 source video (SourceVideo* error codes)."""
    return validate_video_probe(
        path,
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


def validate_control_video_probe(
    path: Path,
    *,
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
    """Validate a Gate 6 controlled video (ControlVideo* error codes)."""
    return validate_video_probe(
        path,
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


def validate_final_video_probe(
    path: Path,
    *,
    ffprobe_binary: str,
    target_duration: float,
    min_duration: float,
    max_duration: float,
    duration_tolerance: float,
    min_width: int,
    min_height: int,
    max_pixels: int,
    max_fps: float,
    require_no_audio: bool = True,
) -> VideoMetadata:
    """Validate a Gate 7 final assembled video (FinalVideo* error codes)."""
    meta = validate_video_probe(
        path,
        ffprobe_binary=ffprobe_binary,
        target_duration=target_duration,
        min_duration=min_duration,
        max_duration=max_duration,
        duration_tolerance=duration_tolerance,
        min_width=min_width,
        min_height=min_height,
        max_pixels=max_pixels,
        max_fps=max_fps,
        errors=FINAL_VIDEO_ERRORS,
    )
    if require_no_audio and meta.has_audio:
        raise FinalVideoInvalidFileError()

    data = probe_video(
        path,
        ffprobe_binary=ffprobe_binary,
        invalid_file_cls=FinalVideoInvalidFileError,
    )
    streams = data.get("streams") or []
    video_streams = [
        s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"
    ]
    if len(video_streams) != 1:
        raise FinalVideoInvalidFileError()
    video = video_streams[0]
    codec = str(video.get("codec_name") or "").lower()
    if codec not in {"h264", "avc1", "avc"}:
        raise FinalVideoInvalidFileError()
    pix_fmt = video.get("pix_fmt")
    if pix_fmt is not None and str(pix_fmt).lower() not in {"yuv420p", "yuvj420p"}:
        raise FinalVideoInvalidFileError()
    format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
    container = str(format_info.get("format_name") or "").lower()
    if "mp4" not in container and path.suffix.lower() != ".mp4":
        raise FinalVideoInvalidFileError()
    if meta.size_bytes <= 0:
        raise FinalVideoInvalidFileError()
    return meta
