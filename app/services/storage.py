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

    def job_directory(self, job_id: str, *, create: bool = True) -> Path:
        """Return the job-specific directory under generated/."""
        if ".." in job_id or "/" in job_id or "\\" in job_id:
            raise StoragePathError("Invalid job id for storage path")
        path = self.resolve_safe(Path("generated") / job_id)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def delete_job_files(self, job_id: str) -> None:
        """Remove a job directory if it exists, never outside the storage root."""
        path = self.job_directory(job_id, create=False)
        # Double-check containment before delete.
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise StoragePathError("Refusing to delete outside storage root") from exc

        if path.exists():
            if not path.is_dir():
                raise StoragePathError("Job path is not a directory")
            shutil.rmtree(path)
            logger.info("Deleted job storage directory for job_id=%s", job_id)

    def is_under_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False
