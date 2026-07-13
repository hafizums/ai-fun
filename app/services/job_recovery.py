"""Interrupted-job recovery on application startup."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.job import GenerationJob, JobStatus, utc_now
from app.services.status_transitions import ACTIVE_PROCESSING_STATES, assert_can_transition

logger = logging.getLogger(__name__)

RECOVERY_ERROR_CODE = "APP_RESTARTED"
RECOVERY_ERROR_MESSAGE = (
    "Local processing was interrupted because the application restarted. "
    "In-process tasks are not persisted across restarts. Artifact paths and "
    "URLs created before the interruption were preserved."
)


def recover_interrupted_jobs(session: Session) -> int:
    """Mark active processing jobs as FAILED after an application restart.

    Preserves artifact paths/URLs. Does not modify DRAFT, COMPLETED, or FAILED jobs.
    Also leaves idle waiting states (BASE_IMAGE_READY, WAITING_FOR_REFERENCE,
    REFERENCE_READY, CHARACTER_EDIT_READY) untouched.
    """
    jobs = (
        session.query(GenerationJob)
        .filter(GenerationJob.status.in_(tuple(ACTIVE_PROCESSING_STATES)))
        .all()
    )
    recovered = 0
    for job in jobs:
        assert_can_transition(job.status, JobStatus.FAILED)
        previous = job.status.value
        job.failed_stage = job.current_stage or previous
        job.status = JobStatus.FAILED
        job.error_code = RECOVERY_ERROR_CODE
        job.error_message = RECOVERY_ERROR_MESSAGE
        job.updated_at = utc_now()
        recovered += 1
        logger.warning(
            "Recovered interrupted job_id=%s previous_status=%s",
            job.id,
            previous,
        )
    if recovered:
        session.commit()
    return recovered
