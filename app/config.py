"""Typed environment configuration for the local application."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    storage_root: Path = Field(default=PROJECT_ROOT / "storage", alias="STORAGE_ROOT")
    max_reference_image_mb: int = Field(default=10, alias="MAX_REFERENCE_IMAGE_MB")
    local_task_workers: int = Field(default=1, ge=1, alias="LOCAL_TASK_WORKERS")
    ffmpeg_binary: str = Field(default="ffmpeg", alias="FFMPEG_BINARY")
    ffprobe_binary: str = Field(default="ffprobe", alias="FFPROBE_BINARY")

    @field_validator("wavespeed_llm_timeout_seconds", mode="before")
    @classmethod
    def _validate_llm_timeout(cls, value: object) -> float:
        import math

        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("WAVESPEED_LLM_TIMEOUT_SECONDS must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("WAVESPEED_LLM_TIMEOUT_SECONDS must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError("WAVESPEED_LLM_TIMEOUT_SECONDS must be finite")
        if number <= 0:
            raise ValueError("WAVESPEED_LLM_TIMEOUT_SECONDS must be positive")
        if number > 600:
            raise ValueError("WAVESPEED_LLM_TIMEOUT_SECONDS must be <= 600")
        return number

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
        # Relative SQLite paths: sqlite:///./file.db → project directory
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


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear settings cache (tests)."""
    get_settings.cache_clear()
