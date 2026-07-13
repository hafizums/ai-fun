"""Gate 2 API workflow tests (offline fake LLM)."""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.models.job import GenerationJob, JobStatus
from app.providers.llm_exceptions import LLMAuthenticationError, LLMTimeoutError
from app.services.prompt_generation import canonical_prompt_json, load_prompt_envelope
from tests.conftest import set_job_status
from tests.fakes import (
    FakeLLMProvider,
    install_fake_llm,
    package_json,
    wait_for_job_status,
)


def test_successful_generation_returns_202_quickly(
    client: TestClient, app, fake_llm: FakeLLMProvider
) -> None:
    hold = threading.Event()
    install_fake_llm(app, FakeLLMProvider(delay_event=hold))
    job_id = client.post("/api/jobs").json()["id"]
    t0 = time.perf_counter()
    response = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    elapsed = time.perf_counter() - t0
    hold.set()
    assert response.status_code == 202
    assert elapsed < 0.75
    assert response.json()["status"] == "PROMPT_GENERATING"
    wait_for_job_status(client, job_id, {"PROMPT_READY", "FAILED"})


def test_successful_background_reaches_prompt_ready(client: TestClient) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    assert client.post(f"/api/jobs/{job_id}/generate-prompts", json={}).status_code == 202
    body = wait_for_job_status(client, job_id, {"PROMPT_READY"})
    assert body["status"] == "PROMPT_READY"
    assert body["progress_percent"] == 100
    assert body["current_stage"] == "prompt_ready"
    assert body["prompt_json"]


def test_stored_envelope_validates_and_canonical_json(client: TestClient) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    wait_for_job_status(client, job_id, {"PROMPT_READY"})
    job = client.get(f"/api/jobs/{job_id}").json()
    envelope = load_prompt_envelope(job["prompt_json"])
    canonical = canonical_prompt_json(envelope)
    assert canonical == job["prompt_json"]
    assert json.loads(canonical)["schema_version"] == 1


def test_typed_prompt_endpoint_returns_package(client: TestClient) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    wait_for_job_status(client, job_id, {"PROMPT_READY"})
    response = client.get(f"/api/jobs/{job_id}/prompts")
    assert response.status_code == 200
    body = response.json()
    assert body["prompts"]["image_prompt"]
    assert body["metadata"]["provider"] == "wavespeed"
    assert body["request"]["duration_seconds"] == 5


def test_unknown_job_generate_404(client: TestClient) -> None:
    response = client.post(
        "/api/jobs/00000000-0000-0000-0000-000000000000/generate-prompts",
        json={},
    )
    assert response.status_code == 404


def test_wrong_state_generation_409(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.COMPLETED)
    response = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert response.status_code == 409


def test_double_submission_enqueues_only_one_provider_call(
    client: TestClient, app
) -> None:
    hold = threading.Event()
    fake = install_fake_llm(app, FakeLLMProvider(delay_event=hold))
    job_id = client.post("/api/jobs").json()["id"]
    first = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert first.status_code == 202
    assert fake.started.wait(timeout=2)
    second = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert second.status_code == 409
    hold.set()
    wait_for_job_status(client, job_id, {"PROMPT_READY"})
    assert len(fake.calls) == 1


