"""Lightweight local transition detector (Gate 7).

Uses FFmpeg grayscale downscaled frames + Numpy frame-difference energy.
Does not claim semantic hand/face detection.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.providers.media_exceptions import (
    FfmpegNotAvailableError,
    TransitionAnalysisFailedError,
)
from app.services.ffmpeg import detect_binary

logger = logging.getLogger(__name__)

ANALYSIS_WIDTH = 96
METHOD_MOTION_PEAK = "motion_peak"
METHOD_MIDPOINT = "midpoint_fallback"
EXTRACT_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class TransitionResult:
    transition_seconds: float
    method: str
    confidence: float
    analysis_fps: float
    frames_analyzed: int


def detect_transition(
    video_path: Path,
    *,
    duration_seconds: float,
    ffmpeg_binary: str,
    work_dir: Path,
    analysis_fps: float,
    search_start_ratio: float,
    search_end_ratio: float,
    min_seconds_from_edge: float,
    confidence_threshold: float,
) -> TransitionResult:
    """Detect a motion-energy peak within the configured search window."""
    if not video_path.is_file() or duration_seconds <= 0 or not math.isfinite(duration_seconds):
        raise TransitionAnalysisFailedError()

    edge = float(min_seconds_from_edge)
    safe_start = edge
    safe_end = max(safe_start, duration_seconds - edge)
    midpoint = _clamp(duration_seconds / 2.0, safe_start, safe_end)

    search_start = max(safe_start, duration_seconds * search_start_ratio)
    search_end = min(safe_end, duration_seconds * search_end_ratio)
    if search_end <= search_start:
        return TransitionResult(
            transition_seconds=midpoint,
            method=METHOD_MIDPOINT,
            confidence=0.0,
            analysis_fps=float(analysis_fps),
            frames_analyzed=0,
        )

    frames_dir = work_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        _extract_frames(
            video_path,
            frames_dir,
            ffmpeg_binary=ffmpeg_binary,
            analysis_fps=analysis_fps,
            width=ANALYSIS_WIDTH,
        )
        frames = _load_grayscale_frames(frames_dir)
        if len(frames) < 3:
            return TransitionResult(
                transition_seconds=midpoint,
                method=METHOD_MIDPOINT,
                confidence=0.0,
                analysis_fps=float(analysis_fps),
                frames_analyzed=len(frames),
            )

        scores = _motion_scores(frames)
        if scores.size == 0 or not np.all(np.isfinite(scores)):
            return TransitionResult(
                transition_seconds=midpoint,
                method=METHOD_MIDPOINT,
                confidence=0.0,
                analysis_fps=float(analysis_fps),
                frames_analyzed=len(frames),
            )

        # Map score index i to time between frame i and i+1.
        times = np.arange(scores.size, dtype=np.float64) / float(analysis_fps)
        mask = (times >= search_start) & (times <= search_end)
        if not np.any(mask):
            return TransitionResult(
                transition_seconds=midpoint,
                method=METHOD_MIDPOINT,
                confidence=0.0,
                analysis_fps=float(analysis_fps),
                frames_analyzed=len(frames),
            )

        window_scores = scores[mask]
        window_times = times[mask]
        peak_idx = int(np.argmax(window_scores))
        peak_score = float(window_scores[peak_idx])
        peak_time = float(window_times[peak_idx])
        peak_time = _clamp(peak_time, safe_start, safe_end)

        mean_score = float(np.mean(window_scores))
        std_score = float(np.std(window_scores))
        denom = mean_score + std_score + 1e-6
        confidence = float(max(0.0, min(1.0, (peak_score - mean_score) / denom)))

        if std_score < 1e-6 or confidence < confidence_threshold:
            return TransitionResult(
                transition_seconds=midpoint,
                method=METHOD_MIDPOINT,
                confidence=confidence,
                analysis_fps=float(analysis_fps),
                frames_analyzed=len(frames),
            )

        return TransitionResult(
            transition_seconds=peak_time,
            method=METHOD_MOTION_PEAK,
            confidence=confidence,
            analysis_fps=float(analysis_fps),
            frames_analyzed=len(frames),
        )
    except (
        TransitionAnalysisFailedError,
        FfmpegNotAvailableError,
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
    ):
        # Recoverable extraction/analysis failures fall back to midpoint.
        logger.error("Transition analysis fell back exception_class=Recoverable")
        return TransitionResult(
            transition_seconds=midpoint,
            method=METHOD_MIDPOINT,
            confidence=0.0,
            analysis_fps=float(analysis_fps),
            frames_analyzed=0,
        )
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)


def detect_transition_from_scores(
    scores: np.ndarray,
    *,
    analysis_fps: float,
    duration_seconds: float,
    search_start_ratio: float,
    search_end_ratio: float,
    min_seconds_from_edge: float,
    confidence_threshold: float,
) -> TransitionResult:
    """Pure-Numpy path for unit tests with synthetic motion scores."""
    edge = float(min_seconds_from_edge)
    safe_start = edge
    safe_end = max(safe_start, duration_seconds - edge)
    midpoint = _clamp(duration_seconds / 2.0, safe_start, safe_end)
    if scores.size < 2 or not np.all(np.isfinite(scores)):
        return TransitionResult(midpoint, METHOD_MIDPOINT, 0.0, analysis_fps, int(scores.size))

    search_start = max(safe_start, duration_seconds * search_start_ratio)
    search_end = min(safe_end, duration_seconds * search_end_ratio)
    times = np.arange(scores.size, dtype=np.float64) / float(analysis_fps)
    mask = (times >= search_start) & (times <= search_end)
    if not np.any(mask):
        return TransitionResult(midpoint, METHOD_MIDPOINT, 0.0, analysis_fps, int(scores.size))

    window_scores = scores[mask]
    window_times = times[mask]
    peak_idx = int(np.argmax(window_scores))
    peak_score = float(window_scores[peak_idx])
    peak_time = _clamp(float(window_times[peak_idx]), safe_start, safe_end)
    mean_score = float(np.mean(window_scores))
    std_score = float(np.std(window_scores))
    confidence = float(
        max(0.0, min(1.0, (peak_score - mean_score) / (mean_score + std_score + 1e-6)))
    )
    if std_score < 1e-6 or confidence < confidence_threshold:
        return TransitionResult(
            midpoint, METHOD_MIDPOINT, confidence, analysis_fps, int(scores.size)
        )
    return TransitionResult(
        peak_time, METHOD_MOTION_PEAK, confidence, analysis_fps, int(scores.size)
    )


def _extract_frames(
    video_path: Path,
    frames_dir: Path,
    *,
    ffmpeg_binary: str,
    analysis_fps: float,
    width: int,
) -> None:
    check = detect_binary(ffmpeg_binary, label="ffmpeg")
    if not check.available or not check.resolved_path:
        raise FfmpegNotAvailableError()
    pattern = str(frames_dir / "frame_%05d.png")
    completed = subprocess.run(
        [
            check.resolved_path,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={analysis_fps},scale={width}:-1,format=gray",
            "-frames:v",
            "200",
            pattern,
        ],
        capture_output=True,
        text=True,
        timeout=EXTRACT_TIMEOUT_SECONDS,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise TransitionAnalysisFailedError()


def _load_grayscale_frames(frames_dir: Path) -> list[np.ndarray]:
    paths = sorted(frames_dir.glob("frame_*.png"))
    frames: list[np.ndarray] = []
    expected_shape: tuple[int, ...] | None = None
    for path in paths:
        with Image.open(path) as img:
            arr = np.asarray(img.convert("L"), dtype=np.float32)
        if arr.ndim != 2 or arr.size == 0:
            raise TransitionAnalysisFailedError()
        if expected_shape is None:
            expected_shape = arr.shape
        elif arr.shape != expected_shape:
            raise TransitionAnalysisFailedError()
        frames.append(arr)
    return frames


def _motion_scores(frames: list[np.ndarray]) -> np.ndarray:
    diffs: list[float] = []
    height, width = frames[0].shape
    # Upper-middle central band where hand/face action is expected.
    y0, y1 = int(height * 0.15), int(height * 0.65)
    x0, x1 = int(width * 0.2), int(width * 0.8)
    for prev, curr in zip(frames[:-1], frames[1:], strict=False):
        full = float(np.mean(np.abs(curr - prev)))
        center = float(np.mean(np.abs(curr[y0:y1, x0:x1] - prev[y0:y1, x0:x1])))
        diffs.append(full + 1.5 * center)
    scores = np.asarray(diffs, dtype=np.float64)
    if scores.size >= 3:
        kernel = np.array([0.25, 0.5, 0.25], dtype=np.float64)
        scores = np.convolve(scores, kernel, mode="same")
    return scores


def _clamp(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))
