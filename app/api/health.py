"""Health check endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.schemas.job import HealthComponent, HealthResponse
from app.services.ffmpeg import detect_binary

if TYPE_CHECKING:
    pass

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Return structured local dependency diagnostics (no paid provider calls)."""
    settings = request.app.state.settings
    checks: list[HealthComponent] = []

    checks.append(HealthComponent(name="application", status="ok", detail="running"))

    # Database
    db_status = "ok"
    db_detail = "reachable"
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = "error"
        db_detail = f"database check failed: {type(exc).__name__}"
    checks.append(HealthComponent(name="database", status=db_status, detail=db_detail))

    # Storage
    storage = request.app.state.storage
    storage_status = "ok"
    storage_detail = "layout present"
    try:
        root: Path = storage.root
        if not root.exists():
            storage_status = "error"
            storage_detail = "storage root missing"
        else:
            missing = [
                n
                for n in ("uploads", "generated", "temporary", "final")
                if not (root / n).is_dir()
            ]
            if missing:
                storage_status = "degraded"
                storage_detail = f"missing subdirs: {', '.join(missing)}"
    except Exception as exc:
        storage_status = "error"
        storage_detail = f"storage check failed: {type(exc).__name__}"
    checks.append(
        HealthComponent(name="storage", status=storage_status, detail=storage_detail)
    )

    # FFmpeg / ffprobe
    ffmpeg = detect_binary(settings.ffmpeg_binary, label="ffmpeg")
    checks.append(
        HealthComponent(
            name="ffmpeg",
            status="ok" if ffmpeg.available else "error",
            detail=ffmpeg.version_line or ffmpeg.detail,
        )
    )
    ffprobe = detect_binary(settings.ffprobe_binary, label="ffprobe")
    checks.append(
        HealthComponent(
            name="ffprobe",
            status="ok" if ffprobe.available else "error",
            detail=ffprobe.version_line or ffprobe.detail,
        )
    )

    # WaveSpeed configured? (no paid request)
    ws_configured = settings.wavespeed_configured
    checks.append(
        HealthComponent(
            name="wavespeed",
            status="ok" if ws_configured else "degraded",
            detail="API key configured" if ws_configured else "API key not configured",
        )
    )

    # Task runner
    runner = request.app.state.task_runner
    runner_ok = runner.is_running
    checks.append(
        HealthComponent(
            name="task_runner",
            status="ok" if runner_ok else "error",
            detail=(
                f"running (workers={runner.max_workers})"
                if runner_ok
                else "not running"
            ),
        )
    )

    statuses = {c.status for c in checks}
    if "error" in statuses:
        overall = "error"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    return HealthResponse(status=overall, checks=checks)
