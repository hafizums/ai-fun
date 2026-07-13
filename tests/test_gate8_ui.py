"""Gate 8 UI serving, headers, and contract tests."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.models.job import GenerationJob, JobStatus
from app.services.base_image_generation import BASE_IMAGE_FILENAME
from tests.media_fakes import make_portrait_bytes

WEB_DIR = Path(__file__).resolve().parents[1] / "app" / "web"


def test_ui_index_returns_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "AI Fun Motion" in resp.text
    assert "/static/app.js" in resp.text
    assert "/static/styles.css" in resp.text
    assert "WAVESPEED_API_KEY" not in resp.text
    assert "sk-" not in resp.text
    assert "api_key" not in resp.text


def test_ui_job_shell(client: TestClient) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "AI Fun Motion" in resp.text


def test_ui_job_rejects_traversal(client: TestClient) -> None:
    assert client.get("/jobs/../etc/passwd").status_code in {404, 422}


def test_static_css_js_content_types(client: TestClient) -> None:
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert (
        "javascript" in js.headers["content-type"]
        or "ecmascript" in js.headers["content-type"]
        or js.headers["content-type"].startswith("text/plain")
        or "application/octet-stream" in js.headers["content-type"]
    )
    wf = client.get("/static/workflow.js")
    assert wf.status_code == 200


def test_unknown_static_404(client: TestClient) -> None:
    assert client.get("/static/nope-does-not-exist.js").status_code == 404


def test_docs_and_api_still_work(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/health").status_code == 200
    created = client.post("/api/jobs")
    assert created.status_code == 201
    job_id = created.json()["id"]
    assert client.get(f"/api/jobs/{job_id}").status_code == 200


def test_security_headers_on_ui(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "unsafe-eval" not in csp
    assert "script-src 'self'" in csp


def test_frontend_files_have_no_secrets_or_remote(client: TestClient) -> None:
    for name in ("index.html", "app.js", "workflow.js", "styles.css"):
        text = (WEB_DIR / name).read_text(encoding="utf-8")
        assert "WAVESPEED_API_KEY" not in text
        assert "eval(" not in text
        assert "new Function" not in text
        assert "unsafe-eval" not in text
        # No remote script/style loads in UI assets.
        assert "https://" not in text
        assert "http://" not in text
        assert re.search(r"\bsk-[A-Za-z0-9]", text) is None


def test_ui_page_load_does_not_call_provider(client, app) -> None:
    fake = app.state.wavespeed
    before_calls = len(getattr(fake, "calls", []))
    before_uploads = len(getattr(fake, "upload_calls", []))
    assert client.get("/").status_code == 200
    job_id = client.post("/api/jobs").json()["id"]
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert len(getattr(fake, "calls", [])) == before_calls
    assert len(getattr(fake, "upload_calls", [])) == before_uploads


def test_endpoint_contracts_from_ui_perspective(client, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    # Prompt accept (uses fake LLM via fixture)
    resp = client.post(
        f"/api/jobs/{job_id}/generate-prompts",
        json={
            "subject_description": "one young child looking at the camera",
            "scene_description": "a simple ordinary indoor room",
            "motion_description": "a quick hand flick across the face",
            "duration_seconds": 5,
        },
    )
    assert resp.status_code == 202

    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.BASE_IMAGE_READY
        job.base_image_url = f"/api/jobs/{job_id}/base-image/file"
        session.commit()
    job_dir = app.state.storage.job_directory(job_id, create=True)
    (job_dir / BASE_IMAGE_FILENAME).write_bytes(make_portrait_bytes())
    up = client.post(
        f"/api/jobs/{job_id}/reference-image",
        files={"file": ("ref.png", make_portrait_bytes(width=400, height=500), "image/png")},
    )
    assert up.status_code in {200, 201, 202}


def test_delete_respects_backend(client, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    from app.models.job import GenerationJob, JobStatus

    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.PROMPT_GENERATING
        session.commit()
    assert client.delete(f"/api/jobs/{job_id}").status_code == 409


def test_completed_exposes_final_routes(client, app) -> None:
    from app.models.job import GenerationJob, JobStatus
    from app.services.final_video_assembly import (
        FINAL_VIDEO_FILENAME,
        TRANSITION_META_FILENAME,
        relative_final_video_path,
    )
    from tests.media_fakes import make_portrait_mp4_bytes

    job_id = client.post("/api/jobs").json()["id"]
    mp4 = make_portrait_mp4_bytes()
    final_dir = app.state.storage.final_job_directory(job_id, create=True)
    (final_dir / FINAL_VIDEO_FILENAME).write_bytes(mp4)
    import json

    (final_dir / TRANSITION_META_FILENAME).write_text(
        json.dumps(
            {
                "job_id": job_id,
                "transition_seconds": 2.5,
                "method": "midpoint_fallback",
                "confidence": 0.0,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.COMPLETED
        job.final_video_path = relative_final_video_path(job_id)
        job.transition_time_seconds = 2.5
        job.progress_percent = 100
        session.commit()
    assert client.get(f"/api/jobs/{job_id}/final-video").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/transition").status_code == 200
    file_resp = client.get(f"/api/jobs/{job_id}/final-video/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("video/mp4")
