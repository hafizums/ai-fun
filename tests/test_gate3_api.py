"""Gate 3 API workflow, claim, provider, and artifact tests (offline)."""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.models.job import GenerationJob, JobStatus
from app.providers.media_exceptions import (
    BaseImageInvalidAspectRatioError,
    BaseImageInvalidFileError,
    MediaAuthenticationError,
    MediaInvalidResultError,
    MediaTimeoutError,
)
from app.providers.wavespeed import validate_media_run_result
from app.schemas.prompts import PromptEnvelope
from app.services.base_image_generation import (
    BASE_IMAGE_FILENAME,
    BASE_IMAGE_STAGE,
    BaseImageGenerationService,
)
from app.services.image_download import ImageDownloader
from app.services.image_normalize import inspect_local_png, normalize_base_image
from app.services.prompt_generation import canonical_prompt_json
from tests.conftest import set_job_status
from tests.fakes import wait_for_job_status
from tests.media_fakes import (
    FakeMediaProvider,
    install_fake_media,
    make_portrait_bytes,
    make_prompt_ready_envelope,
    mock_image_transport,
)


def _seed_prompt_ready(client: TestClient, app, *, image_prompt: str | None = None) -> str:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.PROMPT_READY
        job.current_stage = "prompt_ready"
        job.progress_percent = 100
        job.prompt_json = make_prompt_ready_envelope(image_prompt=image_prompt)
        session.commit()
    return job_id


def _install_downloader(app, body: bytes, **kwargs: Any) -> ImageDownloader:
    downloader = ImageDownloader(
        timeout_seconds=app.state.settings.base_image_download_timeout_seconds,
        max_bytes=app.state.settings.base_image_max_download_bytes,
        transport=mock_image_transport(body=body, **kwargs),
    )
    app.state.image_downloader = downloader
    app.state.base_image_generation._downloader = downloader
    return downloader


def test_prompt_failure_cannot_retry_base_image(client: TestClient, app) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.failed_stage = "prompt_generation"
        job.prompt_json = make_prompt_ready_envelope()
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/generate-base-image").status_code == 409


def test_eligible_base_image_failure_can_retry(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.status = JobStatus.FAILED
        job.failed_stage = BASE_IMAGE_STAGE
        job.error_code = "MEDIA_TIMEOUT"
        session.commit()
    assert client.post(f"/api/jobs/{job_id}/generate-base-image").status_code == 202
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})


def test_unknown_job_404(client: TestClient) -> None:
    assert (
        client.post(
            "/api/jobs/00000000-0000-0000-0000-000000000000/generate-base-image"
        ).status_code
        == 404
    )


def test_wrong_state_409(client: TestClient) -> None:
    job_id = client.post("/api/jobs").json()["id"]
    assert client.post(f"/api/jobs/{job_id}/generate-base-image").status_code == 409


def test_sequential_double_submission_enqueues_once(client: TestClient, app, fake_media) -> None:
    hold = threading.Event()
    install_fake_media(app, FakeMediaProvider(delay_event=hold))
    job_id = _seed_prompt_ready(client, app)
    first = client.post(f"/api/jobs/{job_id}/generate-base-image")
    second = client.post(f"/api/jobs/{job_id}/generate-base-image")
    assert first.status_code == 202
    assert second.status_code == 409
    hold.set()
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})
    assert len(app.state.wavespeed.calls) == 1


def test_concurrent_claim_enqueues_once(client, app) -> None:
    service: BaseImageGenerationService = app.state.base_image_generation
    original_submit = app.state.task_runner.submit
    for _ in range(6):
        hold = threading.Event()
        fake = install_fake_media(app, FakeMediaProvider(delay_event=hold))
        submit_calls: list[Any] = []

        def tracking_submit(
            fn: Any,
            *args: Any,
            _calls: list[Any] = submit_calls,
            _original=original_submit,
            **kwargs: Any,
        ) -> Future[Any]:
            _calls.append(1)
            return _original(fn, *args, **kwargs)

        app.state.task_runner.submit = tracking_submit  # type: ignore[method-assign]
        job_id = _seed_prompt_ready(client, app)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def attempt(
            *,
            _barrier=barrier,
            _job_id=job_id,
            _lock=lock,
            _outcomes=outcomes,
        ) -> None:
            _barrier.wait(timeout=5)
            try:
                service.accept_generation(_job_id)
                with _lock:
                    _outcomes.append("accepted")
            except PermissionError:
                with _lock:
                    _outcomes.append("conflict")

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        hold.set()
        for _i in range(100):
            body = client.get(f"/api/jobs/{job_id}").json()
            if body["status"] in {"BASE_IMAGE_READY", "FAILED"}:
                break
            time.sleep(0.05)
        assert outcomes.count("accepted") == 1, outcomes
        assert outcomes.count("conflict") == 1, outcomes
        assert len(submit_calls) == 1
        assert len(fake.calls) == 1
        path = app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
        assert path.is_file()
    app.state.task_runner.submit = original_submit  # type: ignore[method-assign]


