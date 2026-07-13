"""FFmpeg / ffprobe binary detection helpers."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class BinaryCheck:
    name: str
    configured: str
    available: bool
    resolved_path: str | None
    version_line: str | None
    detail: str


def detect_binary(binary_name: str, *, label: str) -> BinaryCheck:
    """Locate a binary on PATH (or absolute path) and optionally read its version."""
    resolved = shutil.which(binary_name)
    if resolved is None:
        # Absolute path that which() may not find on some platforms
        from pathlib import Path

        candidate = Path(binary_name)
        if candidate.is_file():
            resolved = str(candidate.resolve())

    if not resolved:
        return BinaryCheck(
            name=label,
            configured=binary_name,
            available=False,
            resolved_path=None,
            version_line=None,
            detail=f"{label} not found: {binary_name}",
        )

    version_line: str | None = None
    try:
        completed = subprocess.run(
            [resolved, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        version_line = output.splitlines()[0] if output else None
    except (OSError, subprocess.TimeoutExpired):
        version_line = None

    return BinaryCheck(
        name=label,
        configured=binary_name,
        available=True,
        resolved_path=resolved,
        version_line=version_line,
        detail="ok",
    )
