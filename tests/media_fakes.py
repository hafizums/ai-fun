"""Deterministic fake media providers and image helpers for Gate 3 tests."""

from __future__ import annotations

import io
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from PIL import Image

from app.providers.base import MediaProvider, MediaRunResult
from app.providers.media_exceptions import MediaError
from app.schemas.prompts import (
    PromptEnvelope,
    PromptMetadata,
    PromptPackage,
    PromptRequestSnapshot,
)
from app.services.prompt_generation import canonical_prompt_json
from tests.fakes import VALID_PACKAGE


def make_portrait_bytes(
    *,
    width: int = 576,
    height: int = 1024,
    fmt: str = "PNG",
    animated: bool = False,
) -> bytes:
    """Create small in-memory image bytes for offline tests."""
    buffer = io.BytesIO()
    if animated and fmt.upper() == "GIF":
        frames = []
        for i in range(3):
            frame = Image.new("RGB", (width, height), color=(i * 40, 80, 120))
            frames.append(frame)
        frames[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
    else:
        img = Image.new("RGB", (width, height), color=(40, 120, 200))
        img.save(buffer, format=fmt)
    return buffer.getvalue()


def make_prompt_ready_envelope(*, image_prompt: str | None = None) -> str:
    package = dict(VALID_PACKAGE)
    if image_prompt is not None:
        package["image_prompt"] = image_prompt
    prompts = PromptPackage.model_validate(package)
    envelope = PromptEnvelope(
        schema_version=1,
        request=PromptRequestSnapshot(
            subject_description="one young Asian child looking directly at the camera",
            scene_description="a simple ordinary indoor room with soft natural daylight",
            motion_description="a quick playful hand flick that briefly crosses the face",
            duration_seconds=5,
        ),
        prompts=prompts,
        metadata=PromptMetadata(
            provider="wavespeed",
            model="openai/gpt-5.1",
            response_id="resp_seed",
            input_tokens=1,
            output_tokens=1,
            generated_at=datetime.now(UTC),
        ),
    )
    return canonical_prompt_json(envelope)


class FakeMediaProvider(MediaProvider):
    """Offline deterministic media stub."""

    def __init__(
        self,
        *,
        outputs: list[Any] | None = None,
        raise_exc: Exception | None = None,
        delay_event: threading.Event | None = None,
        configured: bool = True,
        model: str = "openai/gpt-image-2/text-to-image",
        base_url: str = "https://api.wavespeed.ai",
        prediction_id: str | None = "pred_test_1",
        result_factory: Callable[[dict[str, Any]], MediaRunResult] | None = None,
    ) -> None:
        self._outputs = outputs if outputs is not None else [
            "https://cdn.example.invalid/generated.png"
        ]
        self._raise_exc = raise_exc
        self._delay_event = delay_event
        self._configured = configured
        self.model = model
        self.base_url = base_url
        self.api_base_url = base_url
        self._prediction_id = prediction_id
        self._result_factory = result_factory
        self.calls: list[dict[str, Any]] = []
        self.upload_calls: list[str] = []
        self.started = threading.Event()
        self.finished = threading.Event()

    def is_configured(self) -> bool:
        return self._configured

    def check_configuration(self) -> dict[str, Any]:
        return {
            "ok": self._configured,
            "mode": "configuration_only",
            "configured": self._configured,
            "base_url": self.base_url,
            "message": "fake",
        }

    def upload_file(self, file: Any) -> str:
        path = str(file)
        self.upload_calls.append(path)
        # Deterministic HTTPS URL without query params.
        return f"https://cdn.example.invalid/upload/{len(self.upload_calls)}.png"

    def run_model(
        self,
        model: str,
        input_params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        enable_sync_mode: bool = False,
    ) -> MediaRunResult:
        self.started.set()
        payload = {
            "model": model,
            "input": input_params or {},
            "timeout": timeout,
            "poll_interval": poll_interval,
            "enable_sync_mode": enable_sync_mode,
        }
        self.calls.append(payload)
        if self._delay_event is not None:
            self._delay_event.wait(timeout=5)
        if self._raise_exc is not None:
            self.finished.set()
            raise self._raise_exc
        if self._result_factory is not None:
            result = self._result_factory(payload)
            self.finished.set()
            return result
        # Replicate validation path for non-string / bad outputs when tests set them.
        from app.providers.wavespeed import validate_media_run_result

        raw = {"outputs": self._outputs, "id": self._prediction_id, "model": model}
        try:
            result = validate_media_run_result(raw, model=model)
        except MediaError:
            self.finished.set()
            raise
        self.finished.set()
        return result

    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        raise AssertionError("get_prediction must not be called")


def install_fake_media(app: Any, fake: FakeMediaProvider) -> FakeMediaProvider:
    app.state.wavespeed = fake
    app.state.base_image_generation._media = fake
    if hasattr(app.state, "character_edit_generation"):
        app.state.character_edit_generation._media = fake
    return fake


def mock_image_transport(
    *,
    body: bytes,
    status_code: int = 200,
    redirect_to: str | None = None,
    content_type: str = "image/png",
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if redirect_to is not None:
            return httpx.Response(302, headers={"location": redirect_to})
        return httpx.Response(
            status_code,
            content=body,
            headers={"content-type": content_type},
        )

    return httpx.MockTransport(handler)


def assert_https_only(url: str) -> None:
    assert urlparse(url).scheme == "https"
