"""Pillow-based base image verification and PNG normalization."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageSequence, UnidentifiedImageError

from app.providers.media_exceptions import (
    BaseImageInvalidAspectRatioError,
    BaseImageInvalidFileError,
)

logger = logging.getLogger(__name__)

# 9:16 portrait ratio with documented 3% tolerance.
TARGET_RATIO = 9 / 16
RATIO_TOLERANCE = 0.03

# Provider download inputs may be PNG, JPEG, or WebP only; all normalize to PNG.
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


def _reject_animated(img: Image.Image) -> None:
    """Raise BaseImageInvalidFileError if the image has more than one frame."""
    try:
        n_frames = getattr(img, "n_frames", 1)
        if int(n_frames) > 1:
            raise BaseImageInvalidFileError()
        for frame_idx, _frame in enumerate(ImageSequence.Iterator(img)):
            if frame_idx > 0:
                raise BaseImageInvalidFileError()
    except BaseImageInvalidFileError:
        raise
    except Exception:
        pass


def normalize_base_image(
    source_path: Path,
    final_path: Path,
    *,
    max_pixels: int,
) -> NormalizedImageInfo:
    """Validate downloaded image bytes and atomically publish a PNG."""
    partial = final_path.with_suffix(final_path.suffix + ".partial")
    try:
        if partial.exists():
            partial.unlink()
        try:
            with Image.open(source_path) as img:
                if img.format not in APPROVED_SOURCE_FORMATS:
                    raise BaseImageInvalidFileError()
                _reject_animated(img)

                width, height = img.size
                if width <= 0 or height <= 0:
                    raise BaseImageInvalidFileError()
                if width * height > max_pixels:
                    raise BaseImageInvalidFileError()
                if not _is_approx_nine_sixteen(width, height):
                    raise BaseImageInvalidAspectRatioError()

                # Re-encode to strip metadata (EXIF etc.).
                if img.mode in {"RGBA", "LA", "PA"}:
                    converted = img.convert("RGBA")
                elif img.mode == "P" and "transparency" in img.info:
                    converted = img.convert("RGBA")
                else:
                    converted = img.convert("RGB")

                converted.save(partial, format="PNG", optimize=True)
        except BaseImageInvalidFileError:
            raise
        except BaseImageInvalidAspectRatioError:
            raise
        except UnidentifiedImageError as exc:
            raise BaseImageInvalidFileError() from exc
        except OSError as exc:
            # Truncated / unreadable
            raise BaseImageInvalidFileError() from exc

        # Atomic publish within the same directory.
        os.replace(partial, final_path)
        size_bytes = final_path.stat().st_size
        if size_bytes <= 0:
            raise BaseImageInvalidFileError()
        return NormalizedImageInfo(
            width=width,
            height=height,
            format="PNG",
            size_bytes=size_bytes,
        )
    except (BaseImageInvalidFileError, BaseImageInvalidAspectRatioError):
        _cleanup(partial)
        raise
    except Exception:
        logger.error("Base image normalization failed exception_class=Unexpected")
        _cleanup(partial)
        raise BaseImageInvalidFileError() from None


def inspect_local_png(path: Path, *, max_pixels: int) -> NormalizedImageInfo:
    """Inspect an already-published local PNG for metadata and file endpoints.

    Requires Pillow to identify the file as PNG. A JPEG/WebP/GIF renamed to
    ``.png`` is rejected. Truncated or incomplete PNG data is rejected via a
    full pixel load.
    """
    if not path.is_file():
        raise BaseImageInvalidFileError()
    try:
        with Image.open(path) as img:
            if img.format != "PNG":
                raise BaseImageInvalidFileError()
            _reject_animated(img)
            # Fully decode pixels so truncated / incomplete PNGs fail here.
            img.load()
            width, height = img.size
            if width <= 0 or height <= 0:
                raise BaseImageInvalidFileError()
            if width * height > max_pixels:
                raise BaseImageInvalidFileError()
            if not _is_approx_nine_sixteen(width, height):
                raise BaseImageInvalidAspectRatioError()
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise BaseImageInvalidFileError()
        return NormalizedImageInfo(
            width=width,
            height=height,
            format="PNG",
            size_bytes=size_bytes,
        )
    except (BaseImageInvalidFileError, BaseImageInvalidAspectRatioError):
        raise
    except Exception as exc:
        raise BaseImageInvalidFileError() from exc


def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.error("Failed to remove partial normalized image")
