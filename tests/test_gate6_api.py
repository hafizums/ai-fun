"""Gate 6 Fun Control / controlled-video tests (offline, no paid network)."""

from __future__ import annotations

import json
import logging
import threading
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.job import GenerationJob, JobStatus
from app.providers.media_exceptions import MediaTimeoutError
from app.services.base_image_generation import BASE_IMAGE_FILENAME
from app.services.character_edit_generation import EDITED_IMAGE_FILENAME
from app.services.control_video_generation import (
    CONTROL_VIDEO_FILENAME,
    CONTROL_VIDEO_PARTIAL,
    CONTROL_VIDEO_STAGE,
    ControlVideoGenerationService,
)
from app.services.job_recovery import recover_interrupted_jobs
from app.services.source_video_generation import SOURCE_VIDEO_FILENAME
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
    install_control_video_downloader,
    install_fake_media,
    make_portrait_bytes,
    make_portrait_mp4_bytes,
    make_prompt_ready_envelope,
)

VERIFIED_MODEL = "wavespeed-ai/wan-2.2/fun-control"


@pytest.fixture(scope="module")
def portrait_mp4_bytes() -> bytes:
    return make_portrait_mp4_bytes()


def _seed_source_video_ready(client: TestClient, app, portrait_mp4_bytes: bytes) -> str:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.SOURCE_VIDEO_READY
        job.prompt_json = make_prompt_ready_envelope()
        job.base_image_url = f"/api/jobs/{job_id}/base-image/file"
        job.edited_image_url = f"/api/jobs/{job_id}/edited-image/file"
        job.source_video_url = f"/api/jobs/{job_id}/source-video/file"
        job.current_stage = "source_video_ready"
        job.progress_percent = 100
        session.commit()
    job_dir = app.state.storage.job_directory(job_id, create=True)
    (job_dir / BASE_IMAGE_FILENAME).write_bytes(
        make_portrait_bytes(width=576, height=1024)
    )
    (job_dir / EDITED_IMAGE_FILENAME).write_bytes(
        make_portrait_bytes(width=720, height=1280)
    )
    (job_dir / SOURCE_VIDEO_FILENAME).write_bytes(portrait_mp4_bytes)
    return job_id


def _prepare_success(app, portrait_mp4_bytes: bytes) -> FakeMediaProvider:
    fake = FakeMediaProvider(
        outputs=["https://cdn.example.invalid/controlled.mp4?token=secret"],
        model=VERIFIED_MODEL,
    )
    install_fake_media(app, fake)
    install_control_video_downloader(app, portrait_mp4_bytes)
    return fake


def test_control_video_status_and_transitions() -> None:
    assert JobStatus.CONTROL_VIDEO_READY.value == "CONTROL_VIDEO_READY"
    assert transition_status(
        JobStatus.SOURCE_VIDEO_READY, JobStatus.CONTROL_VIDEO_GENERATING
    )
    assert transition_status(
        JobStatus.CONTROL_VIDEO_GENERATING, JobStatus.CONTROL_VIDEO_READY
    )
    assert transition_status(JobStatus.CONTROL_VIDEO_GENERATING, JobStatus.FAILED)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.CONTROL_VIDEO_GENERATING)
    assert JobStatus.CONTROL_VIDEO_READY not in ACTIVE_PROCESSING_STATES
    assert JobStatus.CONTROL_VIDEO_GENERATING in ACTIVE_PROCESSING_STATES
    assert is_deletable(JobStatus.CONTROL_VIDEO_READY)


def test_control_ready_idle_and_restart(client, app, session_factory) -> None:
    with session_factory() as session:
        ready = GenerationJob(status=JobStatus.CONTROL_VIDEO_READY)
        generating = GenerationJob(
            status=JobStatus.CONTROL_VIDEO_GENERATING,
            current_stage=CONTROL_VIDEO_STAGE,
        )
        session.add_all([ready, generating])
        session.commit()
        ready_id, gen_id = ready.id, generating.id
    with session_factory() as session:
        assert recover_interrupted_jobs(session) == 1
        assert session.get(GenerationJob, ready_id).status == JobStatus.CONTROL_VIDEO_READY
        assert session.get(GenerationJob, gen_id).status == JobStatus.FAILED

    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.CONTROL_VIDEO_READY)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200


