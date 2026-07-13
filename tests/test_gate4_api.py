"""Gate 4 status, reference upload, and character-edit tests (offline)."""

from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from PIL import Image
from PIL.ExifTags import Base as ExifBase

from app.models.job import GenerationJob, JobStatus
from app.providers.media_exceptions import (
    MediaAuthenticationError,
    MediaTimeoutError,
    ReferenceImageStorageFailedError,
    ReferenceImageTooSmallError,
)
from app.services.base_image_generation import BASE_IMAGE_FILENAME
from app.services.character_edit_generation import (
    CHARACTER_EDIT_STAGE,
    EDITED_IMAGE_FILENAME,
    CharacterEditGenerationService,
)
from app.services.image_normalize import (
    inspect_reference_png,
    normalize_reference_image,
)
from app.services.job_recovery import recover_interrupted_jobs
from app.services.reference_upload import (
    REFERENCE_BACKUP_FILENAME,
    REFERENCE_FILENAME,
    REFERENCE_NORMALIZE_STAGING,
    REFERENCE_UPLOAD_PARTIAL,
    reconcile_waiting_for_reference_jobs,
    reference_relative_path,
)
from app.services.status_transitions import (
    ACTIVE_PROCESSING_STATES,
    InvalidStatusTransitionError,
    is_deletable,
    transition_status,
)
from tests.conftest import set_job_status
from tests.fakes import wait_for_job_status
from tests.media_fakes import (
    FakeMediaProvider,
    install_fake_media,
    make_portrait_bytes,
    make_prompt_ready_envelope,
    mock_image_transport,
)


def _seed_base_image_ready(client: TestClient, app) -> str:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.BASE_IMAGE_READY
        job.prompt_json = make_prompt_ready_envelope()
        job.base_image_url = f"/api/jobs/{job_id}/base-image/file"
        session.commit()
    path = app.state.storage.job_directory(job_id, create=True) / BASE_IMAGE_FILENAME
    path.write_bytes(make_portrait_bytes())
    return job_id


def _upload_reference(client: TestClient, job_id: str, data: bytes, filename: str = "x.jpg"):
    return client.post(
        f"/api/jobs/{job_id}/reference-image",
        files={"file": (filename, data, "application/octet-stream")},
    )


def _upload_file(data: bytes, filename: str = "x.jpg") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _service_upload(app, job_id: str, data: bytes) -> tuple[int, str | None]:
    try:
        app.state.reference_upload.upload_reference(job_id, _upload_file(data))
        return 200, None
    except PermissionError as exc:
        return 409, str(exc)
    except Exception as exc:
        return 400, str(exc)


def _seed_reference_ready(client: TestClient, app) -> str:
    job_id = _seed_base_image_ready(client, app)
    resp = _upload_reference(client, job_id, make_portrait_bytes(width=400, height=500))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REFERENCE_READY"
    return job_id


# --- Status ---


def test_gate4_statuses_and_transitions() -> None:
    assert JobStatus.REFERENCE_READY.value == "REFERENCE_READY"
    assert JobStatus.CHARACTER_EDIT_READY.value == "CHARACTER_EDIT_READY"
    assert transition_status(JobStatus.BASE_IMAGE_READY, JobStatus.WAITING_FOR_REFERENCE)
    assert transition_status(JobStatus.WAITING_FOR_REFERENCE, JobStatus.REFERENCE_READY)
    assert transition_status(JobStatus.REFERENCE_READY, JobStatus.CHARACTER_EDITING)
    assert transition_status(JobStatus.CHARACTER_EDITING, JobStatus.CHARACTER_EDIT_READY)
    assert transition_status(JobStatus.CHARACTER_EDITING, JobStatus.FAILED)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.CHARACTER_EDITING)


