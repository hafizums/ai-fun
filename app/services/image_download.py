"""Secure HTTPS download of provider-generated artifacts."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

import httpx

from app.providers.media_exceptions import (
    BaseImageDownloadError,
    BaseImageTooLargeError,
    MediaError,
)

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 3


def redact_url_for_log(url: str) -> str:
    """Log-safe URL without query parameters."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class SecureArtifactDownloader:
    """Stream an HTTPS URL to a local path with size and redirect limits."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_bytes: int,
        download_error_cls: type[MediaError],
        too_large_error_cls: type[MediaError],
        transport: httpx.BaseTransport | None = None,
        client_factory: Callable[..., httpx.Client] | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._download_error_cls = download_error_cls
        self._too_large_error_cls = too_large_error_cls
        self._transport = transport
        self._client_factory = client_factory or httpx.Client

    def download(self, url: str, destination: Path) -> int:
        """Download URL to destination. Returns bytes written. Cleans up on failure."""
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            raise self._download_error_cls()

        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()

        timeout = httpx.Timeout(self._timeout, connect=min(30.0, self._timeout))
        client_kwargs: dict = {
            "timeout": timeout,
            "follow_redirects": False,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        written = 0
        try:
            with self._client_factory(**client_kwargs) as client:
                current = url
                for _ in range(MAX_REDIRECTS + 1):
                    with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise self._download_error_cls()
                            next_url = str(httpx.URL(current).join(location))
                            next_parsed = urlparse(next_url)
                            if next_parsed.scheme.lower() != "https":
                                raise self._download_error_cls()
                            current = next_url
                            continue
                        if response.status_code >= 400:
                            raise self._download_error_cls()
                        with destination.open("wb") as handle:
                            for chunk in response.iter_bytes():
                                if not chunk:
                                    continue
                                written += len(chunk)
                                if written > self._max_bytes:
                                    raise self._too_large_error_cls()
                                handle.write(chunk)
                        if written <= 0:
                            raise self._download_error_cls()
                        return written
                raise self._download_error_cls()
        except MediaError:
            self._cleanup(destination)
            raise
        except Exception:
            logger.error(
                "Artifact download failed exception_class=Unexpected url=%s",
                redact_url_for_log(url),
            )
            self._cleanup(destination)
            raise self._download_error_cls() from None

    @staticmethod
    def _cleanup(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.error("Failed to remove partial download path under storage root")


class ImageDownloader(SecureArtifactDownloader):
    """Image-oriented downloader preserving Gate 3/4 error codes."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_bytes: int,
        transport: httpx.BaseTransport | None = None,
        client_factory: Callable[..., httpx.Client] | None = None,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            download_error_cls=BaseImageDownloadError,
            too_large_error_cls=BaseImageTooLargeError,
            transport=transport,
            client_factory=client_factory,
        )
