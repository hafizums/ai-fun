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

    def model_post_init(self, __context: object) -> None:
        if self.reference_image_min_width * self.reference_image_min_height > (
            self.reference_image_max_pixels
        ):
            raise ValueError(
                "REFERENCE_IMAGE_MIN_WIDTH * REFERENCE_IMAGE_MIN_HEIGHT must be "
                "<= REFERENCE_IMAGE_MAX_PIXELS"
            )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear settings cache (tests)."""
    get_settings.cache_clear()
