"""Job CRUD API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func

from app.models.job import GenerationJob, JobStatus, utc_now
from app.schemas.job import DeleteResponse, JobListResponse, JobResponse
from app.services.status_transitions import is_deletable
from app.services.storage import StoragePathError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100


@router.post("", response_model=JobResponse, status_code=201)
def create_job(request: Request) -> JobResponse:
    """Create a generation job in DRAFT status."""
    with request.app.state.session_factory() as session:
        job = GenerationJob(status=JobStatus.DRAFT, progress_percent=0)
        session.add(job)
        session.commit()
        session.refresh(job)
        logger.info("Created job_id=%s status=%s", job.id, job.status.value)
        return JobResponse.model_validate(job)


@router.get("", response_model=JobListResponse)
def list_jobs(
    request: Request,
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    """List jobs newest first with simple pagination."""
    with request.app.state.session_factory() as session:
        total = session.query(func.count(GenerationJob.id)).scalar() or 0
        jobs = (
            session.query(GenerationJob)
            .order_by(GenerationJob.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return JobListResponse(
            items=[JobResponse.model_validate(j) for j in jobs],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, request: Request) -> JobResponse:
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobResponse.model_validate(job)


@router.delete("/{job_id}", response_model=DeleteResponse)
def delete_job(job_id: str, request: Request) -> DeleteResponse:
    """Delete DRAFT / COMPLETED / FAILED jobs and their local files."""
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if not is_deletable(job.status):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot delete job in active status {job.status.value}. "
                    "Only DRAFT, COMPLETED, or FAILED jobs may be deleted."
                ),
            )
        try:
            storage.delete_job_files(job.id)
        except StoragePathError as exc:
            logger.error("Storage delete refused for job_id=%s: %s", job.id, exc)
            raise HTTPException(
                status_code=500,
                detail="Refused unsafe storage deletion",
            ) from exc

        session.delete(job)
        session.commit()
        logger.info("Deleted job_id=%s", job_id)
        return DeleteResponse(deleted=True, id=job_id)


def apply_status_transition(
    job: GenerationJob,
    target: JobStatus,
) -> GenerationJob:
    """Helper for services/tests — validates then applies a status change."""
    from app.services.status_transitions import assert_can_transition

    assert_can_transition(job.status, target)
    job.status = target
    job.updated_at = utc_now()
    return job
