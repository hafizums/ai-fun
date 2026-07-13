"""Gate 3 status-machine and recovery tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.jobs import apply_status_transition
from app.models.job import GenerationJob, JobStatus
from app.services.job_recovery import recover_interrupted_jobs
from app.services.status_transitions import (
    ACTIVE_PROCESSING_STATES,
    InvalidStatusTransitionError,
    is_deletable,
    transition_status,
)
from tests.conftest import set_job_status
from tests.media_fakes import make_prompt_ready_envelope


def test_prompt_ready_to_base_image_generating_allowed() -> None:
    assert transition_status(JobStatus.PROMPT_READY, JobStatus.BASE_IMAGE_GENERATING)


def test_base_image_generating_to_ready_allowed() -> None:
    assert transition_status(JobStatus.BASE_IMAGE_GENERATING, JobStatus.BASE_IMAGE_READY)


def test_generic_failed_to_base_image_generating_rejected() -> None:
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.BASE_IMAGE_GENERATING)
    job = GenerationJob(status=JobStatus.FAILED, failed_stage="base_image_generation")
    with pytest.raises(InvalidStatusTransitionError):
        apply_status_transition(job, JobStatus.BASE_IMAGE_GENERATING)


def test_base_image_ready_idle_and_deletable(client: TestClient, app) -> None:
    assert JobStatus.BASE_IMAGE_READY not in ACTIVE_PROCESSING_STATES
    assert is_deletable(JobStatus.BASE_IMAGE_READY)
    job_id = client.post("/api/jobs").json()["id"]
    set_job_status(app.state.session_factory, job_id, JobStatus.BASE_IMAGE_READY)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 200


def test_base_image_generating_recovered_on_restart(client, session_factory) -> None:
    with session_factory() as session:
        job = GenerationJob(
            status=JobStatus.BASE_IMAGE_GENERATING,
            current_stage="base_image_generation",
            prompt_json=make_prompt_ready_envelope(),
        )
        session.add(job)
        session.commit()
        job_id = job.id
        prompt = job.prompt_json
    with session_factory() as session:
        assert recover_interrupted_jobs(session) == 1
        job = session.get(GenerationJob, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error_code == "APP_RESTARTED"
        assert job.prompt_json == prompt
