"""Local filesystem storage with path-traversal protection."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_SUBDIRS = ("uploads", "generated", "temporary", "final")


class StoragePathError(ValueError):
    """Raised when a path is outside the storage root or otherwise unsafe."""


class StorageService:
    """Resolve and manage paths under a configured storage root."""

    def __init__(self, storage_root: Path) -> None:
        self.root = Path(storage_root).resolve()

    def ensure_layout(self) -> None:
        """Create the standard storage directory tree."""
        self.root.mkdir(parents=True, exist_ok=True)
        for name in STORAGE_SUBDIRS:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        logger.info("Storage layout ready under configured root")

    def resolve_safe(self, relative_path: str | Path) -> Path:
        """Resolve a path relative to the storage root; reject traversal."""
        candidate = Path(relative_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()

        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise StoragePathError("Path escapes storage root") from exc
        return resolved

    def _safe_job_id(self, job_id: str) -> str:
        if ".." in job_id or "/" in job_id or "\\" in job_id:
            raise StoragePathError("Invalid job id for storage path")
        return job_id

    def job_directory(self, job_id: str, *, create: bool = True) -> Path:
        """Return the job-specific directory under generated/."""
        path = self.resolve_safe(Path("generated") / self._safe_job_id(job_id))
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def upload_job_directory(self, job_id: str, *, create: bool = True) -> Path:
        """Return the job-specific directory under uploads/."""
        path = self.resolve_safe(Path("uploads") / self._safe_job_id(job_id))
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def final_job_directory(self, job_id: str, *, create: bool = True) -> Path:
        """Return the job-specific directory under final/."""
        path = self.resolve_safe(Path("final") / self._safe_job_id(job_id))
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def temporary_job_directory(self, job_id: str, *, create: bool = True) -> Path:
        """Return a job-scoped temporary directory under temporary/."""
        path = self.resolve_safe(Path("temporary") / self._safe_job_id(job_id))
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def relative_under_root(self, path: Path) -> str:
        """Return a portable relative path string under the storage root."""
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise StoragePathError("Path escapes storage root") from exc

    def delete_job_files(self, job_id: str) -> None:
        """Remove generated, upload, final, and temporary job directories if they exist."""
        for factory in (
            self.job_directory,
            self.upload_job_directory,
            self.final_job_directory,
            self.temporary_job_directory,
        ):
            path = factory(job_id, create=False)
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise StoragePathError("Refusing to delete outside storage root") from exc
            if path.exists():
                if not path.is_dir():
                    raise StoragePathError("Job path is not a directory")
                shutil.rmtree(path)
                logger.info("Deleted job storage directory path_type=%s", path.name)

    def is_under_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False
