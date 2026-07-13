"""Settings-related API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.providers.exceptions import ProviderError
from app.schemas.job import WaveSpeedTestResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.post("/test-wavespeed", response_model=WaveSpeedTestResponse)
def test_wavespeed(request: Request) -> WaveSpeedTestResponse:
    """Safe WaveSpeed configuration check — never returns the API key.

    Uses configuration-only mode when no lightweight authenticated probe exists
    in the installed SDK. Does not trigger image/video generation.
    """
    provider = request.app.state.wavespeed
    try:
        result = provider.check_configuration()
    except ProviderError as exc:
        logger.warning("WaveSpeed configuration check failed: %s", exc.message)
        return WaveSpeedTestResponse(
            ok=False,
            mode="configuration_only",
            configured=provider.is_configured(),
            message=exc.message,
            base_url=None,
        )

    return WaveSpeedTestResponse(
        ok=bool(result.get("ok")),
        mode=str(result.get("mode", "configuration_only")),
        configured=bool(result.get("configured")),
        message=str(result.get("message", "")),
        base_url=result.get("base_url"),
    )
