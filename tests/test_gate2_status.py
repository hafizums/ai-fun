"""Gate 2 status, recovery, SQLite compatibility, and deletion tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import Settings, clear_settings_cache
from app.main import create_app
from app.models.job import GenerationJob, JobStatus
from app.services.job_recovery import recover_interrupted_jobs
from app.services.status_transitions import (
    ACTIVE_PROCESSING_STATES,
    InvalidStatusTransitionError,
    is_deletable,
    transition_status,
)
from tests.conftest import set_job_status
from tests.fakes import FakeLLMProvider, install_fake_llm, wait_for_job_status


def test_prompt_ready_exists() -> None:
    assert JobStatus.PROMPT_READY.value == "PROMPT_READY"
    assert "PROMPT_READY" in JobStatus.__members__


def test_gate2_valid_status_transitions() -> None:
    assert transition_status(JobStatus.DRAFT, JobStatus.PROMPT_GENERATING)
    assert transition_status(JobStatus.PROMPT_GENERATING, JobStatus.PROMPT_READY)
    assert transition_status(JobStatus.PROMPT_GENERATING, JobStatus.FAILED)


def test_invalid_transitions_remain_rejected() -> None:
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.DRAFT, JobStatus.PROMPT_READY)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.PROMPT_READY, JobStatus.PROMPT_GENERATING)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.DRAFT)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.COMPLETED, JobStatus.PROMPT_GENERATING)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.PROMPT_GENERATING, JobStatus.BASE_IMAGE_GENERATING)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.PROMPT_GENERATING)


def test_apply_status_transition_cannot_retry_unrelated_failed() -> None:
    from app.api.jobs import apply_status_transition

    job = GenerationJob(status=JobStatus.FAILED, failed_stage="base_image")
    with pytest.raises(InvalidStatusTransitionError):
        apply_status_transition(job, JobStatus.PROMPT_GENERATING)


def test_prompt_ready_not_interrupted(client, session_factory) -> None:
    assert JobStatus.PROMPT_READY not in ACTIVE_PROCESSING_STATES
    with session_factory() as session:
        job = GenerationJob(status=JobStatus.PROMPT_READY, current_stage="prompt_ready")
        session.add(job)
        session.commit()
        job_id = job.id
    with session_factory() as session:
        assert recover_interrupted_jobs(session) == 0
        assert session.get(GenerationJob, job_id).status == JobStatus.PROMPT_READY


def test_prompt_ready_jobs_can_be_deleted(client: TestClient, app) -> None:
    assert is_deletable(JobStatus.PROMPT_READY)
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.PROMPT_READY)
    response = client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_gate1_compatible_sqlite_database_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup against a Gate 1 DDL database (VARCHAR status, no CHECK)."""
    db_path = tmp_path / "gate1.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    ddl = """
    CREATE TABLE generation_jobs (
        id VARCHAR(36) NOT NULL,
        status VARCHAR(24) NOT NULL,
        current_stage VARCHAR(64),
        progress_percent INTEGER NOT NULL,
        prompt_json TEXT,
        base_image_url TEXT,
        reference_image_path TEXT,
        edited_image_url TEXT,
        source_video_url TEXT,
        controlled_video_url TEXT,
        transition_time_seconds FLOAT,
        transition_score FLOAT,
        final_video_path TEXT,
        provider_prediction_ids_json TEXT,
        failed_stage VARCHAR(64),
        error_code VARCHAR(64),
        error_message TEXT,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (id)
    )
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(
            text(
                "INSERT INTO generation_jobs "
                "(id, status, progress_percent, created_at, updated_at) "
                "VALUES ('11111111-1111-1111-1111-111111111111', 'DRAFT', 0, "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
        )
    engine.dispose()

    clear_settings_cache()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-key")
    monkeypatch.setenv("WAVESPEED_API_BASE_URL", "https://api.wavespeed.ai")
    monkeypatch.setenv("WAVESPEED_LLM_BASE_URL", "https://llm.wavespeed.ai/v1")
    settings = Settings()
    application = create_app(settings=settings)
    install_fake_llm(application, FakeLLMProvider())
    with TestClient(application) as client:
        health = client.get("/health")
        assert health.status_code == 200
        listed = client.get("/api/jobs")
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1
        # New code can persist PROMPT_READY into the Gate 1 VARCHAR column.
        job_id = client.post("/api/jobs").json()["id"]
        resp = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
        assert resp.status_code == 202
        ready = wait_for_job_status(client, job_id, {"PROMPT_READY"})
        assert ready["status"] == "PROMPT_READY"
    clear_settings_cache()
