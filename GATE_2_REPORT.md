# Gate 2 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `8e8229ffbd7d05a53d23c97961f9d212b6c3c7be`
- Branch: `master`
- Initial Git status: clean, tracking `origin/master`

## Implemented

- Status and database compatibility: added `PROMPT_READY`; Gate 2 transitions; deletable idle state; no SQLite CHECK migration required
- LLM provider: `WaveSpeedLLMProvider` via official `openai.OpenAI` (`chat.completions.create`) against `WAVESPEED_LLM_BASE_URL`
- Prompt contract: centralized system prompt requiring 9:16, one person, static camera, age-appropriate clothing, hand occlusion, background preservation
- JSON validation: plain object or single full-response ```json``` fence; strict Pydantic package; no repair calls
- Background service: commit `PROMPT_GENERATING` then `TaskRunner.submit`; worker opens its own session; one LLM call; canonical envelope persistence
- API endpoints: `POST /api/jobs/{id}/generate-prompts` (`202`), `GET /api/jobs/{id}/prompts`
- Retry behavior: `FAILED` → `PROMPT_GENERATING` only when `failed_stage == "prompt_generation"`

## Prompt package

Stored envelope (`prompt_json`):

```json
{
  "schema_version": 1,
  "request": {
    "subject_description": "...",
    "scene_description": "...",
    "motion_description": "...",
    "duration_seconds": 5
  },
  "prompts": {
    "image_prompt": "...",
    "edit_prompt": "...",
    "motion_prompt": "...",
    "motion_negative_prompt": "...",
    "transition_hint": {
      "event_description": "...",
      "start_seconds": 0.0,
      "end_seconds": 0.0,
      "preferred_transition": "hard_cut|short_crossfade|flash"
    }
  },
  "metadata": {
    "provider": "wavespeed",
    "model": "openai/gpt-5.1",
    "response_id": null,
    "input_tokens": null,
    "output_tokens": null,
    "generated_at": "UTC ISO timestamp"
  }
}
```

Live generated prompt text is intentionally omitted from this report.

## API routes

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/jobs/{id}/generate-prompts` | Validate body; transition to `PROMPT_GENERATING`; enqueue background task; `202`. `404` unknown. `409` wrong state / in-progress. `500` on submit failure after safe FAIL. |
| `GET` | `/api/jobs/{id}/prompts` | Typed envelope when `PROMPT_READY`. `404` unknown. `409` not ready. `500` corrupted stored JSON. |

Existing Gate 1 routes unchanged in behavior aside from allowing delete of `PROMPT_READY`.

## Tests

- Commands:
  - `pytest -q`
  - `ruff check app tests`
- Total: 76
- Passed: 76
- Failed: 0
- Skipped: 0
- Warnings: 1 (Starlette TestClient/`httpx` deprecation)

## Manual live smoke test

- Performed: no
- Reason: local `.env` has no `WAVESPEED_API_KEY`
- Final status: n/a
- Schema validation: n/a
- Response ID available: n/a
- Usage available: n/a
- No media generation confirmed: n/a (automated tests confirm media methods are not invoked)

## Security checks

- Secret handling: API key never returned; fixed public error strings persisted
- Raw-response handling: raw LLM content not logged; canonical validated JSON stored only
- Log sanitization: task runner and LLM mapper avoid exception message/traceback leakage
- Fixed public errors: `LLM_*` and `TASK_SUBMISSION_FAILED` codes with safe messages

## SQLite compatibility

Gate 1 DDL stores `status` as `VARCHAR(24)` with **no CHECK constraint**. `PROMPT_READY` fits the column. **No compatibility migration was required.** Regression test `test_gate1_compatible_sqlite_database_starts` boots against a hand-built Gate 1 schema and successfully writes `PROMPT_READY`.

## Known limitations

- In-process tasks do not survive restarts.
- JSON mode is prompt-enforced and locally validated, not provider-guaranteed.
- No automatic paid repair request.
- No image or video generation yet.
- Live provider smoke test was not performed (no API key configured).

## Deferred work

- Base image generation (WaveSpeed GPT Image 2)
- Reference upload and character edit
- Wan 2.2 image-to-video and Fun Control
- Transition detection and FFmpeg merge
- Frontend UI
- Public prediction polling for media jobs

## Git information

- Implementation commit: _(filled after commit)_
- Final HEAD: _(filled after commit)_
- Final Git status: _(filled after commit)_
