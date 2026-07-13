"""FastAPI application factory and lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, jobs
from app.api import settings as settings_api
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory, init_db
from app.logging_config import configure_logging, get_logger
from app.providers.media_exceptions import (
    ControlVideoDownloadError,
    ControlVideoTooLargeError,
    SourceVideoDownloadError,
    SourceVideoTooLargeError,
)
from app.providers.wavespeed import WaveSpeedProvider
from app.providers.wavespeed_llm import WaveSpeedLLMProvider
from app.services.base_image_generation import BaseImageGenerationService
from app.services.character_edit_generation import CharacterEditGenerationService
from app.services.control_video_generation import ControlVideoGenerationService
from app.services.image_download import ImageDownloader, SecureArtifactDownloader
from app.services.job_recovery import recover_interrupted_jobs
from app.services.prompt_generation import PromptGenerationService
from app.services.reference_upload import (
    ReferenceUploadService,
    reconcile_waiting_for_reference_jobs,
)
from app.services.source_video_generation import SourceVideoGenerationService
from app.services.storage import StorageService
from app.services.task_runner import TaskRunner

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with lifecycle-managed resources."""
    configure_logging()
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        storage: StorageService = app.state.storage
        storage.ensure_layout()

        engine = app.state.engine
        init_db(engine)

        with app.state.session_factory() as session:
            recovered = recover_interrupted_jobs(session)
            if recovered:
                logger.warning("Marked %s interrupted job(s) as FAILED", recovered)
            reconciled = reconcile_waiting_for_reference_jobs(
                session,
                storage,
                app_settings,
            )
            if reconciled:
                logger.warning(
                    "Reconciled %s WAITING_FOR_REFERENCE job(s) after restart",
                    reconciled,
                )

        runner: TaskRunner = app.state.task_runner
        runner.start()
        logger.info("Application startup complete (env=%s)", app_settings.app_env)

        try:
            yield
        finally:
            runner.shutdown(wait=True)
            engine.dispose()
            logger.info("Application shutdown complete")

    app = FastAPI(
        title="AI Fun Motion",
        description=(
            "Local personal-use AI video transformation "
            "(Gate 6: Fun Control motion transfer)"
        ),
        version="0.6.0",
        lifespan=lifespan,
    )

    engine = create_db_engine(app_settings)
    session_factory = create_session_factory(engine)
    storage = StorageService(app_settings.storage_root)
    task_runner = TaskRunner(max_workers=app_settings.local_task_workers)
    wavespeed = WaveSpeedProvider(
        api_key=app_settings.wavespeed_api_key,
        base_url=app_settings.wavespeed_api_base_url,
    )
    llm = WaveSpeedLLMProvider(
        api_key=app_settings.wavespeed_api_key,
        base_url=app_settings.wavespeed_llm_base_url,
        model=app_settings.wavespeed_llm_model,
        timeout_seconds=app_settings.wavespeed_llm_timeout_seconds,
    )
    prompt_generation = PromptGenerationService(
        session_factory=session_factory,
        task_runner=task_runner,
        llm_provider=llm,
        llm_model=app_settings.wavespeed_llm_model,
    )
    downloader = ImageDownloader(
        timeout_seconds=app_settings.base_image_download_timeout_seconds,
        max_bytes=app_settings.base_image_max_download_bytes,
    )
    base_image_generation = BaseImageGenerationService(
        session_factory=session_factory,
        task_runner=task_runner,
        media_provider=wavespeed,
        storage=storage,
        settings=app_settings,
        downloader=downloader,
    )
    reference_upload = ReferenceUploadService(
        session_factory=session_factory,
        storage=storage,
        settings=app_settings,
    )
    character_edit_generation = CharacterEditGenerationService(
        session_factory=session_factory,
        task_runner=task_runner,
        media_provider=wavespeed,
        storage=storage,
        settings=app_settings,
        downloader=downloader,
    )
    video_downloader = SecureArtifactDownloader(
        timeout_seconds=app_settings.source_video_download_timeout_seconds,
        max_bytes=app_settings.source_video_max_download_bytes,
        download_error_cls=SourceVideoDownloadError,
        too_large_error_cls=SourceVideoTooLargeError,
    )
    source_video_generation = SourceVideoGenerationService(
        session_factory=session_factory,
        task_runner=task_runner,
        media_provider=wavespeed,
        storage=storage,
        settings=app_settings,
        downloader=video_downloader,
    )
    control_video_downloader = SecureArtifactDownloader(
        timeout_seconds=app_settings.control_video_download_timeout_seconds,
        max_bytes=app_settings.control_video_max_download_bytes,
        download_error_cls=ControlVideoDownloadError,
        too_large_error_cls=ControlVideoTooLargeError,
    )
    control_video_generation = ControlVideoGenerationService(
        session_factory=session_factory,
        task_runner=task_runner,
        media_provider=wavespeed,
        storage=storage,
        settings=app_settings,
        downloader=control_video_downloader,
    )

    app.state.settings = app_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.storage = storage
    app.state.task_runner = task_runner
    app.state.wavespeed = wavespeed
    app.state.llm = llm
    app.state.prompt_generation = prompt_generation
    app.state.base_image_generation = base_image_generation
    app.state.reference_upload = reference_upload
    app.state.character_edit_generation = character_edit_generation
    app.state.source_video_generation = source_video_generation
    app.state.control_video_generation = control_video_generation
    app.state.image_downloader = downloader
    app.state.video_downloader = video_downloader
    app.state.control_video_downloader = control_video_downloader

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(settings_api.router)

    return app


app = create_app()
