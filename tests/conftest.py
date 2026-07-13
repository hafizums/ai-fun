"""Shared pytest fixtures with isolated DB and storage."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, clear_settings_cache
from app.main import create_app
from app.models.job import GenerationJob, JobStatus
from app.services.image_download import ImageDownloader
from tests.fakes import FakeLLMProvider, install_fake_llm
from tests.media_fakes import FakeMediaProvider, install_fake_media, make_portrait_bytes


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Isolated settings pointing at temporary DB and storage."""
    clear_settings_cache()
    db_path = tmp_path / "test.db"
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-local-key-not-real")
    monkeypatch.setenv("WAVESPEED_API_BASE_URL", "https://api.wavespeed.ai")
    monkeypatch.setenv("WAVESPEED_LLM_BASE_URL", "https://llm.wavespeed.ai/v1")
    monkeypatch.setenv("WAVESPEED_LLM_MODEL", "openai/gpt-5.1")
    monkeypatch.setenv("WAVESPEED_LLM_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv(
        "WAVESPEED_BASE_IMAGE_MODEL", "openai/gpt-image-2/text-to-image"
    )
    monkeypatch.setenv("WAVESPEED_BASE_IMAGE_ASPECT_RATIO", "9:16")
    monkeypatch.setenv("WAVESPEED_BASE_IMAGE_RESOLUTION", "1k")
    monkeypatch.setenv("WAVESPEED_BASE_IMAGE_QUALITY", "medium")
    monkeypatch.setenv("WAVESPEED_BASE_IMAGE_OUTPUT_FORMAT", "png")
    monkeypatch.setenv("WAVESPEED_MEDIA_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("WAVESPEED_MEDIA_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("BASE_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("BASE_IMAGE_MAX_DOWNLOAD_MB", "25")
    monkeypatch.setenv("BASE_IMAGE_MAX_PIXELS", "25000000")
    monkeypatch.setenv(
        "WAVESPEED_CHARACTER_EDIT_MODEL", "openai/gpt-image-2/edit"
    )
    monkeypatch.setenv("WAVESPEED_CHARACTER_EDIT_ASPECT_RATIO", "9:16")
    monkeypatch.setenv("WAVESPEED_CHARACTER_EDIT_RESOLUTION", "1k")
    monkeypatch.setenv("WAVESPEED_CHARACTER_EDIT_QUALITY", "medium")
    monkeypatch.setenv("WAVESPEED_CHARACTER_EDIT_OUTPUT_FORMAT", "png")
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_UPLOAD_MB", "15")
    monkeypatch.setenv("REFERENCE_IMAGE_MAX_PIXELS", "25000000")
    monkeypatch.setenv("REFERENCE_IMAGE_MIN_WIDTH", "256")
    monkeypatch.setenv("REFERENCE_IMAGE_MIN_HEIGHT", "256")
    monkeypatch.setenv(
        "WAVESPEED_SOURCE_VIDEO_MODEL",
        "wavespeed-ai/wan-2.2/i2v-480p-ultra-fast",
    )
    monkeypatch.setenv("WAVESPEED_SOURCE_VIDEO_DURATION_SECONDS", "5")
    monkeypatch.setenv("WAVESPEED_SOURCE_VIDEO_SEED", "-1")
    monkeypatch.setenv("SOURCE_VIDEO_DOWNLOAD_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("SOURCE_VIDEO_MAX_DOWNLOAD_MB", "100")
    monkeypatch.setenv("SOURCE_VIDEO_MAX_DURATION_SECONDS", "7")
    monkeypatch.setenv("SOURCE_VIDEO_MIN_DURATION_SECONDS", "4")
    monkeypatch.setenv("SOURCE_VIDEO_DURATION_TOLERANCE_SECONDS", "0.35")
    monkeypatch.setenv("SOURCE_VIDEO_MIN_WIDTH", "240")
    monkeypatch.setenv("SOURCE_VIDEO_MIN_HEIGHT", "400")
    monkeypatch.setenv("SOURCE_VIDEO_MAX_PIXELS", "5000000")
    monkeypatch.setenv("SOURCE_VIDEO_MAX_FPS", "60")
    monkeypatch.setenv(
        "WAVESPEED_CONTROL_VIDEO_MODEL",
        "wavespeed-ai/wan-2.2/fun-control",
    )
    monkeypatch.setenv("WAVESPEED_CONTROL_VIDEO_DURATION_SECONDS", "5")
    monkeypatch.setenv("WAVESPEED_CONTROL_VIDEO_RESOLUTION", "480p")
    monkeypatch.setenv("WAVESPEED_CONTROL_VIDEO_SEED", "-1")
    monkeypatch.setenv("CONTROL_VIDEO_DOWNLOAD_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("CONTROL_VIDEO_MAX_DOWNLOAD_MB", "150")
    monkeypatch.setenv("CONTROL_VIDEO_MAX_DURATION_SECONDS", "7")
    monkeypatch.setenv("CONTROL_VIDEO_MIN_DURATION_SECONDS", "4")
    monkeypatch.setenv("CONTROL_VIDEO_DURATION_TOLERANCE_SECONDS", "0.35")
    monkeypatch.setenv("CONTROL_VIDEO_MIN_WIDTH", "240")
    monkeypatch.setenv("CONTROL_VIDEO_MIN_HEIGHT", "400")
    monkeypatch.setenv("CONTROL_VIDEO_MAX_PIXELS", "5000000")
    monkeypatch.setenv("CONTROL_VIDEO_MAX_FPS", "60")
    monkeypatch.setenv("LOCAL_TASK_WORKERS", "1")
    monkeypatch.setenv("FFMPEG_BINARY", "ffmpeg")
    monkeypatch.setenv("FFPROBE_BINARY", "ffprobe")
    settings = Settings()
    yield settings
    clear_settings_cache()


@pytest.fixture
def app(tmp_env: Settings):
    application = create_app(settings=tmp_env)
    install_fake_llm(application, FakeLLMProvider())
    fake_media = FakeMediaProvider()
    install_fake_media(application, fake_media)
    # Default downloader returns a valid portrait PNG for the fake HTTPS URL.
    portrait = make_portrait_bytes()
    from tests.media_fakes import mock_image_transport

    downloader = ImageDownloader(
        timeout_seconds=tmp_env.base_image_download_timeout_seconds,
        max_bytes=tmp_env.base_image_max_download_bytes,
        transport=mock_image_transport(body=portrait),
    )
    application.state.image_downloader = downloader
    application.state.base_image_generation._downloader = downloader
    application.state.character_edit_generation._downloader = downloader
    return application


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_factory(app):
    return app.state.session_factory


@pytest.fixture
def fake_llm(app) -> FakeLLMProvider:
    return app.state.llm


@pytest.fixture
def fake_media(app) -> FakeMediaProvider:
    return app.state.wavespeed


def set_job_status(session_factory, job_id: str, status: JobStatus) -> GenerationJob:
    """Directly set job status for test setup (bypasses transition rules)."""
    with session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = status
        session.commit()
        session.refresh(job)
        session.expunge(job)
        return job
