"""Health endpoint tests."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.ffmpeg import BinaryCheck


def test_health_success_structure(client: TestClient) -> None:
    # Force media tools present so structure is stable regardless of host.
    fake_ok = BinaryCheck(
        name="ffmpeg",
        configured="ffmpeg",
        available=True,
        resolved_path="/fake/ffmpeg",
        version_line="ffmpeg version test",
        detail="ok",
    )
    with patch("app.api.health.detect_binary", return_value=fake_ok):
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert body["status"] in {"ok", "degraded", "error"}
    names = {c["name"] for c in body["checks"]}
    assert names >= {
        "application",
        "database",
        "storage",
        "ffmpeg",
        "ffprobe",
        "wavespeed",
        "task_runner",
    }
    for check in body["checks"]:
        assert "name" in check
        assert "status" in check
        assert "detail" in check


def test_health_when_ffmpeg_missing(client: TestClient) -> None:
    def _detect(binary_name: str, *, label: str) -> BinaryCheck:
        if label == "ffmpeg":
            return BinaryCheck(
                name="ffmpeg",
                configured=binary_name,
                available=False,
                resolved_path=None,
                version_line=None,
                detail="ffmpeg not found",
            )
        return BinaryCheck(
            name=label,
            configured=binary_name,
            available=True,
            resolved_path="/fake/ffprobe",
            version_line="ffprobe version test",
            detail="ok",
        )

    with patch("app.api.health.detect_binary", side_effect=_detect):
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    ffmpeg_check = next(c for c in body["checks"] if c["name"] == "ffmpeg")
    assert ffmpeg_check["status"] == "error"
