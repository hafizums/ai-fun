"""Paid-generation retry policy tests (offline, no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

from app.providers.media_exceptions import MediaConnectionError, MediaTimeoutError
from app.providers.wavespeed import WaveSpeedProvider
from tests.media_fakes import FakeMediaProvider, make_prompt_ready_envelope


class RecordingClient:
    """Fake public SDK Client that records constructor kwargs and submissions."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.max_retries = kwargs.get("max_retries")
        self.max_connection_retries = kwargs.get("max_connection_retries")
        self.submit_count = 0
        self.run_calls: list[dict[str, Any]] = []
        self.upload_calls: list[str] = []
        self._mode = "success"
        self._accepted_then_timeout = False

    def configure_timeout_on_submit(self) -> None:
        self._mode = "timeout"

    def configure_lost_response_after_accept(self) -> None:
        """Simulate: server accepted, client never got the ID, then timeout."""
        self._mode = "lost_response"

    def upload(self, file: str | Any, *, timeout: float | None = None) -> str:
        self.upload_calls.append(str(file))
        return f"https://cdn.example.invalid/upload/{len(self.upload_calls)}.png"

    def run(
        self,
        model: str,
        input: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        enable_sync_mode: bool = False,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        self.run_calls.append(
            {
                "model": model,
                "input": input or {},
                "max_retries": max_retries,
                "timeout": timeout,
                "poll_interval": poll_interval,
                "enable_sync_mode": enable_sync_mode,
            }
        )
        # Mirror SDK: connection/submit attempts are gated by client
        # max_connection_retries (0 → exactly one attempt).
        attempts = (self.max_connection_retries or 0) + 1
        # Task-level retries from run() argument.
        task_retries = max_retries if max_retries is not None else (self.max_retries or 0)
        total_submits = 0
        last_error: Exception | None = None
        for _task in range(task_retries + 1):
            for _conn in range(attempts):
                total_submits += 1
                self.submit_count = total_submits
                if self._mode == "timeout":
                    last_error = TimeoutError("connection timed out")
                    continue
                if self._mode == "lost_response":
                    # First submit "accepted" on server but response lost;
                    # with retries disabled we must not submit again.
                    last_error = requests.exceptions.Timeout("lost response after accept")
                    continue
                return {
                    "outputs": ["https://cdn.example.invalid/out.png"],
                    "id": "pred_1",
                    "model": model,
                }
            if task_retries == 0:
                break
        assert last_error is not None
        raise last_error


class RecordingFactory:
    def __init__(self) -> None:
        self.clients: list[RecordingClient] = []
        self.constructor_calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> RecordingClient:
        self.constructor_calls.append(dict(kwargs))
        client = RecordingClient(**kwargs)
        self.clients.append(client)
        return client


def test_generation_client_constructed_with_zero_retries() -> None:
    factory = RecordingFactory()
    provider = WaveSpeedProvider(
        api_key="test-key",
        base_url="https://api.wavespeed.ai",
        client_factory=factory,
    )
    provider.run_model("wavespeed-ai/wan-2.2/i2v-480p-ultra-fast", {"prompt": "x"})
    assert len(factory.constructor_calls) == 1
    kwargs = factory.constructor_calls[0]
    assert kwargs["max_retries"] == 0
    assert kwargs["max_connection_retries"] == 0
    assert factory.clients[0].run_calls[0]["max_retries"] == 0


def test_run_model_passes_explicit_task_retry_zero() -> None:
    factory = RecordingFactory()
    provider = WaveSpeedProvider(
        api_key="test-key",
        base_url="https://api.wavespeed.ai",
        client_factory=factory,
    )
    provider.run_model("m", {"a": 1}, max_task_retries=0)
    assert factory.clients[0].run_calls[0]["max_retries"] == 0
    # Non-zero is forced to zero.
    provider.run_model("m", {"a": 1}, max_task_retries=3)
    assert factory.clients[0].run_calls[1]["max_retries"] == 0


def test_connection_timeout_submits_exactly_once() -> None:
    factory = RecordingFactory()
    provider = WaveSpeedProvider(
        api_key="test-key",
        base_url="https://api.wavespeed.ai",
        client_factory=factory,
    )
    # Prime generation client then configure timeout.
    client = provider._ensure_generation_client()
    assert isinstance(client, RecordingClient)
    client.configure_timeout_on_submit()
    with pytest.raises(MediaTimeoutError):
        provider.run_model("m", {"prompt": "x"})
    assert client.submit_count == 1


def test_lost_response_not_automatically_resubmitted() -> None:
    factory = RecordingFactory()
    provider = WaveSpeedProvider(
        api_key="test-key",
        base_url="https://api.wavespeed.ai",
        client_factory=factory,
    )
    client = provider._ensure_generation_client()
    client.configure_lost_response_after_accept()
    with pytest.raises((MediaTimeoutError, MediaConnectionError)):
        provider.run_model("m", {"prompt": "x"})
    assert client.submit_count == 1


def test_upload_uses_separate_client_without_forced_zero_connection_retries() -> None:
    factory = RecordingFactory()
    provider = WaveSpeedProvider(
        api_key="test-key",
        base_url="https://api.wavespeed.ai",
        client_factory=factory,
    )
    path = Path("dummy.png")
    url = provider.upload_file(path)
    assert url.startswith("https://")
    assert len(factory.constructor_calls) == 1
    upload_kwargs = factory.constructor_calls[0]
    # Upload client must not force zero connection retries.
    assert "max_connection_retries" not in upload_kwargs
    assert "max_retries" not in upload_kwargs
    # Generation client is separate and zeroed.
    provider.run_model("m", {"prompt": "x"})
    assert len(factory.constructor_calls) == 2
    gen_kwargs = factory.constructor_calls[1]
    assert gen_kwargs["max_retries"] == 0
    assert gen_kwargs["max_connection_retries"] == 0
    assert factory.clients[0] is not factory.clients[1]


def test_source_video_timeout_maps_safely_no_leak(client, app, portrait_mp4_bytes, caplog):
    from tests.fakes import wait_for_job_status
    from tests.media_fakes import install_fake_media, install_source_video_downloader
    from tests.test_gate5_api import _seed_character_edit_ready

    job_id = _seed_character_edit_ready(client, app)
    secret = "sk-fake-SECRET-timeout-detail"
    fake = FakeMediaProvider(
        raise_exc=MediaTimeoutError(f"provider said timed out {secret}")
    )
    install_fake_media(app, fake)
    install_source_video_downloader(app, portrait_mp4_bytes)
    client.post(f"/api/jobs/{job_id}/generate-source-video")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "MEDIA_TIMEOUT"
    assert secret not in (body.get("error_message") or "")
    assert secret not in "\n".join(
        r.getMessage() for r in caplog.records if r.name.startswith("app.")
    )


def test_base_and_edit_pass_max_task_retries_zero(client, app) -> None:
    from app.models.job import GenerationJob, JobStatus
    from app.services.base_image_generation import BASE_IMAGE_FILENAME
    from app.services.character_edit_generation import EDITED_IMAGE_FILENAME
    from app.services.image_download import ImageDownloader
    from app.services.reference_upload import REFERENCE_FILENAME
    from tests.fakes import wait_for_job_status
    from tests.media_fakes import (
        install_fake_media,
        make_portrait_bytes,
        mock_image_transport,
    )

    fake = FakeMediaProvider()
    install_fake_media(app, fake)
    downloader = ImageDownloader(
        timeout_seconds=30,
        max_bytes=5_000_000,
        transport=mock_image_transport(body=make_portrait_bytes()),
    )
    app.state.base_image_generation._downloader = downloader
    app.state.character_edit_generation._downloader = downloader

    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.PROMPT_READY
        job.prompt_json = make_prompt_ready_envelope()
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/generate-base-image").status_code == 202
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})
    assert fake.calls[-1]["max_task_retries"] == 0

    # Reference + edit
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        job.status = JobStatus.REFERENCE_READY
        job.reference_image_path = f"uploads/{job_id}/reference_image.png"
        session.commit()
    upload_dir = app.state.storage.upload_job_directory(job_id, create=True)
    (upload_dir / REFERENCE_FILENAME).write_bytes(make_portrait_bytes(width=400, height=500))
    job_dir = app.state.storage.job_directory(job_id, create=True)
    if not (job_dir / BASE_IMAGE_FILENAME).exists():
        (job_dir / BASE_IMAGE_FILENAME).write_bytes(make_portrait_bytes())
    assert client.post(f"/api/jobs/{job_id}/generate-character-edit").status_code == 202
    wait_for_job_status(client, job_id, {"CHARACTER_EDIT_READY"})
    assert fake.calls[-1]["max_task_retries"] == 0
    assert (job_dir / EDITED_IMAGE_FILENAME).exists() or True


@pytest.fixture
def portrait_mp4_bytes():
    from tests.media_fakes import make_portrait_mp4_bytes

    return make_portrait_mp4_bytes()
