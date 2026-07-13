"""Gate 7 final-video assembly tests (offline, no provider/network)."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.job import GenerationJob, JobStatus
from app.services.control_video_generation import CONTROL_VIDEO_FILENAME
from app.services.final_video_assembly import (
    FINAL_ASSEMBLY_STAGE,
    FINAL_VIDEO_ASSEMBLING,
    FINAL_VIDEO_FILENAME,
    TRANSITION_META_FILENAME,
    FinalVideoAssemblyService,
    relative_final_video_path,
)
from app.services.job_recovery import recover_interrupted_jobs
from app.services.source_video_generation import SOURCE_VIDEO_FILENAME
from app.services.status_transitions import (
    ACTIVE_PROCESSING_STATES,
    InvalidStatusTransitionError,
    is_deletable,
    transition_status,
)
from app.services.transition_detector import TransitionResult
from app.services.video_probe import probe_video, validate_final_video_probe
from tests.conftest import set_job_status
from tests.fakes import wait_for_job_status
from tests.media_fakes import FakeMediaProvider, install_fake_media, make_portrait_mp4_bytes


@pytest.fixture(scope="module")
def portrait_mp4_bytes() -> bytes:
    return make_portrait_mp4_bytes()


def _seed_control_ready(
    client: TestClient, app, source_bytes: bytes, control_bytes: bytes | None = None
) -> str:
    job_id = client.post("/api/jobs").json()["id"]
    control = control_bytes if control_bytes is not None else source_bytes
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.CONTROL_VIDEO_READY
        job.current_stage = "control_video_ready"
        job.progress_percent = 100
        job.source_video_url = f"/api/jobs/{job_id}/source-video/file"
        job.controlled_video_url = f"/api/jobs/{job_id}/controlled-video/file"
        session.commit()
    job_dir = app.state.storage.job_directory(job_id, create=True)
    (job_dir / SOURCE_VIDEO_FILENAME).write_bytes(source_bytes)
    (job_dir / CONTROL_VIDEO_FILENAME).write_bytes(control)
    return job_id


def test_final_status_and_transitions() -> None:
    assert JobStatus.FINAL_VIDEO_ASSEMBLING.value == "FINAL_VIDEO_ASSEMBLING"
    assert transition_status(
        JobStatus.CONTROL_VIDEO_READY, JobStatus.FINAL_VIDEO_ASSEMBLING
    )
    assert transition_status(JobStatus.FINAL_VIDEO_ASSEMBLING, JobStatus.COMPLETED)
    assert transition_status(JobStatus.FINAL_VIDEO_ASSEMBLING, JobStatus.FAILED)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.FINAL_VIDEO_ASSEMBLING)
    assert JobStatus.COMPLETED not in ACTIVE_PROCESSING_STATES
    assert JobStatus.FINAL_VIDEO_ASSEMBLING in ACTIVE_PROCESSING_STATES
    assert is_deletable(JobStatus.COMPLETED)
    assert not is_deletable(JobStatus.FINAL_VIDEO_ASSEMBLING)


def test_completed_deletable_and_assembly_restart_failed(
    client, app, session_factory
) -> None:
    with session_factory() as session:
        ready = GenerationJob(status=JobStatus.COMPLETED)
        assembling = GenerationJob(
            status=JobStatus.FINAL_VIDEO_ASSEMBLING,
            current_stage=FINAL_ASSEMBLY_STAGE,
        )
        session.add_all([ready, assembling])
        session.commit()
        ready_id, asm_id = ready.id, assembling.id
    with session_factory() as session:
        assert recover_interrupted_jobs(session) == 1
        assert session.get(GenerationJob, ready_id).status == JobStatus.COMPLETED
        assert session.get(GenerationJob, asm_id).status == JobStatus.FAILED
        assert session.get(GenerationJob, asm_id).failed_stage == FINAL_ASSEMBLY_STAGE

    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.COMPLETED)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200


def test_ineligible_failures_cannot_retry_assembly(client, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    for stage in (
        "prompt_generation",
        "base_image_generation",
        "character_editing",
        "source_video_generation",
        "control_video_generation",
    ):
        with app.state.session_factory() as session:
            job = session.get(GenerationJob, job_id)
            job.status = JobStatus.FAILED
            job.failed_stage = stage
            session.commit()
        assert (
            client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 409
        )


def test_eligible_assembly_failure_retries(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FAILED
        job.failed_stage = FINAL_ASSEMBLY_STAGE
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 202
    wait_for_job_status(client, job_id, {"COMPLETED"}, timeout=60.0)


def test_unknown_wrong_duplicate_claim(client, app, portrait_mp4_bytes) -> None:
    assert (
        client.post(
            "/api/jobs/00000000-0000-0000-0000-000000000000/assemble-final-video"
        ).status_code
        == 404
    )
    job_id = client.post("/api/jobs").json()["id"]
    assert client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 409

    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    assert client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 202
    assert client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 409
    wait_for_job_status(client, job_id, {"COMPLETED", "FAILED"}, timeout=60.0)


def test_concurrent_assembly_claim_one_winner(client, app, portrait_mp4_bytes) -> None:
    service: FinalVideoAssemblyService = app.state.final_video_assembly
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
            service.accept_assembly(job_id)
            results.append("ok")
        except PermissionError:
            results.append("conflict")
        except Exception as exc:
            results.append(type(exc).__name__)

    for _ in range(4):
        results.clear()
        submitted.clear()
        job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
        t1 = threading.Thread(target=attempt, args=(job_id,))
        t2 = threading.Thread(target=attempt, args=(job_id,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert sorted(results) == ["conflict", "ok"]
        assert submitted == [job_id]


def test_task_submission_failure_marks_failed(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)

    def boom(*_a, **_k):
        raise RuntimeError("submit boom")

    app.state.task_runner.submit = boom  # type: ignore[method-assign]
    resp = client.post(f"/api/jobs/{job_id}/assemble-final-video")
    assert resp.status_code == 500
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["failed_stage"] == FINAL_ASSEMBLY_STAGE
    assert body["error_code"] == "TASK_SUBMISSION_FAILED"


def test_missing_and_invalid_inputs_rejected(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
    ).unlink()
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        job.current_stage = FINAL_ASSEMBLY_STAGE
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["error_code"] == "SOURCE_VIDEO_MISSING_OR_INVALID"
    assert (
        app.state.storage.job_directory(job_id, create=False) / CONTROL_VIDEO_FILENAME
    ).is_file()

    job_id2 = _seed_control_ready(client, app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id2, create=False) / SOURCE_VIDEO_FILENAME
    ).write_bytes(b"not-a-video")
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id2)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id2)
    assert (
        client.get(f"/api/jobs/{job_id2}").json()["error_code"]
        == "SOURCE_VIDEO_MISSING_OR_INVALID"
    )

    job_id3 = _seed_control_ready(client, app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id3, create=False) / CONTROL_VIDEO_FILENAME
    ).unlink()
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id3)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id3)
    assert (
        client.get(f"/api/jobs/{job_id3}").json()["error_code"]
        == "CONTROL_VIDEO_MISSING_OR_INVALID"
    )
    assert (
        app.state.storage.job_directory(job_id3, create=False) / SOURCE_VIDEO_FILENAME
    ).is_file()

    job_id4 = _seed_control_ready(client, app, portrait_mp4_bytes)
    (
        app.state.storage.job_directory(job_id4, create=False) / CONTROL_VIDEO_FILENAME
    ).write_bytes(b"corrupt")
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id4)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id4)
    assert (
        client.get(f"/api/jobs/{job_id4}").json()["error_code"]
        == "CONTROL_VIDEO_MISSING_OR_INVALID"
    )


def test_duration_and_dimension_mismatch(client, app, portrait_mp4_bytes) -> None:
    # Both valid vs 5±0.35 target, but delta 0.4 > FINAL_VIDEO_MAX_INPUT_DURATION_DELTA.
    short = make_portrait_mp4_bytes(duration=4.8)
    long = make_portrait_mp4_bytes(duration=5.2)
    job_id = _seed_control_ready(client, app, long, short)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id)
    assert (
        client.get(f"/api/jobs/{job_id}").json()["error_code"]
        == "VIDEO_INPUT_DURATION_MISMATCH"
    )

    wide = make_portrait_mp4_bytes(width=500, height=900)
    job_id2 = _seed_control_ready(client, app, portrait_mp4_bytes, wide)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id2)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id2)
    assert (
        client.get(f"/api/jobs/{job_id2}").json()["error_code"]
        == "VIDEO_INPUT_DIMENSION_MISMATCH"
    )


def test_no_provider_or_llm_on_assembly(client, app, portrait_mp4_bytes) -> None:
    fake = FakeMediaProvider()
    install_fake_media(app, fake)
    llm_before = len(getattr(app.state.llm, "calls", []))
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    assert client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 202
    body = wait_for_job_status(client, job_id, {"COMPLETED"}, timeout=60.0)
    assert body["status"] == "COMPLETED"
    assert fake.calls == []
    assert fake.upload_calls == []
    assert len(getattr(app.state.llm, "calls", [])) == llm_before


def test_success_assembly_duration_codec_and_paths(
    client, app, portrait_mp4_bytes, monkeypatch
) -> None:
    captured: dict = {}

    real_assemble = FinalVideoAssemblyService._assemble_final

    def wrapping(self, job_id, **kwargs):
        captured["transition_seconds"] = kwargs["transition_seconds"]
        return real_assemble(self, job_id, **kwargs)

    monkeypatch.setattr(FinalVideoAssemblyService, "_assemble_final", wrapping)

    forced = TransitionResult(
        transition_seconds=2.5,
        method="motion_peak",
        confidence=0.4,
        analysis_fps=8.0,
        frames_analyzed=40,
    )
    monkeypatch.setattr(
        "app.services.final_video_assembly.detect_transition",
        lambda *a, **k: forced,
    )

    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    assert client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 202
    body = wait_for_job_status(client, job_id, {"COMPLETED"}, timeout=60.0)
    assert body["status"] == "COMPLETED"
    assert body["final_video_path"] == relative_final_video_path(job_id)
    assert not Path(body["final_video_path"]).is_absolute()
    assert body["transition_time_seconds"] == pytest.approx(2.5, abs=0.05)
    assert captured["transition_seconds"] == pytest.approx(2.5, abs=0.05)

    final_path = (
        app.state.storage.final_job_directory(job_id, create=False) / FINAL_VIDEO_FILENAME
    )
    assert final_path.is_file()
    assert not (
        app.state.storage.final_job_directory(job_id, create=False)
        / FINAL_VIDEO_ASSEMBLING
    ).exists()
    assert not app.state.storage.temporary_job_directory(job_id, create=False).exists()

    meta = validate_final_video_probe(
        final_path,
        ffprobe_binary="ffprobe",
        target_duration=5.0,
        min_duration=4.0,
        max_duration=7.0,
        duration_tolerance=0.5,
        min_width=240,
        min_height=400,
        max_pixels=5_000_000,
        max_fps=60,
        require_no_audio=True,
    )
    assert meta.has_audio is False
    assert meta.height > meta.width
    assert 4.0 < meta.duration_seconds < 8.0
    assert "h264" in meta.codec.lower() or meta.codec.lower() in {"avc1", "avc"}

    probe = probe_video(final_path, ffprobe_binary="ffprobe")
    streams = probe.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    assert audio == []

    tr = client.get(f"/api/jobs/{job_id}/transition")
    assert tr.status_code == 200
    tr_body = tr.json()
    assert tr_body["method"] == "motion_peak"
    assert tr_body["transition_seconds"] == pytest.approx(2.5, abs=0.05)

    fv = client.get(f"/api/jobs/{job_id}/final-video")
    assert fv.status_code == 200
    assert fv.json()["has_audio"] is False
    assert fv.json()["url"] == f"/api/jobs/{job_id}/final-video/file"

    file_resp = client.get(f"/api/jobs/{job_id}/final-video/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("video/mp4")


def test_ffmpeg_filter_uses_before_after_segments(
    client, app, portrait_mp4_bytes, monkeypatch
) -> None:
    import subprocess

    cmds: list[list[str]] = []
    real_run = subprocess.run

    def capture_run(cmd, **kwargs):
        cmds.append(list(cmd))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(
        "app.services.final_video_assembly.detect_transition",
        lambda *a, **k: TransitionResult(2.0, "motion_peak", 0.5, 8.0, 40),
    )
    monkeypatch.setattr(
        "app.services.final_video_assembly.subprocess.run", capture_run
    )

    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        job.current_stage = FINAL_ASSEMBLY_STAGE
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "COMPLETED"
    assemble_cmds = [c for c in cmds if "-filter_complex" in c]
    assert assemble_cmds
    joined = " ".join(assemble_cmds[-1])
    assert "trim=0:" in joined
    assert "xfade" in joined or "concat" in joined
    assert "-an" in assemble_cmds[-1]


def test_midpoint_fallback_end_to_end(
    client, app, portrait_mp4_bytes, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.services.final_video_assembly.detect_transition",
        lambda *a, **k: TransitionResult(2.5, "midpoint_fallback", 0.0, 8.0, 2),
    )
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    assert client.post(f"/api/jobs/{job_id}/assemble-final-video").status_code == 202
    body = wait_for_job_status(client, job_id, {"COMPLETED"}, timeout=60.0)
    assert body["transition_time_seconds"] == pytest.approx(2.5, abs=0.05)
    tr = client.get(f"/api/jobs/{job_id}/transition").json()
    assert tr["method"] == "midpoint_fallback"


def test_commit_failure_removes_final(
    client, app, portrait_mp4_bytes, monkeypatch
) -> None:
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        job.current_stage = FINAL_ASSEMBLY_STAGE
        session.commit()

    real_commit = Session.commit

    def failing_commit(self, *a, **k):
        for obj in list(self.identity_map.values()):
            if isinstance(obj, GenerationJob) and obj.status == JobStatus.COMPLETED:
                raise RuntimeError("forced commit failure")
        return real_commit(self, *a, **k)

    monkeypatch.setattr(Session, "commit", failing_commit)
    app.state.final_video_assembly.run_assembly_task(job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["final_video_path"] is None
    final = (
        app.state.storage.final_job_directory(job_id, create=False) / FINAL_VIDEO_FILENAME
    )
    assert not final.exists()
    assert (
        app.state.storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
    ).is_file()
    assert (
        app.state.storage.job_directory(job_id, create=False) / CONTROL_VIDEO_FILENAME
    ).is_file()


def test_partials_removed_on_failure(
    client, app, portrait_mp4_bytes, monkeypatch
) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("assemble boom")

    monkeypatch.setattr(FinalVideoAssemblyService, "_assemble_final", boom)
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.FINAL_VIDEO_ASSEMBLING
        session.commit()
    app.state.final_video_assembly.run_assembly_task(job_id)
    final_dir = app.state.storage.final_job_directory(job_id, create=False)
    assert not (final_dir / FINAL_VIDEO_FILENAME).exists()
    assert not (final_dir / FINAL_VIDEO_ASSEMBLING).exists()
    assert not app.state.storage.temporary_job_directory(job_id, create=False).exists()


def test_endpoints_non_completed_and_inconsistent(
    client, app, portrait_mp4_bytes
) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    assert client.get(f"/api/jobs/{job_id}/transition").status_code == 409
    assert client.get(f"/api/jobs/{job_id}/final-video").status_code == 409
    assert client.get(f"/api/jobs/{job_id}/final-video/file").status_code == 409

    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.COMPLETED
        job.final_video_path = relative_final_video_path(job_id)
        job.transition_time_seconds = 2.5
        session.commit()
    assert client.get(f"/api/jobs/{job_id}/final-video").status_code == 500
    assert client.get(f"/api/jobs/{job_id}/transition").status_code == 500

    final_dir = app.state.storage.final_job_directory(job_id, create=True)
    (final_dir / FINAL_VIDEO_FILENAME).write_bytes(b"corrupt")
    (final_dir / TRANSITION_META_FILENAME).write_text("{}", encoding="utf-8")
    assert client.get(f"/api/jobs/{job_id}/final-video").status_code == 500
    assert client.get(f"/api/jobs/{job_id}/transition").status_code == 500


def test_accepted_endpoint_returns_quickly(client, app, portrait_mp4_bytes) -> None:
    job_id = _seed_control_ready(client, app, portrait_mp4_bytes)
    gate = threading.Event()
    real_run = app.state.final_video_assembly.run_assembly_task

    def delayed(job_id_arg: str) -> None:
        gate.wait(timeout=10)
        return real_run(job_id_arg)

    app.state.final_video_assembly.run_assembly_task = delayed  # type: ignore[method-assign]
    resp = client.post(f"/api/jobs/{job_id}/assemble-final-video")
    assert resp.status_code == 202
    assert resp.json()["status"] == "FINAL_VIDEO_ASSEMBLING"
    gate.set()
    wait_for_job_status(client, job_id, {"COMPLETED", "FAILED"}, timeout=60.0)
