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
    FfprobeNotAvailableError,
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


def _parse_fps(rate: object) -> float:
    if rate is None:
        raise SourceVideoInvalidFrameRateError()
    text = str(rate).strip()
    if not text or text in {"0/0", "N/A"}:
        raise SourceVideoInvalidFrameRateError()
    try:
        value = float(Fraction(text))
    except (ValueError, ZeroDivisionError) as exc:
        raise SourceVideoInvalidFrameRateError() from exc
    if not math.isfinite(value) or value <= 0:
        raise SourceVideoInvalidFrameRateError()
    return value


def probe_video(path: Path, *, ffprobe_binary: str) -> dict:
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
        raise SourceVideoInvalidFileError() from exc
    except OSError as exc:
        raise FfprobeNotAvailableError() from exc
    if completed.returncode != 0:
        raise SourceVideoInvalidFileError()
    try:
        return json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise SourceVideoInvalidFileError() from exc


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
    """Validate a local video file for Gate 5 source-video acceptance."""
    if not path.is_file() or path.stat().st_size <= 0:
        raise SourceVideoInvalidFileError()

    data = probe_video(path, ffprobe_binary=ffprobe_binary)
    streams = data.get("streams") or []
    if not isinstance(streams, list):
        raise SourceVideoInvalidFileError()

    video_streams = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"]
    if len(video_streams) != 1:
        raise SourceVideoInvalidFileError()
    video = video_streams[0]

    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError) as exc:
        raise SourceVideoInvalidDimensionsError() from exc

    # Interpret rotation metadata for displayed dimensions.
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
        raise SourceVideoInvalidDimensionsError()
    if width < min_width or height < min_height:
        raise SourceVideoInvalidDimensionsError()
    if height <= width:
        raise SourceVideoInvalidDimensionsError()
    if width * height > max_pixels:
        raise SourceVideoInvalidDimensionsError()

    format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
    duration_raw = video.get("duration") or format_info.get("duration")
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError) as exc:
        raise SourceVideoInvalidDurationError() from exc
    if not math.isfinite(duration) or duration <= 0:
        raise SourceVideoInvalidDurationError()
    if duration < min_duration or duration > max_duration:
        raise SourceVideoInvalidDurationError()
    if abs(duration - target_duration) > duration_tolerance:
        raise SourceVideoInvalidDurationError()

    fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if fps > max_fps:
        raise SourceVideoInvalidFrameRateError()

    codec = str(video.get("codec_name") or "unknown")
    container = str(format_info.get("format_name") or path.suffix.lstrip(".") or "unknown")
    if "," in container:
        # Prefer mp4 if listed among format names.
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
