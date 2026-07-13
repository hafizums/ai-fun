"""Background prompt-generation service (Gate 2)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session, sessionmaker

from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.llm_base import LLMProvider
from app.providers.llm_exceptions import LLMError, LLMInvalidResponseError
from app.schemas.prompts import (
    PromptEnvelope,
    PromptGenerationRequest,
    PromptMetadata,
    PromptRequestSnapshot,
)
from app.services.prompt_json import parse_prompt_package
from app.services.status_transitions import assert_can_transition
from app.services.task_runner import TaskRunner

logger = logging.getLogger(__name__)

PROMPT_STAGE = "prompt_generation"
PROMPT_READY_STAGE = "prompt_ready"
INITIAL_PROGRESS = 10

TASK_SUBMISSION_FAILED = "TASK_SUBMISSION_FAILED"
TASK_SUBMISSION_MESSAGE = "Failed to submit the local background prompt-generation task."
INTERNAL_PAYLOAD_FAILED = "LLM_REQUEST_FAILED"

SAFE_ERROR_MESSAGES: dict[str, str] = {
    "LLM_NOT_CONFIGURED": "The language model provider is not configured.",
    "LLM_AUTHENTICATION_FAILED": "Language model authentication failed.",
    "LLM_TIMEOUT": "The language model request timed out.",
    "LLM_CONNECTION_FAILED": "Could not connect to the language model provider.",
    "LLM_REQUEST_FAILED": "The language model request failed.",
    "LLM_INVALID_RESPONSE": "The language model returned an invalid response.",
    TASK_SUBMISSION_FAILED: TASK_SUBMISSION_MESSAGE,
}


def canonical_prompt_json(envelope: PromptEnvelope) -> str:
    """Serialize validated envelope with application-controlled JSON."""
    payload = envelope.model_dump(mode="json")
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LLMInvalidResponseError() from exc


def is_eligible_prompt_retry(job: GenerationJob) -> bool:
    return job.status == JobStatus.FAILED and job.failed_stage == PROMPT_STAGE


class PromptGenerationService:
    """Accept and run asynchronous prompt generation for jobs."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        task_runner: TaskRunner,
        llm_provider: LLMProvider,
        llm_model: str,
    ) -> None:
        self._session_factory = session_factory
        self._task_runner = task_runner
        self._llm = llm_provider
        self._llm_model = llm_model

    def accept_generation(
        self, job_id: str, request: PromptGenerationRequest
    ) -> GenerationJob:
        """Atomically claim a job for prompt generation, then enqueue work.

        Raises:
            LookupError: job not found
            PermissionError: wrong state / not eligible (mapped to 409 by API)
            RuntimeError: task submission failed after marking FAILED
        """
        claimed = self._atomic_claim(job_id)
        if not claimed:
            with self._session_factory() as session:
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise LookupError("Job not found")
                if job.status == JobStatus.PROMPT_GENERATING:
                    raise PermissionError("Prompt generation is already in progress")
                raise PermissionError("Job is not eligible for prompt generation")

        request_payload = request.model_dump()

        try:
            self._task_runner.submit(
                self.run_generation_task,
                job_id,
                request_payload,
            )
        except Exception:
            logger.error(
                "TaskRunner.submit failed for job_id=%s exception_class=submit_failure",
                job_id,
            )
            self._mark_failed(
                job_id,
                error_code=TASK_SUBMISSION_FAILED,
                error_message=TASK_SUBMISSION_MESSAGE,
            )
            raise RuntimeError(TASK_SUBMISSION_MESSAGE) from None

        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            assert job is not None
            session.expunge(job)
            return job

    def _atomic_claim(self, job_id: str) -> bool:
        """Compare-and-set claim: DRAFT or eligible FAILED prompt jobs only.

        Returns True only when exactly one row was updated.
        """
        now = utc_now()
        stmt = (
            update(GenerationJob)
            .where(
                GenerationJob.id == job_id,
                or_(
                    GenerationJob.status == JobStatus.DRAFT,
                    and_(
                        GenerationJob.status == JobStatus.FAILED,
                        GenerationJob.failed_stage == PROMPT_STAGE,
                    ),
                ),
            )
            .values(
                status=JobStatus.PROMPT_GENERATING,
                current_stage=PROMPT_STAGE,
                progress_percent=INITIAL_PROGRESS,
                error_code=None,
                error_message=None,
                failed_stage=None,
                prompt_json=None,
                updated_at=now,
            )
        )
        with self._session_factory() as session:
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0) == 1

    def run_generation_task(self, job_id: str, request_payload: dict[str, Any]) -> None:
        """Worker entrypoint: opens its own DB session; one LLM call."""
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                logger.error("Prompt worker: job_id=%s not found", job_id)
                return
            if job.status != JobStatus.PROMPT_GENERATING:
                logger.warning(
                    "Prompt worker: job_id=%s unexpected status=%s; skipping",
                    job_id,
                    job.status.value,
                )
                return

        try:
            # Validate copied payload inside the failure boundary so unexpected
            # internal corruption cannot leave the job stuck in PROMPT_GENERATING.
            try:
                request = PromptGenerationRequest.model_validate(request_payload)
            except Exception:
                logger.error(
                    "Prompt worker payload validation failed job_id=%s "
                    "(payload withheld)",
                    job_id,
                )
                raise LLMInvalidResponseError() from None

            completion = self._llm.generate_prompt_completion(request)
            package = parse_prompt_package(completion.content)
            try:
                package.validate_timing(request.duration_seconds)
            except ValueError as exc:
                raise LLMInvalidResponseError() from exc
            envelope = PromptEnvelope(
                schema_version=1,
                request=PromptRequestSnapshot(
                    subject_description=request.subject_description,
                    scene_description=request.scene_description,
                    motion_description=request.motion_description,
                    duration_seconds=request.duration_seconds,
                ),
                prompts=package,
                metadata=PromptMetadata(
                    provider="wavespeed",
                    model=completion.model or self._llm_model,
                    response_id=completion.response_id,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                    generated_at=datetime.now(UTC),
                ),
            )
            prompt_json = canonical_prompt_json(envelope)
        except LLMError as exc:
            self._mark_failed(
                job_id,
                error_code=exc.code,
                error_message=SAFE_ERROR_MESSAGES.get(exc.code, exc.public_message),
            )
            return
        except Exception:
            logger.error(
                "Prompt worker unexpected failure job_id=%s exception_class=Unexpected",
                job_id,
            )
            self._mark_failed(
                job_id,
                error_code=INTERNAL_PAYLOAD_FAILED,
                error_message=SAFE_ERROR_MESSAGES[INTERNAL_PAYLOAD_FAILED],
            )
            return

        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if job.status != JobStatus.PROMPT_GENERATING:
                logger.warning(
                    "Prompt worker: job_id=%s left PROMPT_GENERATING before write; skipping",
                    job_id,
                )
                return
            assert_can_transition(job.status, JobStatus.PROMPT_READY)
            job.prompt_json = prompt_json
            job.status = JobStatus.PROMPT_READY
            job.current_stage = PROMPT_READY_STAGE
            job.progress_percent = 100
            job.error_code = None
            job.error_message = None
            job.failed_stage = None
            job.updated_at = utc_now()
            session.commit()
            logger.info("Prompt generation completed job_id=%s", job_id)

    def _mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None:
        with self._session_factory() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            if job.status != JobStatus.PROMPT_GENERATING:
                return
            try:
                assert_can_transition(job.status, JobStatus.FAILED)
            except Exception:
                pass
            job.status = JobStatus.FAILED
            job.failed_stage = PROMPT_STAGE
            job.error_code = error_code
            job.error_message = error_message
            job.current_stage = PROMPT_STAGE
            job.prompt_json = None
            job.updated_at = utc_now()
            session.commit()
            logger.error(
                "Prompt generation failed job_id=%s error_code=%s",
                job_id,
                error_code,
            )


def load_prompt_envelope(prompt_json: str | None) -> PromptEnvelope:
    """Load and validate a stored envelope; raise LLMInvalidResponseError if corrupt."""
    if not prompt_json:
        raise LLMInvalidResponseError()
    try:
        data = json.loads(prompt_json, parse_constant=_reject_persisted_nonfinite)
        return PromptEnvelope.model_validate(data)
    except LLMInvalidResponseError:
        raise
    except Exception as exc:
        raise LLMInvalidResponseError() from exc


def _reject_persisted_nonfinite(_value: str) -> Any:
    raise LLMInvalidResponseError()
