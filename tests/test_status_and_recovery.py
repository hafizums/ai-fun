"""Status transition and recovery tests."""

from __future__ import annotations

import pytest

from app.models.job import GenerationJob, JobStatus
from app.services.job_recovery import (
    RECOVERY_ERROR_CODE,
    recover_interrupted_jobs,
)
from app.services.status_transitions import (
    InvalidStatusTransitionError,
    transition_status,
)


def test_invalid_status_transition_is_rejected() -> None:
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.DRAFT, JobStatus.COMPLETED)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.DRAFT, JobStatus.BASE_IMAGE_READY)
    with pytest.raises(InvalidStatusTransitionError):
        transition_status(JobStatus.FAILED, JobStatus.DRAFT)


def test_valid_status_transition_succeeds() -> None:
    assert transition_status(JobStatus.DRAFT, JobStatus.PROMPT_GENERATING) == (
        JobStatus.PROMPT_GENERATING
    )
    assert transition_status(JobStatus.PROMPT_GENERATING, JobStatus.BASE_IMAGE_GENERATING) == (
        JobStatus.BASE_IMAGE_GENERATING
    )
    assert transition_status(JobStatus.BASE_IMAGE_GENERATING, JobStatus.BASE_IMAGE_READY) == (
        JobStatus.BASE_IMAGE_READY
    )
    assert transition_status(JobStatus.DRAFT, JobStatus.FAILED) == JobStatus.FAILED


def test_interrupted_active_jobs_become_failed(client, session_factory) -> None:
    with session_factory() as session:
        active = GenerationJob(status=JobStatus.PROMPT_GENERATING, current_stage="prompt")
        active.base_image_url = "https://example.invalid/base.png"
        draft = GenerationJob(status=JobStatus.DRAFT)
        completed = GenerationJob(status=JobStatus.COMPLETED)
        failed = GenerationJob(status=JobStatus.FAILED, error_code="OLD")
        waiting = GenerationJob(status=JobStatus.BASE_IMAGE_READY)
        session.add_all([active, draft, completed, failed, waiting])
        session.commit()
        active_id = active.id
        draft_id = draft.id
        completed_id = completed.id
        failed_id = failed.id
        waiting_id = waiting.id
        preserved_url = active.base_image_url

    with session_factory() as session:
        count = recover_interrupted_jobs(session)
        assert count == 1

    with session_factory() as session:
        active_job = session.get(GenerationJob, active_id)
        assert active_job is not None
        assert active_job.status == JobStatus.FAILED
        assert active_job.error_code == RECOVERY_ERROR_CODE
        assert active_job.base_image_url == preserved_url
        assert "restart" in (active_job.error_message or "").lower()

        assert session.get(GenerationJob, draft_id).status == JobStatus.DRAFT
        assert session.get(GenerationJob, completed_id).status == JobStatus.COMPLETED
        assert session.get(GenerationJob, failed_id).status == JobStatus.FAILED
        assert session.get(GenerationJob, failed_id).error_code == "OLD"
        assert session.get(GenerationJob, waiting_id).status == JobStatus.BASE_IMAGE_READY


def test_draft_jobs_not_changed_by_recovery(client, session_factory) -> None:
    with session_factory() as session:
        draft = GenerationJob(status=JobStatus.DRAFT)
        session.add(draft)
        session.commit()
        draft_id = draft.id

    with session_factory() as session:
        assert recover_interrupted_jobs(session) == 0
        assert session.get(GenerationJob, draft_id).status == JobStatus.DRAFT