def test_accepted_returns_202_quickly(client: TestClient, app) -> None:
    hold = threading.Event()
    install_fake_media(app, FakeMediaProvider(delay_event=hold))
    job_id = _seed_prompt_ready(client, app)
    t0 = time.perf_counter()
    response = client.post(f"/api/jobs/{job_id}/generate-base-image")
    elapsed = time.perf_counter() - t0
    hold.set()
    assert response.status_code == 202
    assert elapsed < 0.75
    assert response.json()["status"] == "BASE_IMAGE_GENERATING"
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY", "FAILED"})


def test_worker_opens_own_session(client, app) -> None:
    original = app.state.session_factory
    opens: list[str] = []

    def tracking_factory():
        opens.append(threading.current_thread().name)
        return original()

    app.state.base_image_generation._session_factory = tracking_factory  # type: ignore[assignment]
    job_id = _seed_prompt_ready(client, app)
    client.post(f"/api/jobs/{job_id}/generate-base-image")
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})
    assert any(name.startswith("local-task") for name in opens)


def test_worker_reads_image_prompt_and_no_llm(client: TestClient, app, fake_llm) -> None:
    marker = "UNIQUE_IMAGE_PROMPT_MARKER_FOR_PROVIDER_INPUT"
    job_id = _seed_prompt_ready(client, app, image_prompt=marker + " " + "x" * 40)
    fake_llm.generate_prompt_completion = MagicMock(
        side_effect=AssertionError("LLM called")
    )
    client.post(f"/api/jobs/{job_id}/generate-base-image")
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})
    call = app.state.wavespeed.calls[0]
    assert call["input"]["prompt"].startswith(marker)
    assert call["model"] == "openai/gpt-image-2/text-to-image"
    assert call["input"] == {
        "prompt": call["input"]["prompt"],
        "aspect_ratio": "9:16",
        "resolution": "1k",
        "quality": "medium",
        "output_format": "png",
        "enable_sync_mode": False,
        "enable_base64_output": False,
    }
    fake_llm.generate_prompt_completion.assert_not_called()


def test_media_provider_uses_media_api_url(app) -> None:
    assert app.state.wavespeed.api_base_url == "https://api.wavespeed.ai"
    assert app.state.llm.base_url == "https://llm.wavespeed.ai/v1"


@pytest.mark.parametrize(
    "outputs",
    [[], "not-a-list", [123], ["http://insecure.example/x.png"], ["data:image/png;base64,xx"]],
)
def test_invalid_provider_outputs_rejected(outputs: Any) -> None:
    with pytest.raises(MediaInvalidResultError):
        validate_media_run_result({"outputs": outputs}, model="m")


