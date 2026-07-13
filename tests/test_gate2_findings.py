"""Gate 2 finding fixes: atomic claim, strict validation, corrupted prompts."""

from __future__ import annotations

import json
import math
import threading
from concurrent.futures import Future
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.job import GenerationJob, JobStatus
from app.providers.llm_exceptions import LLMInvalidResponseError
from app.schemas.prompts import PromptGenerationRequest, PromptPackage
from app.services.prompt_generation import PromptGenerationService
from app.services.prompt_json import parse_prompt_package
from tests.conftest import set_job_status
from tests.fakes import FakeLLMProvider, install_fake_llm, package_json, wait_for_job_status

PROMPT_STRING_FIELDS = (
    "image_prompt",
    "edit_prompt",
    "motion_prompt",
    "motion_negative_prompt",
)

NON_STRING_VALUES: list[Any] = [None, True, False, 1, 1.5, [], {}, ["x"]]


@pytest.mark.parametrize("field", PROMPT_STRING_FIELDS)
@pytest.mark.parametrize("bad_value", NON_STRING_VALUES)
def test_prompt_string_fields_reject_null_and_non_strings(
    field: str, bad_value: Any
) -> None:
    data = json.loads(package_json())
    data[field] = bad_value
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(json.dumps(data))


@pytest.mark.parametrize("bad_value", NON_STRING_VALUES)
def test_event_description_rejects_null_and_non_strings(bad_value: Any) -> None:
    data = json.loads(package_json())
    data["transition_hint"]["event_description"] = bad_value
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(json.dumps(data))


@pytest.mark.parametrize("field", PROMPT_STRING_FIELDS)
@pytest.mark.parametrize("bad_value", [None, 123, True, [], {}])
def test_invalid_prompt_strings_mark_job_failed(
    client: TestClient, app, field: str, bad_value: Any
) -> None:
    data = json.loads(package_json())
    data[field] = bad_value
    # json.dumps converts None to null; True/lists/objects remain non-strings.
    install_fake_llm(app, FakeLLMProvider(content=json.dumps(data)))
    job_id = client.post("/api/jobs").json()["id"]
    assert client.post(f"/api/jobs/{job_id}/generate-prompts", json={}).status_code == 202
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["status"] == "FAILED"
    assert body["error_code"] == "LLM_INVALID_RESPONSE"
    assert body["failed_stage"] == "prompt_generation"
    assert body["prompt_json"] is None


@pytest.mark.parametrize(
    "raw_number",
    ["NaN", "Infinity", "-Infinity"],
)
def test_parser_rejects_nonfinite_json_constants(raw_number: str) -> None:
    data = json.loads(package_json())
    # Inject non-standard constant via string replace (json.dumps cannot emit NaN
    # when allow_nan=False; construct manually).
    payload = json.dumps(data)
    payload = payload.replace('"start_seconds": 2.0', f'"start_seconds": {raw_number}')
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(payload)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_pydantic_rejects_nonfinite_timing(value: float) -> None:
    data = json.loads(package_json())
    data["transition_hint"]["start_seconds"] = 1.0
    data["transition_hint"]["end_seconds"] = value
    with pytest.raises(ValidationError):
        PromptPackage.model_validate(data)


