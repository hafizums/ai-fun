"""Deterministic Gate 7 transition-detector unit tests."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.services.transition_detector import (
    METHOD_MIDPOINT,
    METHOD_MOTION_PEAK,
    detect_transition,
    detect_transition_from_scores,
)


def _defaults(**overrides):
    base = {
        "analysis_fps": 8.0,
        "duration_seconds": 5.0,
        "search_start_ratio": 0.35,
        "search_end_ratio": 0.70,
        "min_seconds_from_edge": 0.75,
        "confidence_threshold": 0.08,
    }
    base.update(overrides)
    return base


def test_central_motion_spike_selected() -> None:
    fps = 8.0
    duration = 5.0
    n = int(duration * fps)
    scores = np.ones(n, dtype=np.float64) * 1.0
    # Spike near 2.5s (within 35%–70%).
    spike_idx = int(2.5 * fps)
    scores[spike_idx] = 20.0
    result = detect_transition_from_scores(scores, **_defaults())
    assert result.method == METHOD_MOTION_PEAK
    assert abs(result.transition_seconds - spike_idx / fps) < 0.2
    assert 0.0 <= result.confidence <= 1.0
    assert math.isfinite(result.confidence)


def test_flat_motion_falls_back_to_midpoint() -> None:
    scores = np.ones(40, dtype=np.float64) * 2.0
    result = detect_transition_from_scores(scores, **_defaults())
    assert result.method == METHOD_MIDPOINT
    assert abs(result.transition_seconds - 2.5) < 1e-6


def test_spike_outside_search_window_ignored() -> None:
    fps = 8.0
    scores = np.ones(40, dtype=np.float64) * 1.0
    # Spike at ~0.5s (before 35% window).
    scores[int(0.5 * fps)] = 50.0
    # Mild peak inside window so motion_peak could win if we wrongly searched all.
    scores[int(2.5 * fps)] = 3.0
    result = detect_transition_from_scores(scores, **_defaults(confidence_threshold=0.5))
    # Flat-ish window relative to threshold → midpoint, or weak peak; either way
    # must not select the out-of-window spike.
    assert abs(result.transition_seconds - 0.5) > 0.4


def test_edge_bounds_enforced() -> None:
    fps = 8.0
    scores = np.ones(40, dtype=np.float64) * 1.0
    # Put spike exactly at search start but clamp with large edge.
    scores[int(0.35 * 5.0 * fps)] = 30.0
    result = detect_transition_from_scores(
        scores,
        **_defaults(min_seconds_from_edge=1.5, search_start_ratio=0.0, search_end_ratio=1.0),
    )
    assert result.transition_seconds >= 1.5
    assert result.transition_seconds <= 5.0 - 1.5


def test_too_few_frames_fallback() -> None:
    result = detect_transition_from_scores(np.array([1.0]), **_defaults())
    assert result.method == METHOD_MIDPOINT
    assert result.frames_analyzed == 1


def test_non_finite_scores_fallback() -> None:
    scores = np.array([1.0, math.nan, 2.0, 3.0])
    result = detect_transition_from_scores(scores, **_defaults())
    assert result.method == METHOD_MIDPOINT


def test_confidence_bounded() -> None:
    scores = np.linspace(1.0, 2.0, 40)
    scores[20] = 100.0
    result = detect_transition_from_scores(scores, **_defaults())
    assert 0.0 <= result.confidence <= 1.0
    assert math.isfinite(result.confidence)


def test_temp_frames_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    frames_dir = tmp_path / "work" / "frames"

    def fake_extract(*_a, **_k):
        frames_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image

        for i in range(5):
            Image.new("L", (96, 160), color=i * 10).save(frames_dir / f"frame_{i:05d}.png")

    monkeypatch.setattr(
        "app.services.transition_detector._extract_frames", fake_extract
    )
    result = detect_transition(
        video,
        duration_seconds=5.0,
        ffmpeg_binary="ffmpeg",
        work_dir=tmp_path / "work",
        analysis_fps=8.0,
        search_start_ratio=0.35,
        search_end_ratio=0.70,
        min_seconds_from_edge=0.75,
        confidence_threshold=0.08,
    )
    assert not frames_dir.exists()
    assert result.frames_analyzed >= 0
    assert math.isfinite(result.transition_seconds)


def test_ffmpeg_timeout_maps_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    def timeout_run(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

    monkeypatch.setattr(
        "app.services.transition_detector.detect_binary",
        lambda *_a, **_k: MagicMock(available=True, resolved_path="/fake/ffmpeg"),
    )
    monkeypatch.setattr(
        "app.services.transition_detector.subprocess.run", timeout_run
    )
    result = detect_transition(
        video,
        duration_seconds=5.0,
        ffmpeg_binary="ffmpeg",
        work_dir=tmp_path / "work",
        analysis_fps=8.0,
        search_start_ratio=0.35,
        search_end_ratio=0.70,
        min_seconds_from_edge=0.75,
        confidence_threshold=0.08,
    )
    assert result.method == METHOD_MIDPOINT
    assert abs(result.transition_seconds - 2.5) < 1e-6


def test_raw_stderr_not_exposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    secret = "SECRET_STDERR_LEAK_TOKEN"

    def failing_run(*_a, **_k):
        return MagicMock(returncode=1, stdout="", stderr=secret)

    monkeypatch.setattr(
        "app.services.transition_detector.detect_binary",
        lambda *_a, **_k: MagicMock(available=True, resolved_path="/fake/ffmpeg"),
    )
    monkeypatch.setattr(
        "app.services.transition_detector.subprocess.run", failing_run
    )
    result = detect_transition(
        video,
        duration_seconds=5.0,
        ffmpeg_binary="ffmpeg",
        work_dir=tmp_path / "work",
        analysis_fps=8.0,
        search_start_ratio=0.35,
        search_end_ratio=0.70,
        min_seconds_from_edge=0.75,
        confidence_threshold=0.08,
    )
    assert result.method == METHOD_MIDPOINT
    assert secret not in repr(result)
