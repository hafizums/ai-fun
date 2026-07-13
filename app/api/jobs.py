"""Job CRUD, prompt-generation, base-image, reference, character-edit, and source-video APIs."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func

from app.models.job import GenerationJob, JobStatus, utc_now
from app.providers.llm_exceptions import LLMInvalidResponseError
from app.providers.media_exceptions import (
    BaseImageInvalidAspectRatioError,
    BaseImageInvalidFileError,
    EditImageInvalidAspectRatioError,
    EditImageInvalidFileError,
    MediaError,
    ReferenceImageInvalidFileError,
    SourceVideoInvalidDimensionsError,
    SourceVideoInvalidDurationError,
    SourceVideoInvalidFileError,
    SourceVideoInvalidFrameRateError,
)
from app.schemas.base_image import (
    BaseImageMetadataResponse,
    GenerateBaseImageAcceptedResponse,
)
from app.schemas.character_edit import (
    EditedImageMetadataResponse,
    GenerateCharacterEditAcceptedResponse,
    ReferenceImageMetadataResponse,
)
from app.schemas.job import DeleteResponse, JobListResponse, JobResponse
from app.schemas.prompt_api import GeneratePromptsAcceptedResponse, PromptEnvelopeResponse
from app.schemas.prompts import PromptGenerationRequest
from app.schemas.source_video import (
    GenerateSourceVideoAcceptedResponse,
    SourceVideoMetadataResponse,
)
from app.services.base_image_generation import BASE_IMAGE_FILENAME, local_base_image_url
from app.services.character_edit_generation import (
    EDITED_IMAGE_FILENAME,
    local_edited_image_url,
)
from app.services.image_normalize import (
    inspect_edited_png,
    inspect_local_png,
    inspect_reference_png,
)
from app.services.prompt_generation import load_prompt_envelope
from app.services.reference_upload import (
    SAFE_ERROR_MESSAGES as REFERENCE_SAFE_MESSAGES,
)
from app.services.reference_upload import (
    local_reference_image_url,
)
from app.services.source_video_generation import (
    SOURCE_VIDEO_FILENAME,
    local_source_video_url,
)
from app.services.status_transitions import is_deletable
from app.services.storage import StoragePathError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

REFERENCE_READY_STATUSES = frozenset(
    {
        JobStatus.REFERENCE_READY,
        JobStatus.CHARACTER_EDITING,
        JobStatus.CHARACTER_EDIT_READY,
    }
)


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


@router.post(
    "/{job_id}/generate-prompts",
    response_model=GeneratePromptsAcceptedResponse,
    status_code=202,
)
def generate_prompts(
    job_id: str,
    body: PromptGenerationRequest,
    request: Request,
) -> GeneratePromptsAcceptedResponse:
    """Accept async prompt generation (no provider call in this request)."""
    service = request.app.state.prompt_generation
    try:
        job = service.accept_generation(job_id, body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GeneratePromptsAcceptedResponse(
        id=job.id,
        status=job.status.value,
        current_stage=job.current_stage,
        progress_percent=job.progress_percent,
    )


@router.get("/{job_id}/prompts", response_model=PromptEnvelopeResponse)
def get_prompts(job_id: str, request: Request) -> PromptEnvelopeResponse:
    """Return the typed stored prompt envelope when ready."""
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.PROMPT_READY:
            raise HTTPException(
                status_code=409,
                detail="Prompts are not ready for this job",
            )
        if not job.prompt_json:
            logger.error("PROMPT_READY job_id=%s missing prompt_json", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored prompt package is missing",
            )
        try:
            envelope = load_prompt_envelope(job.prompt_json)
        except LLMInvalidResponseError as exc:
            logger.error("Corrupted prompt_json for job_id=%s", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored prompt package is corrupted",
            ) from exc
        return PromptEnvelopeResponse.model_validate(envelope.model_dump(mode="json"))


@router.post(
    "/{job_id}/generate-base-image",
    response_model=GenerateBaseImageAcceptedResponse,
    status_code=202,
)
def generate_base_image(
    job_id: str,
    request: Request,
) -> GenerateBaseImageAcceptedResponse:
    """Accept async base-image generation (no provider call in this request)."""
    service = request.app.state.base_image_generation
    try:
        job = service.accept_generation(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateBaseImageAcceptedResponse(
        id=job.id,
        status=job.status.value,
        current_stage=job.current_stage,
        progress_percent=job.progress_percent,
    )


@router.get("/{job_id}/base-image", response_model=BaseImageMetadataResponse)
def get_base_image_metadata(job_id: str, request: Request) -> BaseImageMetadataResponse:
    """Return local base-image metadata when ready."""
    settings = request.app.state.settings
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.BASE_IMAGE_READY:
            raise HTTPException(status_code=409, detail="Base image is not ready")
        path = storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
        try:
            info = inspect_local_png(path, max_pixels=settings.base_image_max_pixels)
        except (BaseImageInvalidFileError, BaseImageInvalidAspectRatioError) as exc:
            logger.error("BASE_IMAGE_READY job_id=%s local file invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored base image is missing or invalid",
            ) from exc
        return BaseImageMetadataResponse(
            job_id=job_id,
            status=job.status.value,
            url=job.base_image_url or local_base_image_url(job_id),
            width=info.width,
            height=info.height,
            format=info.format,
            size_bytes=info.size_bytes,
        )


@router.get("/{job_id}/base-image/file")
def get_base_image_file(job_id: str, request: Request) -> FileResponse:
    """Serve the local PNG base image when ready."""
    settings = request.app.state.settings
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.BASE_IMAGE_READY:
            raise HTTPException(status_code=409, detail="Base image is not ready")
        path = storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
        try:
            inspect_local_png(path, max_pixels=settings.base_image_max_pixels)
        except (BaseImageInvalidFileError, BaseImageInvalidAspectRatioError) as exc:
            logger.error("BASE_IMAGE_READY job_id=%s file serve invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored base image is missing or invalid",
            ) from exc
        return FileResponse(
            path,
            media_type="image/png",
            filename=f"base-image-{job_id}.png",
            headers={"Cache-Control": "private, max-age=3600"},
        )


UPLOAD_FILE_FIELD = File(...)


@router.post("/{job_id}/reference-image", response_model=ReferenceImageMetadataResponse)
async def upload_reference_image(
    job_id: str,
    request: Request,
    file: UploadFile = UPLOAD_FILE_FIELD,
) -> ReferenceImageMetadataResponse:
    """Upload and normalize a local reference portrait (no provider call)."""
    service = request.app.state.reference_upload
    try:
        job, info = service.upload_reference(job_id, file)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MediaError as exc:
        detail = REFERENCE_SAFE_MESSAGES.get(exc.code, exc.public_message)
        raise HTTPException(status_code=400, detail=detail) from exc

    return ReferenceImageMetadataResponse(
        job_id=job.id,
        status=job.status.value,
        url=local_reference_image_url(job.id),
        width=info.width,
        height=info.height,
        format=info.format,
        size_bytes=info.size_bytes,
    )


@router.get("/{job_id}/reference-image", response_model=ReferenceImageMetadataResponse)
def get_reference_image_metadata(
    job_id: str, request: Request
) -> ReferenceImageMetadataResponse:
    settings = request.app.state.settings
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in REFERENCE_READY_STATUSES or not job.reference_image_path:
            raise HTTPException(status_code=409, detail="Reference image is not ready")
        try:
            path = storage.resolve_safe(job.reference_image_path)
            info = inspect_reference_png(
                path, max_pixels=settings.reference_image_max_pixels
            )
        except (ReferenceImageInvalidFileError, StoragePathError) as exc:
            logger.error("Reference ready job_id=%s local file invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored reference image is missing or invalid",
            ) from exc
        return ReferenceImageMetadataResponse(
            job_id=job_id,
            status=job.status.value,
            url=local_reference_image_url(job_id),
            width=info.width,
            height=info.height,
            format=info.format,
            size_bytes=info.size_bytes,
        )


@router.get("/{job_id}/reference-image/file")
def get_reference_image_file(job_id: str, request: Request) -> FileResponse:
    settings = request.app.state.settings
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in REFERENCE_READY_STATUSES or not job.reference_image_path:
            raise HTTPException(status_code=409, detail="Reference image is not ready")
        try:
            path = storage.resolve_safe(job.reference_image_path)
            inspect_reference_png(path, max_pixels=settings.reference_image_max_pixels)
        except (ReferenceImageInvalidFileError, StoragePathError) as exc:
            logger.error("Reference ready job_id=%s file serve invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored reference image is missing or invalid",
            ) from exc
        return FileResponse(
            path,
            media_type="image/png",
            filename=f"reference-image-{job_id}.png",
            headers={"Cache-Control": "private, max-age=3600"},
        )


@router.post(
    "/{job_id}/generate-character-edit",
    response_model=GenerateCharacterEditAcceptedResponse,
    status_code=202,
)
def generate_character_edit(
    job_id: str,
    request: Request,
) -> GenerateCharacterEditAcceptedResponse:
    """Accept async character editing (no provider call in this request)."""
    service = request.app.state.character_edit_generation
    try:
        job = service.accept_generation(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateCharacterEditAcceptedResponse(
        id=job.id,
        status=job.status.value,
        current_stage=job.current_stage,
        progress_percent=job.progress_percent,
    )


@router.get("/{job_id}/edited-image", response_model=EditedImageMetadataResponse)
def get_edited_image_metadata(job_id: str, request: Request) -> EditedImageMetadataResponse:
    settings = request.app.state.settings
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.CHARACTER_EDIT_READY:
            raise HTTPException(status_code=409, detail="Edited image is not ready")
        path = storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
        try:
            info = inspect_edited_png(path, max_pixels=settings.base_image_max_pixels)
        except (EditImageInvalidFileError, EditImageInvalidAspectRatioError) as exc:
            logger.error("CHARACTER_EDIT_READY job_id=%s local file invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored edited image is missing or invalid",
            ) from exc
        return EditedImageMetadataResponse(
            job_id=job_id,
            status=job.status.value,
            url=job.edited_image_url or local_edited_image_url(job_id),
            width=info.width,
            height=info.height,
            format=info.format,
            size_bytes=info.size_bytes,
        )


@router.get("/{job_id}/edited-image/file")
def get_edited_image_file(job_id: str, request: Request) -> FileResponse:
    settings = request.app.state.settings
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.CHARACTER_EDIT_READY:
            raise HTTPException(status_code=409, detail="Edited image is not ready")
        path = storage.job_directory(job_id, create=False) / EDITED_IMAGE_FILENAME
        try:
            inspect_edited_png(path, max_pixels=settings.base_image_max_pixels)
        except (EditImageInvalidFileError, EditImageInvalidAspectRatioError) as exc:
            logger.error("CHARACTER_EDIT_READY job_id=%s file serve invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored edited image is missing or invalid",
            ) from exc
        return FileResponse(
            path,
            media_type="image/png",
            filename=f"edited-image-{job_id}.png",
            headers={"Cache-Control": "private, max-age=3600"},
        )


@router.post(
    "/{job_id}/generate-source-video",
    response_model=GenerateSourceVideoAcceptedResponse,
    status_code=202,
)
def generate_source_video(
    job_id: str,
    request: Request,
) -> GenerateSourceVideoAcceptedResponse:
    """Accept async source-video generation (no provider call in this request)."""
    service = request.app.state.source_video_generation
    try:
        job = service.accept_generation(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return GenerateSourceVideoAcceptedResponse(
        id=job.id,
        status=job.status.value,
        current_stage=job.current_stage,
        progress_percent=job.progress_percent,
    )


@router.get("/{job_id}/source-video", response_model=SourceVideoMetadataResponse)
def get_source_video_metadata(job_id: str, request: Request) -> SourceVideoMetadataResponse:
    service = request.app.state.source_video_generation
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.SOURCE_VIDEO_READY:
            raise HTTPException(status_code=409, detail="Source video is not ready")
        try:
            info = service.inspect_ready_video(job_id)
        except (
            SourceVideoInvalidFileError,
            SourceVideoInvalidDurationError,
            SourceVideoInvalidDimensionsError,
            SourceVideoInvalidFrameRateError,
            MediaError,
        ) as exc:
            logger.error("SOURCE_VIDEO_READY job_id=%s local file invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored source video is missing or invalid",
            ) from exc
        return SourceVideoMetadataResponse(
            job_id=job_id,
            status=job.status.value,
            url=job.source_video_url or local_source_video_url(job_id),
            width=info.width,
            height=info.height,
            duration_seconds=info.duration_seconds,
            fps=info.fps,
            codec=info.codec,
            container="mp4",
            size_bytes=info.size_bytes,
            has_audio=info.has_audio,
        )


@router.get("/{job_id}/source-video/file")
def get_source_video_file(job_id: str, request: Request) -> FileResponse:
    service = request.app.state.source_video_generation
    storage = request.app.state.storage
    with request.app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.SOURCE_VIDEO_READY:
            raise HTTPException(status_code=409, detail="Source video is not ready")
        path = storage.job_directory(job_id, create=False) / SOURCE_VIDEO_FILENAME
        try:
            service.inspect_ready_video(job_id)
        except (
            SourceVideoInvalidFileError,
            SourceVideoInvalidDurationError,
            SourceVideoInvalidDimensionsError,
            SourceVideoInvalidFrameRateError,
            MediaError,
        ) as exc:
            logger.error("SOURCE_VIDEO_READY job_id=%s file serve invalid", job_id)
            raise HTTPException(
                status_code=500,
                detail="Stored source video is missing or invalid",
            ) from exc
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=f"source-video-{job_id}.mp4",
            headers={"Cache-Control": "private, max-age=3600"},
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
    """Delete idle/terminal jobs and their local files."""
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
                    "Only DRAFT, PROMPT_READY, BASE_IMAGE_READY, REFERENCE_READY, "
                    "CHARACTER_EDIT_READY, SOURCE_VIDEO_READY, COMPLETED, or FAILED "
                    "jobs may be deleted."
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