def test_idle_states_not_restart_failed_and_deletable(client, session_factory, app) -> None:
    for status in (
        JobStatus.WAITING_FOR_REFERENCE,
        JobStatus.REFERENCE_READY,
        JobStatus.CHARACTER_EDIT_READY,
    ):
        assert status not in ACTIVE_PROCESSING_STATES
    assert is_deletable(JobStatus.REFERENCE_READY)
    assert is_deletable(JobStatus.CHARACTER_EDIT_READY)

    with session_factory() as session:
        jobs = [
            GenerationJob(status=JobStatus.WAITING_FOR_REFERENCE),
            GenerationJob(status=JobStatus.REFERENCE_READY),
            GenerationJob(status=JobStatus.CHARACTER_EDIT_READY),
            GenerationJob(status=JobStatus.CHARACTER_EDITING, current_stage=CHARACTER_EDIT_STAGE),
        ]
        session.add_all(jobs)
        session.commit()
        ids = [j.id for j in jobs]
    with session_factory() as session:
        assert recover_interrupted_jobs(session) == 1
        assert session.get(GenerationJob, ids[0]).status == JobStatus.WAITING_FOR_REFERENCE
        assert session.get(GenerationJob, ids[1]).status == JobStatus.REFERENCE_READY
        assert session.get(GenerationJob, ids[2]).status == JobStatus.CHARACTER_EDIT_READY
        assert session.get(GenerationJob, ids[3]).status == JobStatus.FAILED

    for status in (JobStatus.REFERENCE_READY, JobStatus.CHARACTER_EDIT_READY):
        job_id = client.post("/api/jobs").json()["id"]
        set_job_status(app.state.session_factory, job_id, status)
        assert client.delete(f"/api/jobs/{job_id}").status_code == 200


