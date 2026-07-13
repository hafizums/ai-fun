"""Typed environment configuration for the local application."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _finite_positive(
    value: object,
    *,
    name: str,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if number <= 0:
        raise ValueError(f"{name} must be positive")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return number


class Settings(BaseSettings):
    """Environment-backed settings. Secrets must never be logged or returned by APIs."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(
        default=f"sqlite:///{(PROJECT_ROOT / 'ai_fun_motion.db').as_posix()}",
        alias="DATABASE_URL",
    )
    wavespeed_api_key: str = Field(default="", alias="WAVESPEED_API_KEY")
    wavespeed_api_base_url: str = Field(
        default="https://api.wavespeed.ai",
        alias="WAVESPEED_API_BASE_URL",
    )
    wavespeed_llm_base_url: str = Field(
        default="https://llm.wavespeed.ai/v1",
        alias="WAVESPEED_LLM_BASE_URL",
    )
    wavespeed_llm_model: str = Field(
        default="openai/gpt-5.1",
        alias="WAVESPEED_LLM_MODEL",
    )
    wavespeed_llm_timeout_seconds: float = Field(
        default=120.0,
        alias="WAVESPEED_LLM_TIMEOUT_SECONDS",
    )

    wavespeed_base_image_model: str = Field(
        default="openai/gpt-image-2/text-to-image",
        alias="WAVESPEED_BASE_IMAGE_MODEL",
    )
    wavespeed_base_image_aspect_ratio: str = Field(
        default="9:16",
        alias="WAVESPEED_BASE_IMAGE_ASPECT_RATIO",
    )
    wavespeed_base_image_resolution: str = Field(
        default="1k",
        alias="WAVESPEED_BASE_IMAGE_RESOLUTION",
    )
    wavespeed_base_image_quality: str = Field(
        default="medium",
        alias="WAVESPEED_BASE_IMAGE_QUALITY",
    )
    wavespeed_base_image_output_format: str = Field(
        default="png",
        alias="WAVESPEED_BASE_IMAGE_OUTPUT_FORMAT",
    )
    wavespeed_media_timeout_seconds: float = Field(
        default=600.0,
        alias="WAVESPEED_MEDIA_TIMEOUT_SECONDS",
    )
    wavespeed_media_poll_interval_seconds: float = Field(
        default=1.0,
        alias="WAVESPEED_MEDIA_POLL_INTERVAL_SECONDS",
    )
    base_image_download_timeout_seconds: float = Field(
        default=120.0,
        alias="BASE_IMAGE_DOWNLOAD_TIMEOUT_SECONDS",
    )
    base_image_max_download_mb: float = Field(
        default=25.0,
        alias="BASE_IMAGE_MAX_DOWNLOAD_MB",
    )
    base_image_max_pixels: int = Field(
        default=25_000_000,
        alias="BASE_IMAGE_MAX_PIXELS",
    )

    wavespeed_character_edit_model: str = Field(
        default="openai/gpt-image-2/edit",
        alias="WAVESPEED_CHARACTER_EDIT_MODEL",
    )
    wavespeed_character_edit_aspect_ratio: str = Field(
        default="9:16",
        alias="WAVESPEED_CHARACTER_EDIT_ASPECT_RATIO",
    )
    wavespeed_character_edit_resolution: str = Field(
        default="1k",
        alias="WAVESPEED_CHARACTER_EDIT_RESOLUTION",
    )
    wavespeed_character_edit_quality: str = Field(
        default="medium",
        alias="WAVESPEED_CHARACTER_EDIT_QUALITY",
    )
    wavespeed_character_edit_output_format: str = Field(
        default="png",
        alias="WAVESPEED_CHARACTER_EDIT_OUTPUT_FORMAT",
    )
    reference_image_max_upload_mb: float = Field(
        default=15.0,
        alias="REFERENCE_IMAGE_MAX_UPLOAD_MB",
    )
    reference_image_max_pixels: int = Field(
        default=25_000_000,
        alias="REFERENCE_IMAGE_MAX_PIXELS",
    )
    reference_image_min_width: int = Field(
        default=256,
        alias="REFERENCE_IMAGE_MIN_WIDTH",
    )
    reference_image_min_height: int = Field(
        default=256,
        alias="REFERENCE_IMAGE_MIN_HEIGHT",
    )

    wavespeed_source_video_model: str = Field(
        default="wavespeed-ai/wan-2.2/i2v-480p-ultra-fast",
        alias="WAVESPEED_SOURCE_VIDEO_MODEL",
    )
    wavespeed_source_video_duration_seconds: int = Field(
        default=5,
        alias="WAVESPEED_SOURCE_VIDEO_DURATION_SECONDS",
    )
    wavespeed_source_video_seed: int = Field(
        default=-1,
        alias="WAVESPEED_SOURCE_VIDEO_SEED",
    )
    source_video_download_timeout_seconds: float = Field(
        default=300.0,
        alias="SOURCE_VIDEO_DOWNLOAD_TIMEOUT_SECONDS",
    )
    source_video_max_download_mb: float = Field(
        default=100.0,
        alias="SOURCE_VIDEO_MAX_DOWNLOAD_MB",
    )
    source_video_max_duration_seconds: float = Field(
        default=7.0,
        alias="SOURCE_VIDEO_MAX_DURATION_SECONDS",
    )
    source_video_min_duration_seconds: float = Field(
        default=4.0,
        alias="SOURCE_VIDEO_MIN_DURATION_SECONDS",
    )
    source_video_duration_tolerance_seconds: float = Field(
        default=0.35,
        alias="SOURCE_VIDEO_DURATION_TOLERANCE_SECONDS",
    )
    source_video_min_width: int = Field(
        default=240,
        alias="SOURCE_VIDEO_MIN_WIDTH",
    )
    source_video_min_height: int = Field(
        default=400,
        alias="SOURCE_VIDEO_MIN_HEIGHT",
    )
    source_video_max_pixels: int = Field(
        default=5_000_000,
        alias="SOURCE_VIDEO_MAX_PIXELS",
    )
    source_video_max_fps: float = Field(
        default=60.0,
        alias="SOURCE_VIDEO_MAX_FPS",
    )

    wavespeed_control_video_model: str = Field(
        default="wavespeed-ai/wan-2.2/fun-control",
        alias="WAVESPEED_CONTROL_VIDEO_MODEL",
    )
    # Local validation target only — Fun Control schema has no duration field.
    wavespeed_control_video_duration_seconds: int = Field(
        default=5,
        alias="WAVESPEED_CONTROL_VIDEO_DURATION_SECONDS",
    )
    wavespeed_control_video_resolution: str = Field(
        default="480p",
        alias="WAVESPEED_CONTROL_VIDEO_RESOLUTION",
    )
    wavespeed_control_video_seed: int = Field(
        default=-1,
        alias="WAVESPEED_CONTROL_VIDEO_SEED",
    )
    control_video_download_timeout_seconds: float = Field(
        default=300.0,
        alias="CONTROL_VIDEO_DOWNLOAD_TIMEOUT_SECONDS",
    )
    control_video_max_download_mb: float = Field(
        default=150.0,
        alias="CONTROL_VIDEO_MAX_DOWNLOAD_MB",
    )
    control_video_max_duration_seconds: float = Field(
        default=7.0,
        alias="CONTROL_VIDEO_MAX_DURATION_SECONDS",
    )
    control_video_min_duration_seconds: float = Field(
        default=4.0,
        alias="CONTROL_VIDEO_MIN_DURATION_SECONDS",
    )
    control_video_duration_tolerance_seconds: float = Field(
        default=0.35,
        alias="CONTROL_VIDEO_DURATION_TOLERANCE_SECONDS",
    )
    control_video_min_width: int = Field(
        default=240,
        alias="CONTROL_VIDEO_MIN_WIDTH",
    )
    control_video_min_height: int = Field(
        default=400,
        alias="CONTROL_VIDEO_MIN_HEIGHT",
    )
    control_video_max_pixels: int = Field(
        default=5_000_000,
        alias="CONTROL_VIDEO_MAX_PIXELS",
    )
    control_video_max_fps: float = Field(
        default=60.0,
        alias="CONTROL_VIDEO_MAX_FPS",
    )

    transition_analysis_fps: float = Field(
        default=8.0,
        alias="TRANSITION_ANALYSIS_FPS",
    )
    transition_search_start_ratio: float = Field(
        default=0.35,
        alias="TRANSITION_SEARCH_START_RATIO",
    )
    transition_search_end_ratio: float = Field(
        default=0.70,
        alias="TRANSITION_SEARCH_END_RATIO",
    )
    transition_min_seconds_from_edge: float = Field(
        default=0.75,
        alias="TRANSITION_MIN_SECONDS_FROM_EDGE",
    )
    transition_confidence_threshold: float = Field(
        default=0.08,
        alias="TRANSITION_CONFIDENCE_THRESHOLD",
    )
    transition_crossfade_seconds: float = Field(
        default=0.12,
        alias="TRANSITION_CROSSFADE_SECONDS",
    )
    final_video_max_duration_seconds: float = Field(
        default=8.0,
        alias="FINAL_VIDEO_MAX_DURATION_SECONDS",
    )
    final_video_max_pixels: int = Field(
        default=5_000_000,
        alias="FINAL_VIDEO_MAX_PIXELS",
    )
    final_video_max_fps: float = Field(
        default=60.0,
        alias="FINAL_VIDEO_MAX_FPS",
    )
    final_video_ffmpeg_timeout_seconds: float = Field(
        default=180.0,
        alias="FINAL_VIDEO_FFMPEG_TIMEOUT_SECONDS",
    )
    final_video_max_input_duration_delta_seconds: float = Field(
        default=0.35,
        alias="FINAL_VIDEO_MAX_INPUT_DURATION_DELTA_SECONDS",
    )
    final_video_max_dimension_delta_pixels: int = Field(
        default=8,
        alias="FINAL_VIDEO_MAX_DIMENSION_DELTA_PIXELS",
    )

    storage_root: Path = Field(default=PROJECT_ROOT / "storage", alias="STORAGE_ROOT")
    local_task_workers: int = Field(default=1, ge=1, alias="LOCAL_TASK_WORKERS")
    ffmpeg_binary: str = Field(default="ffmpeg", alias="FFMPEG_BINARY")
    ffprobe_binary: str = Field(default="ffprobe", alias="FFPROBE_BINARY")

    @field_validator("wavespeed_llm_timeout_seconds", mode="before")
    @classmethod
    def _validate_llm_timeout(cls, value: object) -> float:
        return _finite_positive(value, name="WAVESPEED_LLM_TIMEOUT_SECONDS", maximum=600)

    @field_validator("wavespeed_media_timeout_seconds", mode="before")
    @classmethod
    def _validate_media_timeout(cls, value: object) -> float:
        return _finite_positive(value, name="WAVESPEED_MEDIA_TIMEOUT_SECONDS", maximum=3600)

    @field_validator("wavespeed_media_poll_interval_seconds", mode="before")
    @classmethod
    def _validate_media_poll(cls, value: object) -> float:
        return _finite_positive(
            value, name="WAVESPEED_MEDIA_POLL_INTERVAL_SECONDS", maximum=60
        )

    @field_validator("base_image_download_timeout_seconds", mode="before")
    @classmethod
    def _validate_download_timeout(cls, value: object) -> float:
        return _finite_positive(
            value, name="BASE_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", maximum=600
        )

    @field_validator("base_image_max_download_mb", mode="before")
    @classmethod
    def _validate_download_mb(cls, value: object) -> float:
        return _finite_positive(value, name="BASE_IMAGE_MAX_DOWNLOAD_MB", maximum=200)

    @field_validator("base_image_max_pixels", mode="before")
    @classmethod
    def _validate_max_pixels(cls, value: object) -> int:
        number = _finite_positive(value, name="BASE_IMAGE_MAX_PIXELS", maximum=100_000_000)
        return int(number)

    @field_validator("reference_image_max_upload_mb", mode="before")
    @classmethod
    def _validate_reference_upload_mb(cls, value: object) -> float:
        return _finite_positive(value, name="REFERENCE_IMAGE_MAX_UPLOAD_MB", maximum=100)

    @field_validator("reference_image_max_pixels", mode="before")
    @classmethod
    def _validate_reference_max_pixels(cls, value: object) -> int:
        number = _finite_positive(
            value, name="REFERENCE_IMAGE_MAX_PIXELS", maximum=100_000_000
        )
        return int(number)

    @field_validator("reference_image_min_width", "reference_image_min_height", mode="before")
    @classmethod
    def _validate_reference_min_dim(cls, value: object) -> int:
        number = _finite_positive(value, name="REFERENCE_IMAGE_MIN_DIMENSION", maximum=10_000)
        return int(number)

    @field_validator("wavespeed_source_video_duration_seconds", mode="before")
    @classmethod
    def _validate_source_duration(cls, value: object) -> int:
        number = _finite_positive(
            value, name="WAVESPEED_SOURCE_VIDEO_DURATION_SECONDS", maximum=30
        )
        duration = int(number)
        if duration not in {5, 8}:
            raise ValueError("WAVESPEED_SOURCE_VIDEO_DURATION_SECONDS must be 5 or 8")
        return duration

    @field_validator("wavespeed_source_video_seed", mode="before")
    @classmethod
    def _validate_source_seed(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("WAVESPEED_SOURCE_VIDEO_SEED must be an integer")
        try:
            seed = int(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("WAVESPEED_SOURCE_VIDEO_SEED must be an integer") from exc
        if seed < -1 or seed > 2_147_483_647:
            raise ValueError("WAVESPEED_SOURCE_VIDEO_SEED must be between -1 and 2147483647")
        return seed

    @field_validator("source_video_download_timeout_seconds", mode="before")
    @classmethod
    def _validate_source_download_timeout(cls, value: object) -> float:
        return _finite_positive(
            value, name="SOURCE_VIDEO_DOWNLOAD_TIMEOUT_SECONDS", maximum=1800
        )

    @field_validator("source_video_max_download_mb", mode="before")
    @classmethod
    def _validate_source_download_mb(cls, value: object) -> float:
        return _finite_positive(value, name="SOURCE_VIDEO_MAX_DOWNLOAD_MB", maximum=500)

    @field_validator(
        "source_video_max_duration_seconds",
        "source_video_min_duration_seconds",
        "source_video_duration_tolerance_seconds",
        mode="before",
    )
    @classmethod
    def _validate_source_duration_bounds(cls, value: object) -> float:
        return _finite_positive(value, name="SOURCE_VIDEO_DURATION_BOUND", maximum=60)

    @field_validator("source_video_min_width", "source_video_min_height", mode="before")
    @classmethod
    def _validate_source_min_dim(cls, value: object) -> int:
        number = _finite_positive(value, name="SOURCE_VIDEO_MIN_DIMENSION", maximum=10_000)
        return int(number)

    @field_validator("source_video_max_pixels", mode="before")
    @classmethod
    def _validate_source_max_pixels(cls, value: object) -> int:
        number = _finite_positive(
            value, name="SOURCE_VIDEO_MAX_PIXELS", maximum=50_000_000
        )
        return int(number)

    @field_validator("source_video_max_fps", mode="before")
    @classmethod
    def _validate_source_max_fps(cls, value: object) -> float:
        return _finite_positive(value, name="SOURCE_VIDEO_MAX_FPS", maximum=240)

    @field_validator("wavespeed_control_video_duration_seconds", mode="before")
    @classmethod
    def _validate_control_duration(cls, value: object) -> int:
        number = _finite_positive(
            value, name="WAVESPEED_CONTROL_VIDEO_DURATION_SECONDS", maximum=30
        )
        duration = int(number)
        if duration not in {5, 8}:
            raise ValueError("WAVESPEED_CONTROL_VIDEO_DURATION_SECONDS must be 5 or 8")
        return duration

    @field_validator("wavespeed_control_video_resolution", mode="before")
    @classmethod
    def _validate_control_resolution(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if text not in {"480p", "720p"}:
            raise ValueError("WAVESPEED_CONTROL_VIDEO_RESOLUTION must be 480p or 720p")
        return text

    @field_validator("wavespeed_control_video_seed", mode="before")
    @classmethod
    def _validate_control_seed(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("WAVESPEED_CONTROL_VIDEO_SEED must be an integer")
        try:
            seed = int(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("WAVESPEED_CONTROL_VIDEO_SEED must be an integer") from exc
        if seed < -1 or seed > 2_147_483_647:
            raise ValueError(
                "WAVESPEED_CONTROL_VIDEO_SEED must be between -1 and 2147483647"
            )
        return seed

    @field_validator("control_video_download_timeout_seconds", mode="before")
    @classmethod
    def _validate_control_download_timeout(cls, value: object) -> float:
        return _finite_positive(
            value, name="CONTROL_VIDEO_DOWNLOAD_TIMEOUT_SECONDS", maximum=1800
        )

    @field_validator("control_video_max_download_mb", mode="before")
    @classmethod
    def _validate_control_download_mb(cls, value: object) -> float:
        return _finite_positive(value, name="CONTROL_VIDEO_MAX_DOWNLOAD_MB", maximum=500)

    @field_validator(
        "control_video_max_duration_seconds",
        "control_video_min_duration_seconds",
        "control_video_duration_tolerance_seconds",
        mode="before",
    )
    @classmethod
    def _validate_control_duration_bounds(cls, value: object) -> float:
        return _finite_positive(value, name="CONTROL_VIDEO_DURATION_BOUND", maximum=60)

    @field_validator("control_video_min_width", "control_video_min_height", mode="before")
    @classmethod
    def _validate_control_min_dim(cls, value: object) -> int:
        number = _finite_positive(value, name="CONTROL_VIDEO_MIN_DIMENSION", maximum=10_000)
        return int(number)

    @field_validator("control_video_max_pixels", mode="before")
    @classmethod
    def _validate_control_max_pixels(cls, value: object) -> int:
        number = _finite_positive(
            value, name="CONTROL_VIDEO_MAX_PIXELS", maximum=50_000_000
        )
        return int(number)

    @field_validator("control_video_max_fps", mode="before")
    @classmethod
    def _validate_control_max_fps(cls, value: object) -> float:
        return _finite_positive(value, name="CONTROL_VIDEO_MAX_FPS", maximum=240)

    @field_validator("transition_analysis_fps", mode="before")
    @classmethod
    def _validate_transition_fps(cls, value: object) -> float:
        return _finite_positive(value, name="TRANSITION_ANALYSIS_FPS", maximum=30)

    @field_validator(
        "transition_search_start_ratio",
        "transition_search_end_ratio",
        mode="before",
    )
    @classmethod
    def _validate_transition_ratio(cls, value: object) -> float:
        number = float(value)  # type: ignore[arg-type]
        if not (0.0 <= number <= 1.0):
            raise ValueError("TRANSITION_SEARCH ratios must be between 0 and 1")
        return number

    @field_validator(
        "transition_min_seconds_from_edge",
        "transition_confidence_threshold",
        "transition_crossfade_seconds",
        "final_video_max_duration_seconds",
        "final_video_max_fps",
        "final_video_ffmpeg_timeout_seconds",
        "final_video_max_input_duration_delta_seconds",
        mode="before",
    )
    @classmethod
    def _validate_final_positive_floats(cls, value: object) -> float:
        return _finite_positive(value, name="FINAL_OR_TRANSITION_BOUND", maximum=1800)

    @field_validator("final_video_max_pixels", mode="before")
    @classmethod
    def _validate_final_max_pixels(cls, value: object) -> int:
        return int(
            _finite_positive(value, name="FINAL_VIDEO_MAX_PIXELS", maximum=50_000_000)
        )

    @field_validator("final_video_max_dimension_delta_pixels", mode="before")
    @classmethod
    def _validate_final_dim_delta(cls, value: object) -> int:
        return int(
            _finite_positive(
                value, name="FINAL_VIDEO_MAX_DIMENSION_DELTA_PIXELS", maximum=1000
            )
        )

    @field_validator("storage_root", mode="before")
    @classmethod
    def _resolve_storage_root(cls, value: object) -> Path:
        if value is None or value == "":
            return PROJECT_ROOT / "storage"
        path = Path(str(value))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_sqlite_url(cls, value: object) -> str:
        if value is None or value == "":
            return f"sqlite:///{(PROJECT_ROOT / 'ai_fun_motion.db').as_posix()}"
        url = str(value)
        prefix = "sqlite:///"
        if url.startswith(prefix) and not url.startswith("sqlite:////"):
            rest = url[len(prefix) :]
            if rest.startswith("./") or (
                rest and not rest.startswith("/") and ":/" not in rest[:3]
            ):
                db_path = (PROJECT_ROOT / rest).resolve()
                return f"sqlite:///{db_path.as_posix()}"
        return url

    @property
    def wavespeed_configured(self) -> bool:
        return bool(self.wavespeed_api_key.strip())

    @property
    def base_image_max_download_bytes(self) -> int:
        return int(self.base_image_max_download_mb * 1024 * 1024)

    @property
    def reference_image_max_upload_bytes(self) -> int:
        return int(self.reference_image_max_upload_mb * 1024 * 1024)

    @property
    def source_video_max_download_bytes(self) -> int:
        return int(self.source_video_max_download_mb * 1024 * 1024)

    @property
    def control_video_max_download_bytes(self) -> int:
        return int(self.control_video_max_download_mb * 1024 * 1024)

    def model_post_init(self, __context: object) -> None:
        if self.reference_image_min_width * self.reference_image_min_height > (
            self.reference_image_max_pixels
        ):
            raise ValueError(
                "REFERENCE_IMAGE_MIN_WIDTH * REFERENCE_IMAGE_MIN_HEIGHT must be "
                "<= REFERENCE_IMAGE_MAX_PIXELS"
            )
        if self.source_video_min_duration_seconds >= self.source_video_max_duration_seconds:
            raise ValueError(
                "SOURCE_VIDEO_MIN_DURATION_SECONDS must be < "
                "SOURCE_VIDEO_MAX_DURATION_SECONDS"
            )
        if self.source_video_min_width * self.source_video_min_height > (
            self.source_video_max_pixels
        ):
            raise ValueError(
                "SOURCE_VIDEO_MIN_WIDTH * SOURCE_VIDEO_MIN_HEIGHT must be "
                "<= SOURCE_VIDEO_MAX_PIXELS"
            )
        if self.control_video_min_duration_seconds >= self.control_video_max_duration_seconds:
            raise ValueError(
                "CONTROL_VIDEO_MIN_DURATION_SECONDS must be < "
                "CONTROL_VIDEO_MAX_DURATION_SECONDS"
            )
        if self.control_video_min_width * self.control_video_min_height > (
            self.control_video_max_pixels
        ):
            raise ValueError(
                "CONTROL_VIDEO_MIN_WIDTH * CONTROL_VIDEO_MIN_HEIGHT must be "
                "<= CONTROL_VIDEO_MAX_PIXELS"
            )
        if self.transition_search_start_ratio >= self.transition_search_end_ratio:
            raise ValueError(
                "TRANSITION_SEARCH_START_RATIO must be < TRANSITION_SEARCH_END_RATIO"
            )
        if self.transition_crossfade_seconds >= self.transition_min_seconds_from_edge:
            raise ValueError(
                "TRANSITION_CROSSFADE_SECONDS must be < TRANSITION_MIN_SECONDS_FROM_EDGE"
            )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear settings cache (tests)."""
    get_settings.cache_clear()
