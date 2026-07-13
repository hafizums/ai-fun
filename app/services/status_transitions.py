"""Centralized allowed job status transitions."""

from __future__ import annotations

from app.models.job import JobStatus

# States where an in-process local task may have been interrupted by a restart.
ACTIVE_PROCESSING_STATES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.PROMPT_GENERATING,
        JobStatus.BASE_IMAGE_GENERATING,
        JobStatus.CHARACTER_EDITING,
        JobStatus.SOURCE_VIDEO_GENERATING,
        JobStatus.CONTROL_VIDEO_GENERATING,
        JobStatus.ANALYZING_TRANSITION,
        JobStatus.MERGING,
    }
)

# Allowed transitions. Gate 2 adds PROMPT_READY and FAILED → PROMPT_GENERATING.
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.DRAFT: frozenset({JobStatus.PROMPT_GENERATING, JobStatus.FAILED}),
    JobStatus.PROMPT_GENERATING: frozenset(
        {
            JobStatus.PROMPT_READY,
            JobStatus.BASE_IMAGE_GENERATING,
            JobStatus.FAILED,
        }
    ),
    JobStatus.PROMPT_READY: frozenset(),
    JobStatus.BASE_IMAGE_GENERATING: frozenset(
        {JobStatus.BASE_IMAGE_READY, JobStatus.FAILED}
    ),
    JobStatus.BASE_IMAGE_READY: frozenset({JobStatus.FAILED}),
    JobStatus.WAITING_FOR_REFERENCE: frozenset({JobStatus.FAILED}),
    JobStatus.CHARACTER_EDITING: frozenset({JobStatus.FAILED}),
    JobStatus.SOURCE_VIDEO_GENERATING: frozenset({JobStatus.FAILED}),
    JobStatus.CONTROL_VIDEO_GENERATING: frozenset({JobStatus.FAILED}),
    JobStatus.ANALYZING_TRANSITION: frozenset({JobStatus.FAILED}),
    JobStatus.MERGING: frozenset({JobStatus.FAILED}),
    JobStatus.COMPLETED: frozenset(),
    # FAILED → PROMPT_GENERATING is only for eligible prompt-generation retries
    # (failed_stage == "prompt_generation"), enforced by the prompt service.
    JobStatus.FAILED: frozenset({JobStatus.PROMPT_GENERATING}),
}


class InvalidStatusTransitionError(ValueError):
    """Raised when a status change is not permitted."""

    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid status transition: {current.value} → {target.value}")


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    """Return True if current → target is allowed."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_can_transition(current: JobStatus, target: JobStatus) -> None:
    """Raise InvalidStatusTransitionError if the transition is not allowed."""
    if not can_transition(current, target):
        raise InvalidStatusTransitionError(current, target)


def transition_status(current: JobStatus, target: JobStatus) -> JobStatus:
    """Validate and return the target status."""
    assert_can_transition(current, target)
    return target


DELETABLE_STATUSES: frozenset[JobStatus] = frozenset(
    {
        JobStatus.DRAFT,
        JobStatus.PROMPT_READY,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
    }
)


def is_deletable(status: JobStatus) -> bool:
    return status in DELETABLE_STATUSES