def test_prompt_or_base_failure_cannot_retry_edit(client, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.failed_stage = "prompt_generation"
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/generate-character-edit").status_code == 409

    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.failed_stage = "base_image_generation"
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/generate-character-edit").status_code == 409


def test_eligible_edit_failure_can_retry(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.failed_stage = CHARACTER_EDIT_STAGE
        session.commit()
    resp = client.post(f"/api/jobs/{job_id}/generate-character-edit")
    assert resp.status_code == 202
    wait_for_job_status(client, job_id, {"CHARACTER_EDIT_READY"})


# --- Upload ---


def test_upload_unknown_and_wrong_state(client, app) -> None:
    assert client.post(
        "/api/jobs/00000000-0000-0000-0000-000000000000/reference-image",
        files={"file": ("a.png", make_portrait_bytes(), "image/png")},
    ).status_code == 404
    job_id = client.post("/api/jobs").json()["id"]
    assert _upload_reference(client, job_id, make_portrait_bytes()).status_code == 409


def test_empty_upload_rejected(client, app) -> None:
    job_id = _seed_base_image_ready(client, app)
    resp = _upload_reference(client, job_id, b"")
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "BASE_IMAGE_READY"


def test_upload_byte_limit_streaming(client, app) -> None:
    from types import SimpleNamespace

    tiny = SimpleNamespace(
        reference_image_max_upload_bytes=100,
        reference_image_max_pixels=25_000_000,
        reference_image_min_width=256,
        reference_image_min_height=256,
    )
    app.state.reference_upload._settings = tiny
    job_id = _seed_base_image_ready(client, app)
    huge = b"x" * 1000
    resp = _upload_reference(client, job_id, huge)
    assert resp.status_code == 400
    upload_dir = app.state.storage.upload_job_directory(job_id, create=False)
    assert not (upload_dir / "reference_image.upload").exists()


def test_png_jpeg_webp_accepted_gif_bmp_tiff_rejected(client, app, tmp_path) -> None:
    job_id = _seed_base_image_ready(client, app)
    png_resp = _upload_reference(client, job_id, make_portrait_bytes(fmt="PNG"), "a.png")
    assert png_resp.status_code == 200
    job_id = _seed_base_image_ready(client, app)
    jpeg_resp = _upload_reference(
        client, job_id, make_portrait_bytes(fmt="JPEG"), "evil.exe"
    )
    assert jpeg_resp.status_code == 200
    meta = client.get(f"/api/jobs/{job_id}/reference-image").json()
    assert meta["format"] == "PNG"
    file_resp = client.get(f"/api/jobs/{job_id}/reference-image/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("image/png")
    with Image.open(io.BytesIO(file_resp.content)) as img:
        assert img.format == "PNG"

    try:
        webp = make_portrait_bytes(fmt="WEBP")
    except Exception:
        webp = None
    if webp is not None:
        job_id = _seed_base_image_ready(client, app)
        assert _upload_reference(client, job_id, webp, "a.webp").status_code == 200

    for fmt, name in (("GIF", "a.gif"), ("BMP", "a.bmp"), ("TIFF", "a.tif")):
        job_id = _seed_base_image_ready(client, app)
        bad = _upload_reference(client, job_id, make_portrait_bytes(fmt=fmt), name)
        assert bad.status_code == 400


def test_animated_truncated_too_small_too_large_rejected(client, app, tmp_path) -> None:
    job_id = _seed_base_image_ready(client, app)
    assert (
        _upload_reference(
            client, job_id, make_portrait_bytes(fmt="GIF", animated=True), "a.gif"
        ).status_code
        == 400
    )
    job_id = _seed_base_image_ready(client, app)
    trunc = make_portrait_bytes()[:40]
    assert _upload_reference(client, job_id, trunc).status_code == 400

    job_id = _seed_base_image_ready(client, app)
    small = make_portrait_bytes(width=100, height=100)
    assert _upload_reference(client, job_id, small).status_code == 400

    # Excessive pixels via normalize helper
    src = tmp_path / "big.png"
    src.write_bytes(make_portrait_bytes(width=300, height=300))
    with pytest.raises((ReferenceImageTooSmallError, Exception)):
        # force max_pixels tiny
        normalize_reference_image(
            src, tmp_path / "out.png", max_pixels=10, min_width=1, min_height=1
        )


def test_exif_orientation_applied_and_metadata_stripped(tmp_path: Path) -> None:
    img = Image.new("RGB", (300, 500), color=(10, 20, 30))
    exif = img.getexif()
    exif[ExifBase.Orientation] = 6
    src = tmp_path / "oriented.jpg"
    img.save(src, format="JPEG", exif=exif)
    out = tmp_path / "out.png"
    info = normalize_reference_image(
        src, out, max_pixels=25_000_000, min_width=256, min_height=256
    )
    assert info.format == "PNG"
    with Image.open(out) as published:
        assert published.format == "PNG"
        # Orientation 6 swaps dimensions after transpose.
        assert published.size == (500, 300)
        assert ExifBase.Orientation not in published.getexif()


def test_failed_replacement_preserves_prior_reference(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    before = client.get(f"/api/jobs/{job_id}/reference-image").json()
    path = app.state.storage.resolve_safe(
        client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    )
    prior_bytes = path.read_bytes()
    resp = _upload_reference(client, job_id, b"not-an-image")
    assert resp.status_code == 400
    after_job = client.get(f"/api/jobs/{job_id}").json()
    assert after_job["status"] == "REFERENCE_READY"
    assert path.read_bytes() == prior_bytes
    assert client.get(f"/api/jobs/{job_id}/reference-image").json()["size_bytes"] == before[
        "size_bytes"
    ]


def test_successful_replacement_swaps_file(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    path = app.state.storage.resolve_safe(
        client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    )
    prior = path.read_bytes()
    new_bytes = make_portrait_bytes(width=320, height=480, fmt="JPEG")
    assert _upload_reference(client, job_id, new_bytes).status_code == 200
    assert path.read_bytes() != prior
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "REFERENCE_READY"
    upload_dir = app.state.storage.upload_job_directory(job_id, create=False)
    assert not (upload_dir / REFERENCE_BACKUP_FILENAME).exists()
    assert not (upload_dir / REFERENCE_UPLOAD_PARTIAL).exists()
    assert not (upload_dir / REFERENCE_NORMALIZE_STAGING).exists()


def test_commit_failure_after_publish_restores_prior_reference(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    path = app.state.storage.resolve_safe(
        client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    )
    prior_bytes = path.read_bytes()
    prior_path = client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]

    def fail_commit() -> None:
        raise ReferenceImageStorageFailedError()

    app.state.reference_upload._before_db_commit_hook = fail_commit
    resp = _upload_reference(
        client, job_id, make_portrait_bytes(width=350, height=450, fmt="JPEG")
    )
    assert resp.status_code == 400
    assert path.read_bytes() == prior_bytes
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "REFERENCE_READY"
    assert job["reference_image_path"] == prior_path
    upload_dir = app.state.storage.upload_job_directory(job_id, create=False)
    assert not (upload_dir / REFERENCE_UPLOAD_PARTIAL).exists()
    assert not (upload_dir / REFERENCE_NORMALIZE_STAGING).exists()
    assert not (upload_dir / REFERENCE_BACKUP_FILENAME).exists()
    assert client.get(f"/api/jobs/{job_id}/reference-image").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/reference-image/file").status_code == 200


def test_initial_upload_commit_failure_leaves_no_reference(client, app) -> None:
    job_id = _seed_base_image_ready(client, app)

    def fail_commit() -> None:
        raise ReferenceImageStorageFailedError()

    app.state.reference_upload._before_db_commit_hook = fail_commit
    resp = _upload_reference(client, job_id, make_portrait_bytes(width=400, height=500))
    assert resp.status_code == 400
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "BASE_IMAGE_READY"
    assert job["reference_image_path"] is None
    upload_dir = app.state.storage.upload_job_directory(job_id, create=False)
    assert not (upload_dir / REFERENCE_FILENAME).exists()
    assert not (upload_dir / REFERENCE_UPLOAD_PARTIAL).exists()
    assert not (upload_dir / REFERENCE_NORMALIZE_STAGING).exists()
    assert not (upload_dir / REFERENCE_BACKUP_FILENAME).exists()


def test_concurrent_upload_claim_one_winner(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    path = app.state.storage.resolve_safe(
        client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    )
    prior = path.read_bytes()
    claim_started = threading.Event()
    release_upload = threading.Event()
    results: list[int] = []
    payloads = [
        make_portrait_bytes(width=300, height=400, fmt="JPEG"),
        make_portrait_bytes(width=350, height=450, fmt="JPEG"),
    ]

    def after_claim() -> None:
        claim_started.set()
        release_upload.wait(timeout=5)

    app.state.reference_upload._after_claim_hook = after_claim
    app.state.reference_upload._claim_barrier = threading.Barrier(2)

    def attempt(data: bytes) -> None:
        status, _ = _service_upload(app, job_id, data)
        results.append(status)

    t1 = threading.Thread(target=attempt, args=(payloads[0],))
    t2 = threading.Thread(target=attempt, args=(payloads[1],))
    t1.start()
    t2.start()
    claim_started.wait(timeout=5)
    deadline = time.time() + 5
    while time.time() < deadline and len(results) < 2:
        if len(results) == 1 and results[0] == 409:
            break
        time.sleep(0.01)
    assert 409 in results
    release_upload.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert sorted(results) == [200, 409]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "REFERENCE_READY"
    with Image.open(path) as img:
        assert img.format == "PNG"
        assert img.size in {(300, 400), (350, 450)}
    assert path.read_bytes() != prior
    upload_dir = app.state.storage.upload_job_directory(job_id, create=False)
    assert not (upload_dir / REFERENCE_UPLOAD_PARTIAL).exists()
    assert not (upload_dir / REFERENCE_NORMALIZE_STAGING).exists()
    assert not (upload_dir / REFERENCE_BACKUP_FILENAME).exists()


def test_edit_rejected_during_reserved_upload(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    reserved = threading.Event()
    continue_upload = threading.Event()

    def after_claim() -> None:
        reserved.set()
        continue_upload.wait(timeout=5)

    app.state.reference_upload._after_claim_hook = after_claim
    upload_result: dict[str, int] = {}

    def do_upload() -> None:
        status, _ = _service_upload(
            app, job_id, make_portrait_bytes(width=380, height=520, fmt="JPEG")
        )
        upload_result["status"] = status

    thread = threading.Thread(target=do_upload)
    thread.start()
    reserved.wait(timeout=5)
    edit_resp = client.post(f"/api/jobs/{job_id}/generate-character-edit")
    assert edit_resp.status_code == 409
    continue_upload.set()
    thread.join(timeout=10)
    assert upload_result["status"] == 200
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "REFERENCE_READY"
    edit_after = client.post(f"/api/jobs/{job_id}/generate-character-edit")
    assert edit_after.status_code == 202


def test_upload_rejected_during_character_editing(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    path = app.state.storage.resolve_safe(
        client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    )
    prior_bytes = path.read_bytes()
    app.state.character_edit_generation.accept_generation(job_id)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "CHARACTER_EDITING"
    resp = _upload_reference(client, job_id, make_portrait_bytes(width=360, height=480))
    assert resp.status_code == 409
    assert path.read_bytes() == prior_bytes


def test_restart_reconcile_reference_ready_when_valid_file(client, app, session_factory) -> None:
    job_id = _seed_reference_ready(client, app)
    set_job_status(session_factory, job_id, JobStatus.WAITING_FOR_REFERENCE)
    with session_factory() as session:
        count = reconcile_waiting_for_reference_jobs(
            session, app.state.storage, app.state.settings
        )
    assert count == 1
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "REFERENCE_READY"


def test_restart_reconcile_base_image_when_no_valid_reference(client, app, session_factory) -> None:
    job_id = _seed_base_image_ready(client, app)
    set_job_status(session_factory, job_id, JobStatus.WAITING_FOR_REFERENCE)
    with session_factory() as session:
        count = reconcile_waiting_for_reference_jobs(
            session, app.state.storage, app.state.settings
        )
    assert count == 1
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "BASE_IMAGE_READY"
    assert job["reference_image_path"] is None


def test_atomic_claim_from_base_image_ready(client, app) -> None:
    job_id = _seed_base_image_ready(client, app)
    resp = _upload_reference(client, job_id, make_portrait_bytes(width=400, height=500))
    assert resp.status_code == 200
    assert resp.json()["status"] == "REFERENCE_READY"


def test_atomic_replacement_claim_from_reference_ready(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    resp = _upload_reference(
        client, job_id, make_portrait_bytes(width=360, height=480, fmt="JPEG")
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "REFERENCE_READY"


def test_reference_endpoints_missing_renamed_500(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    path = app.state.storage.resolve_safe(
        client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    )
    path.unlink()
    assert client.get(f"/api/jobs/{job_id}/reference-image").status_code == 500
    assert client.get(f"/api/jobs/{job_id}/reference-image/file").status_code == 500

    job_id = _seed_reference_ready(client, app)
    path = app.state.storage.resolve_safe(
        client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    )
    path.write_bytes(make_portrait_bytes(fmt="JPEG"))
    assert client.get(f"/api/jobs/{job_id}/reference-image").status_code == 500
    assert client.get(f"/api/jobs/{job_id}/reference-image/file").status_code == 500

    job_id = _seed_base_image_ready(client, app)
    assert client.get(f"/api/jobs/{job_id}/reference-image").status_code == 409


# --- Character edit ---


def test_edit_accepted_concurrent_and_success(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    llm_calls_before = len(getattr(app.state.llm, "calls", []))
    resp = client.post(f"/api/jobs/{job_id}/generate-character-edit")
    assert resp.status_code == 202
    assert resp.json()["status"] == "CHARACTER_EDITING"
    body = wait_for_job_status(client, job_id, {"CHARACTER_EDIT_READY"})
    assert body["edited_image_url"] == f"/api/jobs/{job_id}/edited-image/file"
    assert len(getattr(app.state.llm, "calls", [])) == llm_calls_before
    assert len(app.state.wavespeed.upload_calls) == 2
    assert len(app.state.wavespeed.calls) == 1
    call = app.state.wavespeed.calls[0]
    assert call["model"] == "openai/gpt-image-2/edit"
    assert call["input"]["aspect_ratio"] == "9:16"
    assert call["input"]["resolution"] == "1k"
    assert call["input"]["quality"] == "medium"
    assert call["input"]["output_format"] == "png"
    assert call["input"]["enable_sync_mode"] is False
    assert call["input"]["enable_base64_output"] is False
    assert len(call["input"]["images"]) == 2
    assert len(app.state.wavespeed.upload_calls) == 2
    base_path = str(
        app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
    )
    ref_path = str(
        app.state.storage.upload_job_directory(job_id, create=False) / REFERENCE_FILENAME
    )
    assert app.state.wavespeed.upload_calls[0] == base_path
    assert app.state.wavespeed.upload_calls[1] == ref_path
    assert call["input"]["images"][0].endswith("/1.png")
    assert call["input"]["images"][1].endswith("/2.png")

    meta = client.get(f"/api/jobs/{job_id}/edited-image")
    assert meta.status_code == 200
    file_resp = client.get(f"/api/jobs/{job_id}/edited-image/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("image/png")
    with Image.open(io.BytesIO(file_resp.content)) as img:
        assert img.format == "PNG"


def test_concurrent_edit_claim_enqueues_once(client, app) -> None:
    service: CharacterEditGenerationService = app.state.character_edit_generation
    submitted: list[str] = []
    lock = threading.Lock()

    def fake_submit(fn, job_id):
        with lock:
            submitted.append(job_id)
        fut = MagicMock()
        return fut

    app.state.task_runner.submit = fake_submit  # type: ignore[method-assign]
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt(job_id: str) -> None:
        barrier.wait(timeout=5)
        try:
            service.accept_generation(job_id)
            results.append("ok")
        except PermissionError:
            results.append("conflict")
        except Exception as exc:
            results.append(type(exc).__name__)

    for _ in range(4):
        results.clear()
        submitted.clear()
        job_id = _seed_reference_ready(client, app)
        t1 = threading.Thread(target=attempt, args=(job_id,))
        t2 = threading.Thread(target=attempt, args=(job_id,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert sorted(results) == ["conflict", "ok"]
        assert submitted == [job_id]


def test_edit_provider_failures_and_no_secret_leak(client, app, caplog) -> None:
    job_id = _seed_reference_ready(client, app)
    fake = FakeMediaProvider(raise_exc=MediaTimeoutError())
    install_fake_media(app, fake)
    client.post(f"/api/jobs/{job_id}/generate-character-edit")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "MEDIA_TIMEOUT"
    assert body["failed_stage"] == CHARACTER_EDIT_STAGE
    assert body["edited_image_url"] is None
    assert body["prompt_json"]
    assert (app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME).exists()
    assert (
        app.state.storage.upload_job_directory(job_id, create=False) / REFERENCE_FILENAME
    ).exists()

    job_id2 = _seed_reference_ready(client, app)
    fake2 = FakeMediaProvider(
        raise_exc=MediaAuthenticationError("Authorization Bearer sk-fake-SECRET-key")
    )
    install_fake_media(app, fake2)
    with caplog.at_level(logging.ERROR):
        client.post(f"/api/jobs/{job_id2}/generate-character-edit")
        body2 = wait_for_job_status(client, job_id2, {"FAILED"})
    assert body2["error_code"] == "MEDIA_AUTHENTICATION_FAILED"
    assert "sk-fake-SECRET-key" not in body2.get("error_message", "")
    assert "sk-fake-SECRET-key" not in caplog.text


def test_edit_wrong_aspect_and_task_submission_failure(client, app) -> None:
    from app.services.image_download import ImageDownloader

    job_id = _seed_reference_ready(client, app)
    landscape = make_portrait_bytes(width=1024, height=576)
    downloader = ImageDownloader(
        timeout_seconds=30,
        max_bytes=25 * 1024 * 1024,
        transport=mock_image_transport(body=landscape),
    )
    app.state.character_edit_generation._downloader = downloader
    client.post(f"/api/jobs/{job_id}/generate-character-edit")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "EDIT_IMAGE_INVALID_ASPECT_RATIO"
    assert body["edited_image_url"] is None

    job_id2 = _seed_reference_ready(client, app)
    app.state.task_runner.submit = MagicMock(side_effect=RuntimeError("pool dead"))
    resp = client.post(f"/api/jobs/{job_id2}/generate-character-edit")
    assert resp.status_code == 500
    body2 = client.get(f"/api/jobs/{job_id2}").json()
    assert body2["status"] == "FAILED"
    assert body2["error_code"] == "TASK_SUBMISSION_FAILED"


def test_edited_ready_inconsistent_file_500(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    set_job_status(app.state.session_factory, job_id, JobStatus.CHARACTER_EDIT_READY)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.edited_image_url = f"/api/jobs/{job_id}/edited-image/file"
        session.commit()
    assert client.get(f"/api/jobs/{job_id}/edited-image").status_code == 500
    path = app.state.storage.job_directory(job_id, create=True) / EDITED_IMAGE_FILENAME
    path.write_bytes(make_portrait_bytes(fmt="JPEG"))
    assert client.get(f"/api/jobs/{job_id}/edited-image/file").status_code == 500


def test_no_video_model_in_edit(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    client.post(f"/api/jobs/{job_id}/generate-character-edit")
    wait_for_job_status(client, job_id, {"CHARACTER_EDIT_READY"})
    for call in app.state.wavespeed.calls:
        assert "video" not in call["model"]
        assert call["model"] == "openai/gpt-image-2/edit"


def test_reference_path_under_storage_root(client, app) -> None:
    job_id = _seed_reference_ready(client, app)
    rel = client.get(f"/api/jobs/{job_id}").json()["reference_image_path"]
    assert rel == reference_relative_path(job_id)
    path = app.state.storage.resolve_safe(rel)
    assert app.state.storage.is_under_root(path)
    inspect_reference_png(path, max_pixels=25_000_000)
