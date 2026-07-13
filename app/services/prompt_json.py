"""Parse and validate LLM prompt JSON without provider structured-output assumptions."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.providers.llm_exceptions import LLMInvalidResponseError
from app.schemas.prompts import PromptPackage

_FENCE_RE = re.compile(
    r"^```(?:json)?\s*\n(?P<body>.*?)\n```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def _reject_nonstandard_number(value: str) -> Any:
    """Reject JSON NaN / Infinity constants (non-standard and non-finite)."""
    raise LLMInvalidResponseError("LLM response contains a non-finite numeric constant")


def extract_json_text(raw: str | None) -> str:
    """Extract a single JSON object string from model content.

    Accepts:
    - a plain JSON object
    - the entire response enclosed in exactly one Markdown json code fence

    Rejects leading/trailing prose and other fence layouts.
    """
    if raw is None:
        raise LLMInvalidResponseError("LLM returned empty content")
    text = raw.strip()
    if not text:
        raise LLMInvalidResponseError("LLM returned empty content")

    fence = _FENCE_RE.match(text)
    if fence:
        candidate = fence.group("body").strip()
    else:
        candidate = text

    if not candidate.startswith("{") or not candidate.endswith("}"):
        raise LLMInvalidResponseError("LLM response is not a JSON object")

    # Reject prose wrapped around a bare object (fence path already constrained).
    if fence is None and (candidate != text):
        raise LLMInvalidResponseError("LLM response contains surrounding prose")

    return candidate


def parse_prompt_package(raw: str | None) -> PromptPackage:
    """Parse content into a strict PromptPackage; never silently fill fields."""
    candidate = extract_json_text(raw)
    try:
        data: Any = json.loads(candidate, parse_constant=_reject_nonstandard_number)
    except LLMInvalidResponseError:
        raise
    except json.JSONDecodeError as exc:
        raise LLMInvalidResponseError("LLM response is not valid JSON") from exc

    if not isinstance(data, dict):
        raise LLMInvalidResponseError("LLM response must be a JSON object")

    try:
        return PromptPackage.model_validate(data)
    except ValidationError as exc:
        raise LLMInvalidResponseError("LLM response failed prompt package validation") from exc
