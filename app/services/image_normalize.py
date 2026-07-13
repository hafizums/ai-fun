"""Pillow-based base image verification and PNG normalization."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

from app.providers.media_exceptions import (
    BaseImageInvalidAspectRatioError,
    BaseImageInvalidFileError,
    EditImageInvalidAspectRatioError,
    EditImageInvalidFileError,
    ReferenceImageInvalidFileError,
    ReferenceImageTooLargeError,
    ReferenceImageTooSmallError,
)

logger = logging.getLogger(__name__)

# 9:16 portrait ratio with documented 3% tolerance.
TARGET_RATIO = 9 / 16
RATIO_TOLERANCE = 0.03

# Provider download / upload inputs may be PNG, JPEG, or WebP only.
APPROVED_SOURCE_FORMATS: frozenset[str] = frozenset({"PNG", "JPEG", "WEBP"})


@dataclass(frozen=True)
class NormalizedImageInfo:
    width: int
    height: int
    format: str
    size_bytes: int


def _is_approx_nine_sixteen(width: int, height: int) -> bool:
    if width <= 0 or height <= 0:
        return False
    if height <= width:
        return False  # must be portrait
    ratio = width / height
    lower = TARGET_RATIO * (1.0 - RATIO_TOLERANCE)
    upper = TARGET_RATIO * (1.0 + RATIO_TOLERANCE)
    return lower <= ratio <= upper


def _reject_animated(img: Image.Image, *, error_cls: type[Exception]) -> None:
    """Raise error_cls if the image has more than one frame."""
    try:
        n_frames = getattr(img, "n_frames", 1)
        if int(n_frames) > 1:
            raise error_cls()
        for frame_idx, _frame in enumerate(ImageSequence.Iterator(img)):
            if frame_idx > 0:
                raise error_cls()
    except Exception as exc:
        if isinstance(exc, error_cls):
            raise


def normalize_base_image(
    source_path: Path,
    final_path: Path,
    *,
    max_pixels: int,
) -> NormalizedImageInfo:
    """Validate downloaded image bytes and atomically publish a PNG."""
    return _normalize_portrait_png(
        source_path,
        final_path,
        max_pixels=max_pixels,
        invalid_file_cls=BaseImageInvalidFileError,
        invalid_ratio_cls=BaseImageInvalidAspectRatioError,
    )


def normalize_edited_image(
    source_path: Path,
    final_path: Path,
    *,
    max_pixels: int,
) -> NormalizedImageInfo:
    """Validate edited provider image bytes and atomically publish a PNG."""
    return _normalize_portrait_png(
        source_path,
        final_path,
        max_pixels=max_pixels,
        invalid_file_cls=EditImageInvalidFileError,
        invalid_ratio_cls=EditImageInvalidAspectRatioError,
    )


def _normalize_portrait_png(
    source_path: Path,
    final_path: Path,
    *,
    max_pixels: int,
    invalid_file_cls: type[Exception],
    invalid_ratio_cls: type[Exception],
) -> NormalizedImageInfo:
    partial = final_path.with_suffix(final_path.suffix + ".partial")
    try:
        if partial.exists():
            partial.unlink()
        try:
            with Image.open(source_path) as img:
                if img.format not in APPROVED_SOURCE_FORMATS:
                    raise invalid_file_cls()
                _reject_animated(img, error_cls=invalid_file_cls)

                width, height = img.size
                if width <= 0 or height <= 0:
                    raise invalid_file_cls()
                if width * height > max_pixels:
                    raise invalid_file_cls()
                if not _is_approx_nine_sixteen(width, height):
                    raise invalid_ratio_cls()

                if img.mode in {"RGBA", "LA", "PA"}:
                    converted = img.convert("RGBA")
                elif img.mode == "P" and "transparency" in img.info:
                    converted = img.convert("RGBA")
                else:
                    converted = img.convert("RGB")

                converted.save(partial, format="PNG", optimize=True)
        except Exception as exc:
            if isinstance(exc, (invalid_file_cls, invalid_ratio_cls)):
                raise
            if isinstance(exc, (UnidentifiedImageError, OSError)):
                raise invalid_file_cls() from exc
            raise invalid_file_cls() from exc

        os.replace(partial, final_path)
        size_bytes = final_path.stat().st_size
        if size_bytes <= 0:
            raise invalid_file_cls()
        return NormalizedImageInfo(
            width=width,
            height=height,
            format="PNG",
            size_bytes=size_bytes,
        )
    except Exception as exc:
        _cleanup(partial)
        if isinstance(exc, (invalid_file_cls, invalid_ratio_cls)):
            raise
        logger.error("Portrait PNG normalization failed exception_class=Unexpected")
        raise invalid_file_cls() from None


def normalize_reference_image(
    source_path: Path,
    final_path: Path,
    *,
    max_pixels: int,
    min_width: int,
    min_height: int,
) -> NormalizedImageInfo:
    """Validate a reference identity image and atomically publish a PNG.

    Orientation may be portrait, square, or landscape. EXIF orientation is
    applied; metadata is stripped by re-encoding.
    """
    partial = final_path.with_suffix(final_path.suffix + ".partial")
    try:
        if partial.exists():
            partial.unlink()
        try:
            previous_max = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = max(max_pixels, 1)
            try:
                with Image.open(source_path) as img:
                    if img.format not in APPROVED_SOURCE_FORMATS:
                        raise ReferenceImageInvalidFileError()
                    _reject_animated(img, error_cls=ReferenceImageInvalidFileError)
                    oriented = ImageOps.exif_transpose(img)
                    working = oriented if oriented is not None else img
                    width, height = working.size
                    if width <= 0 or height <= 0:
                        raise ReferenceImageInvalidFileError()
                    if width < min_width or height < min_height:
                        raise ReferenceImageTooSmallError()
                    if width * height > max_pixels:
                        raise ReferenceImageTooLargeError()

                    if working.mode in {"RGBA", "LA", "PA"}:
                        converted = working.convert("RGBA")
                    elif working.mode == "P" and "transparency" in working.info:
                        converted = working.convert("RGBA")
                    else:
                        converted = working.convert("RGB")

                    converted.save(partial, format="PNG", optimize=True)
            finally:
                Image.MAX_IMAGE_PIXELS = previous_max
        except (
            ReferenceImageInvalidFileError,
            ReferenceImageTooSmallError,
            ReferenceImageTooLargeError,
        ):
            raise
        except UnidentifiedImageError as exc:
            raise ReferenceImageInvalidFileError() from exc
        except OSError as exc:
            raise ReferenceImageInvalidFileError() from exc
        except Image.DecompressionBombError as exc:
            raise ReferenceImageTooLargeError() from exc

        os.replace(partial, final_path)
        size_bytes = final_path.stat().st_size
        if size_bytes <= 0:
            raise ReferenceImageInvalidFileError()
        return NormalizedImageInfo(
            width=width,
            height=height,
            format="PNG",
            size_bytes=size_bytes,
        )
    except (
        ReferenceImageInvalidFileError,
        ReferenceImageTooSmallError,
        ReferenceImageTooLargeError,
    ):
        _cleanup(partial)
        raise
    except Exception:
        logger.error("Reference normalization failed exception_class=Unexpected")
        _cleanup(partial)
        raise ReferenceImageInvalidFileError() from None


def inspect_local_png(path: Path, *, max_pixels: int) -> NormalizedImageInfo:
    """Inspect a published portrait PNG (Gate 3 base / Gate 4 edited)."""
    return _inspect_published_png(
        path,
        max_pixels=max_pixels,
        require_nine_sixteen=True,
        invalid_file_cls=BaseImageInvalidFileError,
        invalid_ratio_cls=BaseImageInvalidAspectRatioError,
    )


def inspect_edited_png(path: Path, *, max_pixels: int) -> NormalizedImageInfo:
    """Inspect a published edited portrait PNG."""
    return _inspect_published_png(
        path,
        max_pixels=max_pixels,
        require_nine_sixteen=True,
        invalid_file_cls=EditImageInvalidFileError,
        invalid_ratio_cls=EditImageInvalidAspectRatioError,
    )


def inspect_reference_png(path: Path, *, max_pixels: int) -> NormalizedImageInfo:
    """Inspect a published reference PNG (any orientation)."""
    return _inspect_published_png(
        path,
        max_pixels=max_pixels,
        require_nine_sixteen=False,
        invalid_file_cls=ReferenceImageInvalidFileError,
        invalid_ratio_cls=ReferenceImageInvalidFileError,
    )


def _inspect_published_png(
    path: Path,
    *,
    max_pixels: int,
    require_nine_sixteen: bool,
    invalid_file_cls: type[Exception],
    invalid_ratio_cls: type[Exception],
) -> NormalizedImageInfo:
    if not path.is_file():
        raise invalid_file_cls()
    try:
        with Image.open(path) as img:
            if img.format != "PNG":
                raise invalid_file_cls()
            _reject_animated(img, error_cls=invalid_file_cls)
            img.load()
            width, height = img.size
            if width <= 0 or height <= 0:
                raise invalid_file_cls()
            if width * height > max_pixels:
                raise invalid_file_cls()
            if require_nine_sixteen and not _is_approx_nine_sixteen(width, height):
                raise invalid_ratio_cls()
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise invalid_file_cls()
        return NormalizedImageInfo(
            width=width,
            height=height,
            format="PNG",
            size_bytes=size_bytes,
        )
    except Exception as exc:
        if isinstance(exc, (invalid_file_cls, invalid_ratio_cls)):
            raise
        raise invalid_file_cls() from exc


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.error("Failed to remove partial normalized image")
