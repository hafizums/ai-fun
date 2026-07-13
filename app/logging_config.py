"""Logging setup with secret-safe defaults."""

from __future__ import annotations

import logging
import re
from typing import Final

_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+"),
    re.compile(r"(?i)WAVESPEED_API_KEY\s*=\s*\S+"),
)


def sanitize_log_message(message: str) -> str:
    """Strip likely secret material from a log message."""
    sanitized = message
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1=[REDACTED]", sanitized)
    return sanitized


class SanitizingFilter(logging.Filter):
    """Filter that redacts secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = sanitize_log_message(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: sanitize_log_message(str(v)) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        sanitize_log_message(str(a)) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:
            # Never break logging because of sanitization failures.
            pass
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root application logging once."""
    root = logging.getLogger()
    if any(isinstance(f, SanitizingFilter) for h in root.handlers for f in h.filters):
        root.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    sanitizer = SanitizingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(sanitizer)
    logging.getLogger("app").setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
