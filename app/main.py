"""FastAPI application factory and lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, jobs
from app.api import settings as settings_api
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory, init_db
from app.logging_config import configure_logging, get_logger
from app.providers.wavespeed import WaveSpeedProvider
from app.services.job_recovery import recover_interrupted_jobs
from app.services.storage import StorageService
from app.services.task_runner import TaskRunner

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with lifecycle-managed resources."""
    configure_logging()
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        # Initialize storage
        storage: StorageService = app.state.storage
        storage.ensure_layout()

        # Initialize database (Gate 1: create_all; introduce migrations before evolution)
        engine = app.state.engine
        init_db(engine)

        # Recover interrupted in-process jobs
        with app.state.session_factory() as session:
            recovered = recover_interrupted_jobs(session)
            if recovered:
                logger.warning("Marked %s interrupted job(s) as FAILED", recovered)

        # Start local task runner
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
        description="Local personal-use AI video transformation (Gate 1 foundation)",
        version="0.1.0",
        lifespan=lifespan,
    )

    engine = create_db_engine(app_settings)
    session_factory = create_session_factory(engine)
    storage = StorageService(app_settings.storage_root)
    task_runner = TaskRunner(max_workers=app_settings.local_task_workers)
    wavespeed = WaveSpeedProvider(
        api_key=app_settings.wavespeed_api_key,
        base_url=app_settings.wavespeed_llm_base_url,
    )

    app.state.settings = app_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.storage = storage
    app.state.task_runner = task_runner
    app.state.wavespeed = wavespeed

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(settings_api.router)

    return app


# Default ASGI app for uvicorn: `uvicorn app.main:app --host 127.0.0.1`
app = create_app()