@pytest.mark.parametrize("raw_number", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_timing_marks_job_failed(
    client: TestClient, app, raw_number: str
) -> None:
    data = json.loads(package_json())
    payload = json.dumps(data).replace(
        '"end_seconds": 3.0', f'"end_seconds": {raw_number}'
    )
    install_fake_llm(app, FakeLLMProvider(content=payload))
    job_id = client.post("/api/jobs").json()["id"]
    client.post(f"/api/jobs/{job_id}/generate-prompts", json={})
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "LLM_INVALID_RESPONSE"
    assert body["prompt_json"] is None


def test_prompts_409_when_not_ready(client: TestClient) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    assert client.get(f"/api/jobs/{job_id}/prompts").status_code == 409


def test_prompts_500_when_ready_missing_json(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.PROMPT_READY)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.prompt_json = None
        session.commit()
    response = client.get(f"/api/jobs/{job_id}/prompts")
    assert response.status_code == 500
    assert "missing" in response.json()["detail"].lower()


def test_prompts_500_when_ready_malformed_json(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.PROMPT_READY)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.prompt_json = "{not-valid"
        session.commit()
    response = client.get(f"/api/jobs/{job_id}/prompts")
    assert response.status_code == 500
    assert "corrupt" in response.json()["detail"].lower()
    # Never echo corrupted content.
    assert "{not-valid" not in response.text


def test_eligible_prompt_retry_still_works(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.failed_stage = "prompt_generation"
        job.error_code = "LLM_INVALID_RESPONSE"
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/generate-prompts", json={}).status_code == 202
    wait_for_job_status(client, job_id, {"PROMPT_READY"})


def test_unrelated_failed_cannot_retry_prompt(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.failed_stage = "base_image"
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/generate-prompts", json={}).status_code == 409


def test_worker_invalid_payload_marks_failed_not_stuck(client, app) -> None:
    hold = threading.Event()
    hold.set()
    fake = install_fake_llm(app, FakeLLMProvider(delay_event=hold))
    service: PromptGenerationService = app.state.prompt_generation
    with app.state.session_factory() as session:
        job = GenerationJob(status=JobStatus.DRAFT, progress_percent=0)
        session.add(job)
        session.commit()
        job_id = job.id
    # Force claim then run worker with bad payload.
    assert service._atomic_claim(job_id) is True
    service.run_generation_task(job_id, {"subject_description": 123})
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error_code == "LLM_INVALID_RESPONSE"
        assert job.failed_stage == "prompt_generation"
    assert fake.calls == []


def test_concurrent_prompt_claim_only_one_wins(client, app) -> None:
    """File-backed SQLite atomic claim under concurrent threads.

    Repeated to make the regression meaningful without flaky sleeps.
    """
    service: PromptGenerationService = app.state.prompt_generation
    original_submit = app.state.task_runner.submit

    for _round in range(8):
        hold = threading.Event()
        fake = install_fake_llm(app, FakeLLMProvider(delay_event=hold))
        submit_calls: list[Any] = []

        def tracking_submit(
            fn: Any,
            *args: Any,
            _calls: list[Any] = submit_calls,
            _original=original_submit,
            **kwargs: Any,
        ) -> Future[Any]:
            _calls.append((fn, args, kwargs))
            return _original(fn, *args, **kwargs)

        app.state.task_runner.submit = tracking_submit  # type: ignore[method-assign]

        with app.state.session_factory() as session:
            job = GenerationJob(status=JobStatus.DRAFT, progress_percent=0)
            session.add(job)
            session.commit()
            job_id = job.id

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def attempt(
            *,
            _barrier: threading.Barrier = barrier,
            _job_id: str = job_id,
            _lock: threading.Lock = lock,
            _outcomes: list[str] = outcomes,
        ) -> None:
            _barrier.wait(timeout=5)
            try:
                service.accept_generation(_job_id, PromptGenerationRequest())
                with _lock:
                    _outcomes.append("accepted")
            except PermissionError:
                with _lock:
                    _outcomes.append("conflict")
            except Exception as exc:  # pragma: no cover - unexpected
                with _lock:
                    _outcomes.append(f"error:{type(exc).__name__}")

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        hold.set()
        wait_deadline_ok = False
        for _ in range(100):
            with app.state.session_factory() as session:
                current = session.get(GenerationJob, job_id)
                assert current is not None
                if current.status in {JobStatus.PROMPT_READY, JobStatus.FAILED}:
                    wait_deadline_ok = True
                    break
            hold.set()
            threading.Event().wait(0.05)
        assert wait_deadline_ok

        assert outcomes.count("accepted") == 1, outcomes
        assert outcomes.count("conflict") == 1, outcomes
        assert len(submit_calls) == 1
        assert len(fake.calls) == 1

    app.state.task_runner.submit = original_submit  # type: ignore[method-assign]


def test_llm_timeout_setting_rejects_nonfinite(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings, clear_settings_cache

    clear_settings_cache()
    monkeypatch.setenv("WAVESPEED_LLM_TIMEOUT_SECONDS", "NaN")
    with pytest.raises(ValidationError):
        Settings()
    clear_settings_cache()