def test_provider_timeout_and_auth_map_safely(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    install_fake_media(app, FakeMediaProvider(raise_exc=MediaTimeoutError()))
    client.post(f"/api/jobs/{job_id}/generate-base-image")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "MEDIA_TIMEOUT"
    assert body["failed_stage"] == BASE_IMAGE_STAGE
    assert body["base_image_url"] is None
    assert body["prompt_json"]

    job_id2 = _seed_prompt_ready(client, app)
    install_fake_media(app, FakeMediaProvider(raise_exc=MediaAuthenticationError()))
    client.post(f"/api/jobs/{job_id2}/generate-base-image")
    body2 = wait_for_job_status(client, job_id2, {"FAILED"})
    assert body2["error_code"] == "MEDIA_AUTHENTICATION_FAILED"


def test_provider_exception_key_and_prompt_not_logged(client: TestClient, app) -> None:
    fake_key = "ws-media-secret-do-not-leak"
    prompt_marker = "SECRET_PROMPT_TEXT_SHOULD_NOT_APPEAR"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("app")
    log.addHandler(handler)
    try:
        install_fake_media(
            app,
            FakeMediaProvider(raise_exc=MediaAuthenticationError(f"bad {fake_key}")),
        )
        job_id = _seed_prompt_ready(client, app, image_prompt=prompt_marker + " y" * 20)
        client.post(f"/api/jobs/{job_id}/generate-base-image")
        body = wait_for_job_status(client, job_id, {"FAILED"})
        text = stream.getvalue() + client.get(f"/api/jobs/{job_id}").text
        assert fake_key not in text
        assert prompt_marker not in stream.getvalue()
        assert fake_key not in (body["error_message"] or "")
    finally:
        log.removeHandler(handler)


def test_successful_download_and_ready(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    assert client.post(f"/api/jobs/{job_id}/generate-base-image").status_code == 202
    body = wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})
    assert body["base_image_url"] == f"/api/jobs/{job_id}/base-image/file"
    meta = client.get(f"/api/jobs/{job_id}/base-image")
    assert meta.status_code == 200
    data = meta.json()
    assert data["format"] == "PNG"
    assert data["width"] == 576
    assert data["height"] == 1024
    assert data["size_bytes"] > 0
    file_resp = client.get(f"/api/jobs/{job_id}/base-image/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("image/png")
    path = app.state.storage.job_directory(job_id, create=False) / BASE_IMAGE_FILENAME
    assert path.is_file()
    assert app.state.storage.is_under_root(path)


def test_redirect_to_http_rejected(client: TestClient, app) -> None:
    _install_downloader(
        app,
        body=b"",
        redirect_to="http://evil.example/x.png",
    )
    job_id = _seed_prompt_ready(client, app)
    client.post(f"/api/jobs/{job_id}/generate-base-image")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "BASE_IMAGE_DOWNLOAD_FAILED"
    assert body["base_image_url"] is None


def test_download_exceeding_limit_removed(client: TestClient, app, tmp_path: Path) -> None:
    huge = make_portrait_bytes() + (b"x" * (30 * 1024 * 1024))
    app.state.settings.base_image_max_download_mb = 0.001  # ~1KB
    # rebuild downloader with tiny limit
    downloader = ImageDownloader(
        timeout_seconds=30,
        max_bytes=1024,
        transport=mock_image_transport(body=huge),
    )
    app.state.base_image_generation._downloader = downloader
    job_id = _seed_prompt_ready(client, app)
    client.post(f"/api/jobs/{job_id}/generate-base-image")
    body = wait_for_job_status(client, job_id, {"FAILED"})
    assert body["error_code"] == "BASE_IMAGE_TOO_LARGE"
    job_dir = app.state.storage.job_directory(job_id, create=False)
    assert not (job_dir / "base_image.download").exists()
    assert not (job_dir / BASE_IMAGE_FILENAME).exists()


def test_invalid_and_truncated_images_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not-an-image")
    final = tmp_path / "out.png"
    with pytest.raises(BaseImageInvalidFileError):
        normalize_base_image(bad, final, max_pixels=25_000_000)
    assert not final.exists()

    trunc = tmp_path / "trunc.png"
    data = make_portrait_bytes()
    trunc.write_bytes(data[:40])
    with pytest.raises(BaseImageInvalidFileError):
        normalize_base_image(trunc, final, max_pixels=25_000_000)


def test_animated_landscape_wrong_ratio_excessive_pixels_rejected(tmp_path: Path) -> None:
    final = tmp_path / "out.png"
    animated = tmp_path / "a.gif"
    animated.write_bytes(make_portrait_bytes(fmt="GIF", animated=True))
    with pytest.raises(BaseImageInvalidFileError):
        normalize_base_image(animated, final, max_pixels=25_000_000)

    landscape = tmp_path / "land.jpg"
    landscape.write_bytes(make_portrait_bytes(width=1024, height=576, fmt="JPEG"))
    with pytest.raises(BaseImageInvalidAspectRatioError):
        normalize_base_image(landscape, final, max_pixels=25_000_000)

    wrong = tmp_path / "wrong.png"
    wrong.write_bytes(make_portrait_bytes(width=500, height=1000))  # 0.5 vs 0.5625
    with pytest.raises(BaseImageInvalidAspectRatioError):
        normalize_base_image(wrong, final, max_pixels=25_000_000)

    small = tmp_path / "small.png"
    small.write_bytes(make_portrait_bytes())
    with pytest.raises(BaseImageInvalidFileError):
        normalize_base_image(small, final, max_pixels=10)


def test_png_jpeg_webp_normalize_to_genuine_png(tmp_path: Path) -> None:
    png = tmp_path / "in.png"
    png.write_bytes(make_portrait_bytes(fmt="PNG"))
    out_png = tmp_path / "out_png.png"
    info = normalize_base_image(png, out_png, max_pixels=25_000_000)
    assert info.format == "PNG"
    with Image.open(out_png) as img:
        assert img.format == "PNG"

    jpeg = tmp_path / "in.jpg"
    jpeg.write_bytes(make_portrait_bytes(fmt="JPEG"))
    out = tmp_path / "out.png"
    info = normalize_base_image(jpeg, out, max_pixels=25_000_000)
    assert out.is_file()
    assert info.format == "PNG"
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert "exif" not in {k.lower() for k in img.info}

    try:
        webp = tmp_path / "in.webp"
        webp.write_bytes(make_portrait_bytes(fmt="WEBP"))
    except Exception:
        pytest.skip("WEBP encode not supported by this Pillow build")
    out2 = tmp_path / "out2.png"
    normalize_base_image(webp, out2, max_pixels=25_000_000)
    assert out2.is_file()
    with Image.open(out2) as img:
        assert img.format == "PNG"


def test_unsupported_source_formats_rejected(tmp_path: Path) -> None:
    final = tmp_path / "out.png"
    for fmt, name in (("GIF", "static.gif"), ("BMP", "in.bmp"), ("TIFF", "in.tiff")):
        source = tmp_path / name
        source.write_bytes(make_portrait_bytes(fmt=fmt))
        with pytest.raises(BaseImageInvalidFileError):
            normalize_base_image(source, final, max_pixels=25_000_000)
        assert not final.exists()


def test_inspect_local_png_requires_genuine_png(tmp_path: Path) -> None:
    good = tmp_path / "base_image.png"
    good.write_bytes(make_portrait_bytes(fmt="PNG"))
    info = inspect_local_png(good, max_pixels=25_000_000)
    assert info.format == "PNG"
    assert info.width > 0 and info.height > 0

    renamed_jpeg = tmp_path / "jpeg_as.png"
    renamed_jpeg.write_bytes(make_portrait_bytes(fmt="JPEG"))
    with pytest.raises(BaseImageInvalidFileError):
        inspect_local_png(renamed_jpeg, max_pixels=25_000_000)

    try:
        webp_bytes = make_portrait_bytes(fmt="WEBP")
    except Exception:
        webp_bytes = None
    if webp_bytes is not None:
        renamed_webp = tmp_path / "webp_as.png"
        renamed_webp.write_bytes(webp_bytes)
        with pytest.raises(BaseImageInvalidFileError):
            inspect_local_png(renamed_webp, max_pixels=25_000_000)

    renamed_gif = tmp_path / "gif_as.png"
    renamed_gif.write_bytes(make_portrait_bytes(fmt="GIF"))
    with pytest.raises(BaseImageInvalidFileError):
        inspect_local_png(renamed_gif, max_pixels=25_000_000)

    trunc = tmp_path / "trunc.png"
    trunc.write_bytes(make_portrait_bytes(fmt="PNG")[:40])
    with pytest.raises(BaseImageInvalidFileError):
        inspect_local_png(trunc, max_pixels=25_000_000)

    wrong_ratio = tmp_path / "wrong.png"
    wrong_ratio.write_bytes(make_portrait_bytes(width=500, height=1000, fmt="PNG"))
    with pytest.raises(BaseImageInvalidAspectRatioError):
        inspect_local_png(wrong_ratio, max_pixels=25_000_000)


def _mark_ready_with_bytes(app, job_id: str, data: bytes) -> Path:
    set_job_status(app.state.session_factory, job_id, JobStatus.BASE_IMAGE_READY)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.base_image_url = f"/api/jobs/{job_id}/base-image/file"
        session.commit()
    path = app.state.storage.job_directory(job_id, create=True) / BASE_IMAGE_FILENAME
    path.write_bytes(data)
    return path


def test_missing_and_corrupt_ready_file_500(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    set_job_status(app.state.session_factory, job_id, JobStatus.BASE_IMAGE_READY)
    with app.state.session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        job.base_image_url = f"/api/jobs/{job_id}/base-image/file"
        session.commit()
    assert client.get(f"/api/jobs/{job_id}/base-image").status_code == 500
    assert client.get(f"/api/jobs/{job_id}/base-image/file").status_code == 500

    path = app.state.storage.job_directory(job_id, create=True) / BASE_IMAGE_FILENAME
    path.write_bytes(b"corrupt")
    assert client.get(f"/api/jobs/{job_id}/base-image").status_code == 500


def test_inconsistent_ready_artifacts_return_500(client: TestClient, app) -> None:
    cases: list[bytes] = [
        make_portrait_bytes(fmt="JPEG"),
        make_portrait_bytes(fmt="GIF"),
        make_portrait_bytes(fmt="PNG")[:40],
        make_portrait_bytes(width=500, height=1000, fmt="PNG"),
    ]
    try:
        cases.append(make_portrait_bytes(fmt="WEBP"))
    except Exception:
        pass

    for data in cases:
        job_id = _seed_prompt_ready(client, app)
        _mark_ready_with_bytes(app, job_id, data)
        meta = client.get(f"/api/jobs/{job_id}/base-image")
        file_resp = client.get(f"/api/jobs/{job_id}/base-image/file")
        assert meta.status_code == 500
        assert file_resp.status_code == 500
        assert file_resp.headers.get("content-type", "").split(";")[0] != "image/png"


def test_valid_ready_png_endpoints_200(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    client.post(f"/api/jobs/{job_id}/generate-base-image")
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})
    meta = client.get(f"/api/jobs/{job_id}/base-image")
    assert meta.status_code == 200
    assert meta.json()["format"] == "PNG"
    file_resp = client.get(f"/api/jobs/{job_id}/base-image/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["content-type"].startswith("image/png")
    with Image.open(io.BytesIO(file_resp.content)) as img:
        assert img.format == "PNG"


def test_file_endpoint_rejects_non_ready(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    assert client.get(f"/api/jobs/{job_id}/base-image/file").status_code == 409
    assert client.get(f"/api/jobs/{job_id}/base-image").status_code == 409


def test_task_submission_failure(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    app.state.task_runner.submit = MagicMock(side_effect=RuntimeError("pool dead"))
    response = client.post(f"/api/jobs/{job_id}/generate-base-image")
    assert response.status_code == 500
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "TASK_SUBMISSION_FAILED"
    assert body["failed_stage"] == BASE_IMAGE_STAGE
    assert body["prompt_json"]


def test_infinity_word_in_prompt_serializes_nonfinite_rejected() -> None:
    text = make_prompt_ready_envelope(
        image_prompt="A scene near Infinity Bridge with soft light and calm pose."
    )
    assert "Infinity" in text
    envelope = PromptEnvelope.model_validate(json.loads(text))
    assert "Infinity" in envelope.prompts.image_prompt
    # Round-trip through canonical serializer must succeed for the word Infinity.
    again = canonical_prompt_json(envelope)
    assert "Infinity" in again

    payload = json.loads(make_prompt_ready_envelope())
    payload["prompts"]["transition_hint"]["start_seconds"] = float("nan")
    with pytest.raises((ValueError, TypeError)):
        json.dumps(payload, allow_nan=False)


def test_no_edit_or_video_model_invoked(client: TestClient, app) -> None:
    job_id = _seed_prompt_ready(client, app)
    client.post(f"/api/jobs/{job_id}/generate-base-image")
    wait_for_job_status(client, job_id, {"BASE_IMAGE_READY"})
    for call in app.state.wavespeed.calls:
        assert "image-to-video" not in call["model"]
        assert "edit" not in call["model"]
        assert call["model"] == "openai/gpt-image-2/text-to-image"
