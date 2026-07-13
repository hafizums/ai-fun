"""Storage service unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.storage import STORAGE_SUBDIRS, StoragePathError, StorageService


def test_storage_directories_created(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    service = StorageService(root)
    service.ensure_layout()
    assert root.is_dir()
    for name in STORAGE_SUBDIRS:
        assert (root / name).is_dir()


def test_storage_rejects_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    service = StorageService(root)
    service.ensure_layout()
    with pytest.raises(StoragePathError):
        service.resolve_safe("../outside.txt")
    with pytest.raises(StoragePathError):
        service.resolve_safe(Path("generated") / ".." / ".." / "secret.txt")


def test_job_file_deletion_cannot_escape_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    (outside / "keep.txt").write_text("do not delete", encoding="utf-8")

    service = StorageService(root)
    service.ensure_layout()

    # Craft a malicious job_id attempt
    with pytest.raises(StoragePathError):
        service.job_directory("../outside_secret")

    with pytest.raises(StoragePathError):
        service.delete_job_files("../outside_secret")

    assert (outside / "keep.txt").read_text(encoding="utf-8") == "do not delete"

    # Legitimate delete only removes under root
    job_dir = service.job_directory("safe-job-1")
    (job_dir / "artifact.bin").write_bytes(b"x")
    service.delete_job_files("safe-job-1")
    assert not job_dir.exists()
    assert (outside / "keep.txt").exists()
