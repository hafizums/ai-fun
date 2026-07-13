"""Shared pytest fixtures with isolated DB and storage."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, clear_settings_cache
from app.main import create_app
from app.models.job import GenerationJob, JobStatus


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Isolated settings pointing at temporary DB and storage."""
    clear_settings_cache()
    db_path = tmp_path / "test.db"
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("WAVESPEED_API_KEY", "")
    monkeypatch.setenv("WAVESPEED_API_BASE_URL", "https://api.wavespeed.ai")
    monkeypatch.setenv("WAVESPEED_LLM_BASE_URL", "https://llm.wavespeed.ai/v1")
    monkeypatch.setenv("LOCAL_TASK_WORKERS", "1")
    monkeypatch.setenv("FFMPEG_BINARY", "ffmpeg")
    monkeypatch.setenv("FFPROBE_BINARY", "ffprobe")
    settings = Settings()
    yield settings
    clear_settings_cache()


@pytest.fixture
def app(tmp_env: Settings):
    return create_app(settings=tmp_env)


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_factory(app):
    return app.state.session_factory


def set_job_status(session_factory, job_id: str, status: JobStatus) -> GenerationJob:
    """Directly set job status for test setup (bypasses transition rules)."""
    with session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = status
        session.commit()
        session.refresh(job)
        # Detach attributes we need
        session.expunge(job)
        return job