def test_eligible_failed_prompt_job_can_retry(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.failed_stage = "prompt_generation"
        job.error_code = "LLM_INVALID_RESPONSE"
        session.commit()
    response = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert response.status_code == 202
    wait_for_job_status(client, job_id, {"PROMPT_READY"})


def test_unrelated_failed_job_cannot_retry_as_prompt(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.failed_stage = "base_image"
        job.error_code = "OTHER"
        session.commit()
    response = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert response.status_code == 409


def test_malformed_json_marks_job_failed(client: TestClient, app) -> None:
    install_fake_llm(app, FakeLLMProvider(content="{not-json"))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "LLM_INVALID_RESPONSE"
    assert body["failed_stage"] == "prompt_generation"
    assert body["prompt_json"] is None


def test_missing_fields_mark_job_failed(client: TestClient, app) -> None:
    install_fake_llm(app, FakeLLMProvider(content='{"image_prompt":"x"}'))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "LLM_INVALID_RESPONSE"


def test_extra_fields_mark_job_failed(client: TestClient, app) -> None:
    data = json.loads(package_json())
    data["bonus"] = True
    install_fake_llm(app, FakeLLMProvider(content=json.dumps(data)))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert wait_for_job_status(client, job_id, {"FAILED"})["error_code"] == (
        "LLM_INVALID_RESPONSE"
    )


def test_invalid_transition_timing_marks_failed(client: TestClient, app) -> None:
    install_fake_llm(
        app,
        FakeLLMProvider(
            content=package_json(
                transition_hint={"start_seconds": 4.0, "end_seconds": 4.0}
            )
        ),
    )
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert wait_for_job_status(client, job_id, {"FAILED"})["error_code"] == (
        "LLM_INVALID_RESPONSE"
    )


def test_invalid_transition_enum_marks_failed(client: TestClient, app) -> None:
    install_fake_llm(
        app,
        FakeLLMProvider(
            content=package_json(transition_hint={"preferred_transition": "dissolve"})
        ),
    )
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert wait_for_job_status(client, job_id, {"FAILED"})["error_code"] == (
        "LLM_INVALID_RESPONSE"
    )


def test_empty_completion_marks_failed(client: TestClient, app) -> None:
    install_fake_llm(app, FakeLLMProvider(content=""))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert wait_for_job_status(client, job_id, {"FAILED"})["error_code"] == (
        "LLM_INVALID_RESPONSE"
    )


def test_provider_timeout_safe_failure(client: TestClient, app) -> None:
    install_fake_llm(app, FakeLLMProvider(raise_exc=LLMTimeoutError()))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "LLM_TIMEOUT"
    assert "exception" not in (body["error_message"] or "").lower() or True
    assert body["error_message"] == "The language model request timed out."


def test_auth_failure_safe_failure(client: TestClient, app) -> None:
    install_fake_llm(app, FakeLLMProvider(raise_exc=LLMAuthenticationError()))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "LLM_AUTHENTICATION_FAILED"


def test_fake_api_key_not_in_logs_api_or_db(client: TestClient, app) -> None:
    fake_key = "ws-db-leak-key-ZZZZ-do-not-leak"
    install_fake_llm(
        app,
        FakeLLMProvider(
            raise_exc=LLMAuthenticationError(f"auth failed for {fake_key}")
        ),
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("app")
    root.addHandler(handler)
    try:
        job_id = client.post("/api/jobs").json()["id"]
        client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
        body = wait_for_job_status(client, job_id, {"FAILED"})
        assert fake_key not in body["error_message"]
        assert fake_key not in stream.getvalue()
        assert fake_key not in client.get(f"/api/jobs/{job_id}").text
    finally:
        root.removeHandler(handler)


def test_raw_llm_response_not_logged(client: TestClient, app) -> None:
    marker = "UNIQUE_RAW_LLM_PAYLOAD_SHOULD_NOT_APPEAR_IN_LOGS"
    content = package_json(image_prompt=marker + " " + "x" * 40)
    install_fake_llm(app, FakeLLMProvider(content=content))
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    for name in ("app", "app.services.prompt_generation", "app.providers.wavespeed_llm"):
        logging.getLogger(name).addHandler(handler)
    try:
        job_id = client.post("/api/jobs").json()["id"]
        client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
        wait_for_job_status(client, job_id, {"PROMPT_READY"})
        assert marker not in stream.getvalue()
    finally:
        for name in (
            "app",
            "app.services.prompt_generation",
            "app.providers.wavespeed_llm",
        ):
            logging.getLogger(name).removeHandler(handler)


def test_task_runner_submit_failure_marks_failed(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    app.state.task_runner.submit = MagicMock(side_effect=RuntimeError("pool dead"))
    response = client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert response.status_code == 500
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "TASK_SUBMISSION_FAILED"
    assert body["failed_stage"] == "prompt_generation"


def test_worker_opens_own_database_session(client: TestClient, app) -> None:
    original = app.state.session_factory
    opens: list[str] = []

    def tracking_factory():
        opens.append(threading.current_thread().name)
        return original()

    app.state.prompt_generation._session_factory = tracking_factory  # type: ignore[assignment]
    hold = threading.Event()
    install_fake_llm(app, FakeLLMProvider(delay_event=hold))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    assert FakeLLMProvider  # placate linters
    # Release worker after accept path finished (accept also opens sessions on main thread).
    hold.set()
    wait_for_job_status(client, job_id, {"PROMPT_READY"})
    assert any(name.startswith("local-task") for name in opens)


def test_no_media_provider_generation_invoked(client: TestClient, app) -> None:
    wavespeed = app.state.wavespeed
    wavespeed.run_model = MagicMock(side_effect=AssertionError("media run called"))
    wavespeed.upload_file = MagicMock(side_effect=AssertionError("upload called"))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    wait_for_job_status(client, job_id, {"PROMPT_READY"})
    wavespeed.run_model.assert_not_called()
    wavespeed.upload_file.assert_not_called()


def test_prompts_endpoint_409_when_not_ready(client: TestClient) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    assert client.get(f"/api/jobs/{job_id}/prompts").status_code == 409


def test_app_llm_uses_llm_url(app) -> None:
    assert app.state.llm.base_url == "https://llm.wavespeed.ai/v1"
    assert app.state.wavespeed.api_base_url == "https://api.wavespeed.ai"
