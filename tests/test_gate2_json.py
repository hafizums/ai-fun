"""JSON extraction and package validation tests."""

from __future__ import annotations

import pytest

from app.providers.llm_exceptions import LLMInvalidResponseError
from app.services.prompt_json import extract_json_text, parse_prompt_package
from tests.fakes import package_json


def test_plain_json_accepted() -> None:
    pkg = parse_prompt_package(package_json())
    assert pkg.transition_hint.preferred_transition.value == "hard_cut"


def test_exact_full_response_json_fence_accepted() -> None:
    fenced = f"```json\n{package_json()}\n```"
    pkg = parse_prompt_package(fenced)
    assert pkg.image_prompt


def test_leading_or_trailing_prose_rejected() -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(f"Here you go:\n{package_json()}")
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(f"{package_json()}\nThanks!")
    with pytest.raises(LLMInvalidResponseError):
        extract_json_text("not json at all")


def test_malformed_json_raises() -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package("{not-json")


def test_missing_fields_rejected() -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package('{"image_prompt":"only"}')


def test_extra_fields_rejected() -> None:
    raw = package_json()
    # inject extra top-level field
    import json

    data = json.loads(raw)
    data["extra_field"] = "nope"
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(json.dumps(data))


def test_invalid_transition_timing_rejected() -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(
            package_json(transition_hint={"start_seconds": 3.0, "end_seconds": 2.0})
        )
    pkg = parse_prompt_package(
        package_json(transition_hint={"start_seconds": 1.0, "end_seconds": 6.0})
    )
    with pytest.raises(ValueError):
        pkg.validate_timing(5)


def test_invalid_transition_enum_rejected() -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(
            package_json(transition_hint={"preferred_transition": "wipe"})
        )


def test_empty_completion_content_rejected() -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package("")
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package(None)
    with pytest.raises(LLMInvalidResponseError):
        parse_prompt_package("   ")