def test_ineligible_failures_cannot_retry_control(client, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    for stage in (
        "prompt_generation",
        "base_image_generation",
        "character_editing",
        "source_video_generation",
    ):
        with app.state.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            job.status = JobStatus.FAILED
            job.failed_stage = stage
            session.commit()
        assert (
            client.post(f"/api/jobs/{job_id}/generate-controlled-video").status_code
            == 409
        )


def test_eligible_control_failure_retries(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.failed_stage = CONTROL_VIDEO_STAGE
        session.commit()
    _prepare_success(app, portrait_mp4_bytes)
    assert client.post(f"/api/jobs/{job_id}/generate-controlled-video").status_code == 202
    wait_for_job_status(client, job_id, {"CONTROL_VIDEO_READY"})


def test_unknown_wrong_duplicate_claim(client, app, portrait_mp4_bytes) -> None:
    assert (
        client.post(
            "/api/jobs/00000000-0000-0000-0000-000000000000/generate-controlled-video"
        ).status_code
        == 404
    )
    job_id = client.post("/api/jobs").json()["id"]
    assert client.post(f"/api/jobs/{job_id}/generate-controlled-video").status_code == 409

    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    _prepare_success(app, portrait_mp4_bytes)
    assert client.post(f"/api/jobs/{job_id}/generate-controlled-video").status_code == 202
    assert client.post(f"/api/jobs/{job_id}/generate-controlled-video").status_code == 409
    wait_for_job_status(client, job_id, {"CONTROL_VIDEO_READY", "FAILED"})


def test_concurrent_control_claim_one_winner(client, app, portrait_mp4_bytes) -> None:
    service: ControlVideoGenerationService = app.state.control_video_generation
    submitted: list[str] = []
    lock = threading.Lock()

    def fake_submit(fn, job_id):
        with lock:
            submitted.append(job_id)
        return MagicMock()

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
        job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
        t1 = threading.Thread(target=attempt, args=(job_id,))
        t2 = threading.Thread(target=attempt, args=(job_id,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert sorted(results) == ["conflict", "ok"]
        assert submitted == [job_id]


def test_task_submission_failure_marks_failed(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)

    def boom(*_a, **_k):
        raise RuntimeError("submit boom")

    app.state.task_runner.submit = boom  # type: ignore[method-assign]
    resp = client.post(f"/api/jobs/{job_id}/generate-controlled-video")
    assert resp.status_code == 500
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["failed_stage"] == CONTROL_VIDEO_STAGE
    assert body["error_code"] == "TASK_SUBMISSION_FAILED"


def test_commit_failure_removes_uncommitted_final(
    client, app, portrait_mp4_bytes, monkeypatch
) -> None:
    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    _prepare_success(app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.CONTROL_VIDEO_GENERATING
        job.current_stage = CONTROL_VIDEO_STAGE
        session.commit()

    real_commit = Session.commit

    def failing_commit(self, *a, **k):
        for obj in list(self.identity_map.values()):
            if (
                isinstance(obj, GenerationJob)
                and obj.status == JobStatus.CONTROL_VIDEO_READY
            ):
                raise RuntimeError("forced commit failure")
        return real_commit(self, *a, **k)

    monkeypatch.setattr(Session, "commit", failing_commit)
    app.state.control_video_generation.run_generation_task(job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["controlled_video_url"] is None
    final = (
        app.state.storage.job_directory(job_id, create=False) / CONTROL_VIDEO_FILENAME
    )
    assert not final.exists()


def test_success_uses_edited_and_source_not_base(
    client, app, portrait_mp4_bytes, caplog
) -> None:
    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    edited_path = str(
        app.state.storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
    )
    source_path = str(
        app.state.storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
    )
    base_path = str(
        app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
    )
    llm_before = len(getattr(app.state.llm, "calls", []))
    fake = _prepare_success(app, portrait_mp4_bytes)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(f"/api/jobs/{job_id}/generate-controlled-video")
    assert resp.status_code == 202
    assert resp.json()["status"] == "CONTROL_VIDEO_GENERATING"
    body = wait_for_job_status(client, job_id, {"CONTROL_VIDEO_READY"})
    assert body["controlled_video_url"] == f"/api/jobs/{job_id}/controlled-video/file"
    assert len(getattr(app.state.llm, "calls", [])) == llm_before
    assert len(fake.upload_calls) == 2
    assert fake.upload_calls[0] == edited_path
    assert fake.upload_calls[1] == source_path
    assert base_path not in fake.upload_calls
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == VERIFIED_MODEL
    assert call["max_task_retries"] == 0
    assert set(call["input"].keys()) == {
        "image",
        "video",
        "prompt",
        "resolution",
        "seed",
    }
    assert "negative_prompt" not in call["input"]
    assert "duration" not in call["input"]
    assert call["input"]["resolution"] == "480p"
    assert call["input"]["seed"] == -1
    assert call["input"]["image"].endswith("/1.png")
    assert call["input"]["video"].endswith("/2.png")
    assert "Static camera" in call["input"]["prompt"]
    log_text = "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("app.")
    )
    assert call["input"]["prompt"] not in log_text
    assert "token=secret" not in log_text

    meta = client.get(f"/api/jobs/{job_id}/controlled-video")
    assert meta.status_code == 200
    file_resp = client.get(f"/api/jobs/{job_id}/controlled-video/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("video/mp4")
    job_dir = app.state.storage.job_directory(job_id, create=False)
    assert (job_dir / CONTROL_VIDEO_FILENAME).is_file()
    assert not (job_dir / CONTROL_VIDEO_PARTIAL).exists()
    assert (job_dir / EDITED_IMAGE_FILENAME).is_file()
    assert (job_dir / SOURCE_VIDEO_FILENAME).is_file()
    assert (job_dir / BASE_IMAGE_FILENAME).is_file()


def test_integrity_failures_before_provider(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    fake = _prepare_success(app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.CONTROL_VIDEO_GENERATING
        job.prompt_json = '{"schema_version":1}'
        session.commit()
    app.state.control_video_generation.run_generation_task(job_id)
    assert fake.calls == []
    assert client.get(f"/api/jobs/{job_id}").json()["error_code"] == "PROMPT_PACKAGE_CORRUPTED"

    job_id2 = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    fake2 = _prepare_success(app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id2, create=False) / EDITED_IMAGE_FILENAME
    ).unlink()
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id2)
        job.status = JobStatus.CONTROL_VIDEO_GENERATING
        session.commit()
    app.state.control_video_generation.run_generation_task(job_id2)
    assert fake2.calls == []
    assert (
        client.get(f"/api/jobs/{job_id2}").json()["error_code"]
        == "EDITED_IMAGE_MISSING_OR_INVALID"
    )

    job_id3 = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    fake3 = _prepare_success(app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id3, create=False) / SOURCE_VIDEO_FILENAME
    ).unlink()
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id3)
        job.status = JobStatus.CONTROL_VIDEO_GENERATING
        session.commit()
    app.state.control_video_generation.run_generation_task(job_id3)
    assert fake3.calls == []
    assert (
        client.get(f"/api/jobs/{job_id3}").json()["error_code"]
        == "SOURCE_VIDEO_MISSING_OR_INVALID"
    )


def test_provider_timeout_preserves_priors(client, app, portrait_mp4_bytes, caplog) -> None:
    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    edited = (
        app.state.storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
    ).read_bytes()
    source = (
        app.state.storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
    ).read_bytes()
    install_fake_media(
        app,
        FakeMediaProvider(raise_exc=MediaTimeoutError("timed out sk-fake-SECRET")),
    )
    install_control_video_downloader(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id}/generate-controlled-video")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "MEDIA_TIMEOUT"
    assert body["failed_stage"] == CONTROL_VIDEO_STAGE
    assert body["controlled_video_url"] is None
    assert "sk-fake-SECRET" not in (body.get("error_message") or "")
    assert (
        app.state.storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
    ).read_bytes() == edited
    assert (
        app.state.storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
    ).read_bytes() == source
    assert not (
        app.state.storage.job_directory(job_id, create=False) / CONTROL_VIDEO_FILENAME
    ).exists()


def test_endpoints_non_ready_and_inconsistent(client, app, portrait_mp4_bytes) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    assert client.get(f"/api/jobs/{job_id}/controlled-video").status_code == 409
    assert client.get(f"/api/jobs/{job_id}/controlled-video/file").status_code == 409

    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.CONTROL_VIDEO_READY
        job.controlled_video_url = f"/api/jobs/{job_id}/controlled-video/file"
        session.commit()
    assert client.get(f"/api/jobs/{job_id}/controlled-video").status_code == 500

    path = (
        app.state.storage.job_directory(job_id, create=True) / CONTROL_VIDEO_FILENAME
    )
    path.write_bytes(b"corrupt")
    assert client.get(f"/api/jobs/{job_id}/controlled-video").status_code == 500


def test_no_i2v_or_fun_control_confusion(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_source_video_ready(client, app, portrait_mp4_bytes)
    fake = _prepare_success(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id}/generate-controlled-video")
    wait_for_job_status(client, job_id, {"CONTROL_VIDEO_READY"})
    assert fake.calls[0]["model"] == VERIFIED_MODEL
    assert "i2v" not in fake.calls[0]["model"].lower()
    assert "fun-control" in json.dumps(fake.calls).lower()
