"""Gate 5 source-video generation tests (offline, no paid network)."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models.job import GenerationJob, JobStatus
from app.providers.media_exceptions import (
    MediaAuthenticationError,
    MediaTimeoutError,
    SourceVideoDownloadError,
    SourceVideoInvalidDimensionsError,
    SourceVideoInvalidDurationError,
    SourceVideoInvalidFileError,
    SourceVideoInvalidFrameRateError,
    SourceVideoTooLargeError,
)
from app.services.base_image_generation import BASE_IMAGE_FILENAME
from app.services.character_edit_generation import EDITED_IMAGE_FILENAME
from app.services.image_download import SecureArtifactDownloader, redact_url_for_log
from app.services.job_recovery import recover_interrupted_jobs
from app.services.source_video_generation import (
    SOURCE_VIDEO_FILENAME,
    SOURCE_VIDEO_PARTIAL,
    SOURCE_VIDEO_SOURCE,
    SOURCE_VIDEO_STAGE,
    SourceVideoGenerationService,
)
from app.services.status_transitions import (
    ACTIVE_PROCESSING_STATES,
    InvalidStatusTransitionError,
    is_deletable,
    transition_status,
)
from app.services.video_normalize import normalize_source_video
from app.services.video_probe import VideoMetadata, validate_source_video_probe
from tests.conftest import set_job_status
from tests.fakes import wait_for_job_status
from tests.media_fakes import (
    FakeMediaProvider,
    install_fake_media,
    install_source_video_downloader,
    make_portrait_bytes,
    make_portrait_mp4_bytes,
    make_prompt_ready_envelope,
    mock_image_transport,
)

VERIFIED_MODEL = "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast"


@pytest.fixture(scope="module")
def portrait_mp4_bytes() -> bytes:
    return make_portrait_mp4_bytes()


def _seed_character_edit_ready(client: TestClient, app) -> str:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.CHARACTER_EDIT_READY
        job.prompt_json = make_prompt_ready_envelope()
        job.base_image_url = f"/api/jobs/{job_id}/base-image/file"
        job.edited_image_url = f"/api/jobs/{job_id}/edited-image/file"
        job.current_stage = "character_edit_ready"
        job.progress_percent = 100
        session.commit()
    job_dir = app.state.storage.job_directory(job_id, create=True)
    (job_dir / BASE_IMAGE_FILENAME).write_bytes(
        make_portrait_bytes(width=576, height=1024)
    )
    (job_dir / EDITED_IMAGE_FILENAME).write_bytes(
        make_portrait_bytes(width=720, height=1280)
    )
    return job_id


def _prepare_success(app, portrait_mp4_bytes: bytes) -> FakeMediaProvider:
    fake = FakeMediaProvider(
        outputs=["https://cdn.example.invalid/source.mp4?token=secret"],
        model=VERIFIED_MODEL,
    )
    install_fake_media(app, fake)
    install_source_video_downloader(app, portrait_mp4_bytes)
    return fake


def test_source_video_status_and_transitions() -> None:
    assert JobStatus.SOURCE_VIDEO_READY.value == "SOURCE_VIDEO_READY"
    assert transition_status(
        JobStatus.CHARACTER_EDIT_READY, JobStatus.SOURCE_VIDEO_GENERATING
    )
    assert transition_status(
        JobStatus.SOURCE_VIDEO_GENERATING, JobStatus.SOURCE_VIDEO_READY
    )
    assert transition_status(JobStatus.SOURCE_VIDEO_GENERATING, JobStatus.FAILED)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.SOURCE_VIDEO_GENERATING)
    assert JobStatus.SOURCE_VIDEO_READY not in ACTIVE_PROCESSING_STATES
    assert JobStatus.SOURCE_VIDEO_GENERATING in ACTIVE_PROCESSING_STATES
    assert is_deletable(JobStatus.SOURCE_VIDEO_READY)


def test_source_video_ready_idle_deletable_and_restart(client, app, session_factory) -> None:
    with session_factory() as session:
        ready = GenerationJob(status=JobStatus.SOURCE_VIDEO_READY)
        generating = GenerationJob(
            status=JobStatus.SOURCE_VIDEO_GENERATING,
            current_stage=SOURCE_VIDEO_STAGE,
        )
        session.add_all([ready, generating])
        session.commit()
        ready_id, gen_id = ready.id, generating.id
    with session_factory() as session:
        assert recover_interrupted_jobs(session) == 1
        assert session.get(GenerationJob, ready_id).status == JobStatus.SOURCE_VIDEO_READY
        assert session.get(GenerationJob, gen_id).status == JobStatus.FAILED

    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.SOURCE_VIDEO_READY)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200


def test_ineligible_failures_cannot_retry_source(client, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    for stage in (
        "prompt_generation",
        "base_image_generation",
        "character_editing",
    ):
        with app.state.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            job.status = JobStatus.FAILED
            job.failed_stage = stage
            session.commit()
        assert client.post(f"/api/jobs/{job_id}/generate-source-video").status_code == 409


def test_eligible_source_failure_retries(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_character_edit_ready(client, app)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.failed_stage = SOURCE_VIDEO_STAGE
        session.commit()
    _prepare_success(app, portrait_mp4_bytes)
    resp = client.post(f"/api/jobs/{job_id}/generate-source-video")
    assert resp.status_code == 202
    wait_for_job_status(client, job_id, {"SOURCE_VIDEO_READY"})


def test_unknown_wrong_duplicate_claim(client, app, portrait_mp4_bytes) -> None:
    assert (
        client.post(
            "/api/jobs/00000000-0000-0000-0000-000000000000/generate-source-video"
        ).status_code
        == 404
    )
    job_id = client.post("/api/jobs").json()["id"]
    assert client.post(f"/api/jobs/{job_id}/generate-source-video").status_code == 409

    job_id = _seed_character_edit_ready(client, app)
    _prepare_success(app, portrait_mp4_bytes)
    assert client.post(f"/api/jobs/{job_id}/generate-source-video").status_code == 202
    assert client.post(f"/api/jobs/{job_id}/generate-source-video").status_code == 409
    wait_for_job_status(client, job_id, {"SOURCE_VIDEO_READY", "FAILED"})


def test_concurrent_source_claim_one_winner(client, app, portrait_mp4_bytes) -> None:
    service: SourceVideoGenerationService = app.state.source_video_generation
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
        job_id = _seed_character_edit_ready(client, app)
        t1 = threading.Thread(target=attempt, args=(job_id,))
        t2 = threading.Thread(target=attempt, args=(job_id,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert sorted(results) == ["conflict", "ok"]
        assert submitted == [job_id]


def test_task_submission_failure_marks_failed(client, app) -> None:
    job_id = _seed_character_edit_ready(client, app)

    def boom(*_a, **_k):
        raise RuntimeError("submit boom")

    app.state.task_runner.submit = boom  # type: ignore[method-assign]
    resp = client.post(f"/api/jobs/{job_id}/generate-source-video")
    assert resp.status_code == 500
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["failed_stage"] == SOURCE_VIDEO_STAGE
    assert body["error_code"] == "TASK_SUBMISSION_FAILED"
    assert (app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME).exists()
    assert (app.state.storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME).exists()


def test_worker_skips_wrong_state(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_character_edit_ready(client, app)
    fake = _prepare_success(app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.CHARACTER_EDIT_READY
        session.commit()
    app.state.source_video_generation.run_generation_task(job_id)
    assert fake.calls == []
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "CHARACTER_EDIT_READY"


def test_commit_failure_removes_uncommitted_final(
    client, app, portrait_mp4_bytes, monkeypatch
) -> None:
    job_id = _seed_character_edit_ready(client, app)
    _prepare_success(app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.SOURCE_VIDEO_GENERATING
        job.current_stage = SOURCE_VIDEO_STAGE
        session.commit()

    from sqlalchemy.orm import Session

    real_commit = Session.commit

    def failing_commit(self, *a, **k):
        for obj in list(self.identity_map.values()):
            if (
                isinstance(obj, GenerationJob)
                and obj.status == JobStatus.SOURCE_VIDEO_READY
            ):
                raise RuntimeError("forced commit failure")
        return real_commit(self, *a, **k)

    monkeypatch.setattr(Session, "commit", failing_commit)
    app.state.source_video_generation.run_generation_task(job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["source_video_url"] is None
    final = app.state.storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
    assert not final.exists()


def test_success_uses_base_not_edited_and_verified_schema(
    client, app, portrait_mp4_bytes, caplog
) -> None:
    job_id = _seed_character_edit_ready(client, app)
    base_path = str(
        app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
    )
    edited_path = str(
        app.state.storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
    )
    llm_before = len(getattr(app.state.llm, "calls", []))
    fake = _prepare_success(app, portrait_mp4_bytes)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(f"/api/jobs/{job_id}/generate-source-video")
    assert resp.status_code == 202
    assert resp.json()["status"] == "SOURCE_VIDEO_GENERATING"
    assert resp.json()["progress_percent"] == 20
    body = wait_for_job_status(client, job_id, {"SOURCE_VIDEO_READY"})
    assert body["source_video_url"] == f"/api/jobs/{job_id}/source-video/file"
    assert len(getattr(app.state.llm, "calls", [])) == llm_before
    assert len(fake.upload_calls) == 1
    assert fake.upload_calls[0] == base_path
    assert edited_path not in fake.upload_calls
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == VERIFIED_MODEL
    assert set(call["input"].keys()) == {
        "image",
        "prompt",
        "negative_prompt",
        "duration",
        "seed",
    }
    assert call["input"]["duration"] == 5
    assert call["input"]["seed"] == -1
    assert "edited" not in call["input"]["image"].lower()
    assert call["input"]["image"].startswith("https://cdn.example.invalid/upload/")
    assert "Static camera" in call["input"]["prompt"]
    assert "camera motion" in call["input"]["negative_prompt"]
    log_text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("app.")
    )
    assert call["input"]["prompt"] not in log_text
    assert call["input"]["negative_prompt"] not in log_text
    assert "token=secret" not in log_text

    meta = client.get(f"/api/jobs/{job_id}/source-video")
    assert meta.status_code == 200
    meta_body = meta.json()
    assert meta_body["width"] == 480
    assert meta_body["height"] == 854
    assert abs(meta_body["duration_seconds"] - 5.0) <= 0.35
    assert meta_body["container"] == "mp4"
    file_resp = client.get(f"/api/jobs/{job_id}/source-video/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("video/mp4")
    assert "private" in file_resp.headers.get("cache-control", "")
    job_dir = app.state.storage.job_directory(job_id, create=False)
    assert not (job_dir / SOURCE_VIDEO_PARTIAL).exists()
    assert not (job_dir / SOURCE_VIDEO_SOURCE).exists()
    assert (job_dir / SOURCE_VIDEO_FILENAME).is_file()
    assert (job_dir / BASE_IMAGE_FILENAME).is_file()
    assert (job_dir / EDITED_IMAGE_FILENAME).is_file()


def test_corrupt_prompt_and_missing_images_fail_before_provider(
    client, app, portrait_mp4_bytes
) -> None:
    job_id = _seed_character_edit_ready(client, app)
    fake = _prepare_success(app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.SOURCE_VIDEO_GENERATING
        job.prompt_json = '{"schema_version":1}'
        session.commit()
    app.state.source_video_generation.run_generation_task(job_id)
    assert fake.calls == []
    assert client.get(f"/api/jobs/{job_id}").json()["error_code"] == "PROMPT_PACKAGE_CORRUPTED"

    job_id2 = _seed_character_edit_ready(client, app)
    fake2 = _prepare_success(app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id2, create=False) / BASE_IMAGE_FILENAME
    ).unlink()
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id2)
        job.status = JobStatus.SOURCE_VIDEO_GENERATING
        session.commit()
    app.state.source_video_generation.run_generation_task(job_id2)
    assert fake2.calls == []
    assert (
        client.get(f"/api/jobs/{job_id2}").json()["error_code"]
        == "BASE_IMAGE_MISSING_OR_INVALID"
    )

    job_id3 = _seed_character_edit_ready(client, app)
    fake3 = _prepare_success(app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id3, create=False) / EDITED_IMAGE_FILENAME
    ).unlink()
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id3)
        job.status = JobStatus.SOURCE_VIDEO_GENERATING
        session.commit()
    app.state.source_video_generation.run_generation_task(job_id3)
    assert fake3.calls == []
    assert (
        client.get(f"/api/jobs/{job_id3}").json()["error_code"]
        == "EDITED_IMAGE_MISSING_OR_INVALID"
    )

    job_id4 = _seed_character_edit_ready(client, app)
    fake4 = _prepare_success(app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id4, create=False) / BASE_IMAGE_FILENAME
    ).write_bytes(b"not-a-png")
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id4)
        job.status = JobStatus.SOURCE_VIDEO_GENERATING
        session.commit()
    app.state.source_video_generation.run_generation_task(job_id4)
    assert fake4.calls == []
    assert (
        client.get(f"/api/jobs/{job_id4}").json()["error_code"]
        == "BASE_IMAGE_MISSING_OR_INVALID"
    )


def test_provider_failures_safe_and_no_secret_leak(
    client, app, portrait_mp4_bytes, caplog
) -> None:
    job_id = _seed_character_edit_ready(client, app)
    install_fake_media(app, FakeMediaProvider(raise_exc=MediaTimeoutError()))
    install_source_video_downloader(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id}/generate-source-video")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "MEDIA_TIMEOUT"
    assert body["failed_stage"] == SOURCE_VIDEO_STAGE
    assert body["source_video_url"] is None

    job_id2 = _seed_character_edit_ready(client, app)
    install_fake_media(
        app,
        FakeMediaProvider(
            raise_exc=MediaAuthenticationError("Authorization Bearer sk-fake-SECRET-key")
        ),
    )
    with caplog.at_level(logging.ERROR):
        client.post(f"/api/jobs/{job_id2}/generate-source-video")
        body2 = wait_for_job_status(client, job_id2, {"FAILED"})
    assert body2["error_code"] == "MEDIA_AUTHENTICATION_FAILED"
    assert "sk-fake-SECRET-key" not in body2["error_message"]
    assert "sk-fake-SECRET-key" not in "\n".join(r.getMessage() for r in caplog.records)

    job_id3 = _seed_character_edit_ready(client, app)
    install_fake_media(app, FakeMediaProvider(outputs=[]))
    install_source_video_downloader(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id3}/generate-source-video")
    body3 = wait_for_job_status(client, job_id3, {"FAILED"})
    assert body3["error_code"] == "MEDIA_INVALID_RESULT"

    job_id4 = _seed_character_edit_ready(client, app)
    install_fake_media(
        app, FakeMediaProvider(outputs=["http://insecure.example.invalid/x.mp4"])
    )
    install_source_video_downloader(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id4}/generate-source-video")
    body4 = wait_for_job_status(client, job_id4, {"FAILED"})
    assert body4["error_code"] in {"MEDIA_INVALID_RESULT", "SOURCE_VIDEO_DOWNLOAD_FAILED"}


def test_secure_download_rules(tmp_path, caplog) -> None:
    dest = tmp_path / "v.download"
    good = SecureArtifactDownloader(
        timeout_seconds=5,
        max_bytes=1000,
        download_error_cls=SourceVideoDownloadError,
        too_large_error_cls=SourceVideoTooLargeError,
        transport=mock_image_transport(body=b"abcdef", content_type="video/mp4"),
    )
    assert good.download("https://cdn.example.invalid/a.mp4", dest) == 6
    assert dest.read_bytes() == b"abcdef"

    with pytest.raises(SourceVideoDownloadError):
        good.download("http://cdn.example.invalid/a.mp4", dest)

    redirect_http = SecureArtifactDownloader(
        timeout_seconds=5,
        max_bytes=1000,
        download_error_cls=SourceVideoDownloadError,
        too_large_error_cls=SourceVideoTooLargeError,
        transport=mock_image_transport(
            body=b"x", redirect_to="http://evil.example.invalid/x"
        ),
    )
    with pytest.raises(SourceVideoDownloadError):
        redirect_http.download("https://cdn.example.invalid/a.mp4", dest)
    assert not dest.exists()

    hops = {"n": 0}

    def multi_redirect(request: httpx.Request) -> httpx.Response:
        hops["n"] += 1
        if hops["n"] <= 5:
            return httpx.Response(
                302, headers={"location": f"https://cdn.example.invalid/r{hops['n']}"}
            )
        return httpx.Response(200, content=b"done")

    many = SecureArtifactDownloader(
        timeout_seconds=5,
        max_bytes=1000,
        download_error_cls=SourceVideoDownloadError,
        too_large_error_cls=SourceVideoTooLargeError,
        transport=httpx.MockTransport(multi_redirect),
    )
    with pytest.raises(SourceVideoDownloadError):
        many.download("https://cdn.example.invalid/start", dest)

    oversized = SecureArtifactDownloader(
        timeout_seconds=5,
        max_bytes=4,
        download_error_cls=SourceVideoDownloadError,
        too_large_error_cls=SourceVideoTooLargeError,
        transport=mock_image_transport(body=b"12345"),
    )
    with pytest.raises(SourceVideoTooLargeError):
        oversized.download("https://cdn.example.invalid/big", dest)
    assert not dest.exists()

    empty = SecureArtifactDownloader(
        timeout_seconds=5,
        max_bytes=1000,
        download_error_cls=SourceVideoDownloadError,
        too_large_error_cls=SourceVideoTooLargeError,
        transport=mock_image_transport(body=b""),
    )
    with pytest.raises(SourceVideoDownloadError):
        empty.download("https://cdn.example.invalid/empty", dest)
    assert not dest.exists()

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    net = SecureArtifactDownloader(
        timeout_seconds=5,
        max_bytes=1000,
        download_error_cls=SourceVideoDownloadError,
        too_large_error_cls=SourceVideoTooLargeError,
        transport=httpx.MockTransport(boom),
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SourceVideoDownloadError):
            net.download("https://cdn.example.invalid/x?token=SECRET", dest)
    assert not dest.exists()
    assert "token=SECRET" not in "\n".join(r.getMessage() for r in caplog.records)
    assert redact_url_for_log("https://x/y?token=SECRET") == "https://x/y"


def _probe_kwargs(**overrides):
    base = dict(
        ffprobe_binary="ffprobe",
        target_duration=5.0,
        min_duration=4.0,
        max_duration=7.0,
        duration_tolerance=0.35,
        min_width=240,
        min_height=400,
        max_pixels=5_000_000,
        max_fps=60.0,
    )
    base.update(overrides)
    return base


def test_validate_real_portrait_mp4(tmp_path, portrait_mp4_bytes) -> None:
    path = tmp_path / "ok.mp4"
    path.write_bytes(portrait_mp4_bytes)
    meta = validate_source_video_probe(path, **_probe_kwargs())
    assert meta.width == 480
    assert meta.height == 854
    assert abs(meta.duration_seconds - 5.0) <= 0.35


def test_validate_rejects_bad_dimensions_duration_fps(tmp_path, monkeypatch) -> None:
    path = tmp_path / "x.mp4"
    path.write_bytes(b"not-really")

    def landscape(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1280,
                    "height": 720,
                    "avg_frame_rate": "24/1",
                    "duration": "5.0",
                }
            ],
            "format": {"duration": "5.0", "format_name": "mp4"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", landscape)
    with pytest.raises(SourceVideoInvalidDimensionsError):
        validate_source_video_probe(path, **_probe_kwargs())

    def zero_dims(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 0,
                    "height": 0,
                    "avg_frame_rate": "24/1",
                    "duration": "5.0",
                }
            ],
            "format": {"duration": "5.0"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", zero_dims)
    with pytest.raises(SourceVideoInvalidDimensionsError):
        validate_source_video_probe(path, **_probe_kwargs())

    def huge_pixels(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 2000,
                    "height": 4000,
                    "avg_frame_rate": "24/1",
                    "duration": "5.0",
                }
            ],
            "format": {"duration": "5.0"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", huge_pixels)
    with pytest.raises(SourceVideoInvalidDimensionsError):
        validate_source_video_probe(path, **_probe_kwargs())

    def short_dur(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 480,
                    "height": 854,
                    "avg_frame_rate": "24/1",
                    "duration": "1.0",
                }
            ],
            "format": {"duration": "1.0"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", short_dur)
    with pytest.raises(SourceVideoInvalidDurationError):
        validate_source_video_probe(path, **_probe_kwargs())

    def long_dur(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 480,
                    "height": 854,
                    "avg_frame_rate": "24/1",
                    "duration": "8.0",
                }
            ],
            "format": {"duration": "8.0"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", long_dur)
    with pytest.raises(SourceVideoInvalidDurationError):
        validate_source_video_probe(path, **_probe_kwargs())

    def nan_dur(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 480,
                    "height": 854,
                    "avg_frame_rate": "24/1",
                    "duration": "nan",
                }
            ],
            "format": {},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", nan_dur)
    with pytest.raises(SourceVideoInvalidDurationError):
        validate_source_video_probe(path, **_probe_kwargs())

    def audio_only(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "format": {"duration": "5.0"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", audio_only)
    with pytest.raises(SourceVideoInvalidFileError):
        validate_source_video_probe(path, **_probe_kwargs())

    def no_video(path, *, ffprobe_binary, invalid_file_cls=None):
        return {"streams": [], "format": {"duration": "5.0"}}

    monkeypatch.setattr("app.services.video_probe.probe_video", no_video)
    with pytest.raises(SourceVideoInvalidFileError):
        validate_source_video_probe(path, **_probe_kwargs())

    def high_fps(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 480,
                    "height": 854,
                    "avg_frame_rate": "120/1",
                    "duration": "5.0",
                }
            ],
            "format": {"duration": "5.0"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", high_fps)
    with pytest.raises(SourceVideoInvalidFrameRateError):
        validate_source_video_probe(path, **_probe_kwargs())

    def bad_fps(path, *, ffprobe_binary, invalid_file_cls=None):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 480,
                    "height": 854,
                    "avg_frame_rate": "0/0",
                    "duration": "5.0",
                }
            ],
            "format": {"duration": "5.0"},
        }

    monkeypatch.setattr("app.services.video_probe.probe_video", bad_fps)
    with pytest.raises(SourceVideoInvalidFrameRateError):
        validate_source_video_probe(path, **_probe_kwargs())


def test_corrupt_truncated_ffprobe_errors(tmp_path, monkeypatch, caplog) -> None:
    path = tmp_path / "bad.mp4"
    path.write_bytes(b"not mp4")
    with pytest.raises(SourceVideoInvalidFileError):
        validate_source_video_probe(path, **_probe_kwargs())

    trunc = tmp_path / "trunc.mp4"
    trunc.write_bytes(make_portrait_mp4_bytes()[:200])
    with pytest.raises(SourceVideoInvalidFileError):
        validate_source_video_probe(trunc, **_probe_kwargs())

    def missing_binary(name, label="ffprobe"):
        from app.services.ffmpeg import BinaryCheck

        return BinaryCheck(
            name=label,
            configured=name,
            available=False,
            resolved_path=None,
            version_line=None,
            detail="missing",
        )

    monkeypatch.setattr("app.services.video_probe.detect_binary", missing_binary)
    with pytest.raises(Exception) as excinfo:
        validate_source_video_probe(path, **_probe_kwargs())
    assert excinfo.value.code == "FFPROBE_NOT_AVAILABLE"

    def timeout_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)

    monkeypatch.setattr(
        "app.services.video_probe.detect_binary",
        lambda *a, **k: type(
            "C",
            (),
            {
                "available": True,
                "resolved_path": "ffprobe",
                "version_line": "1",
                "name": "ffprobe",
                "configured": "ffprobe",
                "detail": "ok",
            },
        )(),
    )
    monkeypatch.setattr("app.services.video_probe.subprocess.run", timeout_run)
    good = tmp_path / "g.mp4"
    good.write_bytes(b"xxxx")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SourceVideoInvalidFileError):
            validate_source_video_probe(good, **_probe_kwargs())
    assert "stderr" not in "\n".join(r.getMessage() for r in caplog.records).lower()


def test_normalize_and_atomic_publish(tmp_path, portrait_mp4_bytes) -> None:
    source = tmp_path / "source_video.source"
    final = tmp_path / "source_video.mp4"
    source.write_bytes(portrait_mp4_bytes)
    meta = normalize_source_video(
        source,
        final,
        ffmpeg_binary="ffmpeg",
        **_probe_kwargs(),
    )
    assert final.is_file()
    assert not final.with_suffix(final.suffix + ".partial").exists()
    assert isinstance(meta, VideoMetadata)
    assert meta.container == "mp4"
    assert abs(meta.duration_seconds - 5.0) <= 0.35


def test_endpoints_non_ready_and_inconsistent(client, app, portrait_mp4_bytes) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    assert client.get(f"/api/jobs/{job_id}/source-video").status_code == 409
    assert client.get(f"/api/jobs/{job_id}/source-video/file").status_code == 409

    job_id = _seed_character_edit_ready(client, app)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.SOURCE_VIDEO_READY
        job.source_video_url = f"/api/jobs/{job_id}/source-video/file"
        session.commit()
    assert client.get(f"/api/jobs/{job_id}/source-video").status_code == 500
    assert client.get(f"/api/jobs/{job_id}/source-video/file").status_code == 500

    path = app.state.storage.job_directory(job_id, create=True) / SOURCE_VIDEO_FILENAME
    path.write_bytes(b"corrupt")
    assert client.get(f"/api/jobs/{job_id}/source-video").status_code == 500


def test_failed_preserves_prior_artifacts(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_character_edit_ready(client, app)
    base = (
        app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
    ).read_bytes()
    edited = (
        app.state.storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
    ).read_bytes()
    prompt = client.get(f"/api/jobs/{job_id}").json()["prompt_json"]
    install_fake_media(app, FakeMediaProvider(raise_exc=MediaTimeoutError()))
    install_source_video_downloader(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id}/generate-source-video")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["prompt_json"] == prompt
    assert (
        app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
    ).read_bytes() == base
    assert (
        app.state.storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
    ).read_bytes() == edited
    assert body["source_video_url"] is None
    job_dir = app.state.storage.job_directory(job_id, create=False)
    assert not (job_dir / SOURCE_VIDEO_FILENAME).exists()
    assert not (job_dir / SOURCE_VIDEO_PARTIAL).exists()


def test_no_fun_control_model_invoked(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_character_edit_ready(client, app)
    fake = _prepare_success(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id}/generate-source-video")
    wait_for_job_status(client, job_id, {"SOURCE_VIDEO_READY"})
    for call in fake.calls:
        assert "fun-control" not in call["model"].lower()
        assert "fun_control" not in json.dumps(call).lower()
