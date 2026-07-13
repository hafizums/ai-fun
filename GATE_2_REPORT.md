# Gate 2 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `8e8229ffbd7d05a53d23c97961f9d212b6c3c7be`
- Branch: `master`
- Initial Git status: clean, tracking `origin/master`

## Findings corrected

Correction pass starting HEAD: `155d38925862ebfcb36f868f7c153431e80c2c72`.

### Atomic-claim design

`PromptGenerationService._atomic_claim()` performs a single SQLAlchemy
`UPDATE generation_jobs SET ... WHERE id = ? AND (status = 'DRAFT' OR (status = 'FAILED' AND failed_stage = 'prompt_generation'))`.

Only when `rowcount == 1` does the service enqueue work. Competing claimants receive `PermissionError` → HTTP `409`. Unknown IDs still return `404`. Claim clears prior errors and `prompt_json`, sets `PROMPT_GENERATING` / stage / progress / `updated_at`, and commits before `TaskRunner.submit()`. Submission failures still mark `TASK_SUBMISSION_FAILED`.

### Concurrent test method

`test_concurrent_prompt_claim_only_one_wins` uses the file-backed temp SQLite DB from fixtures, a `threading.Barrier(2)`, and eight repeated rounds. Exactly one accept, one conflict, one submit, and one fake LLM call are asserted each round.

### Null and non-finite validation

Prompt string fields and `event_description` reject `null` and non-strings (no `str()` coercion). Transition seconds require finite numbers. JSON parsing uses `parse_constant` to reject `NaN` / `Infinity` / `-Infinity`. `WAVESPEED_LLM_TIMEOUT_SECONDS` must be finite before range checks. Invalid LLM payloads fail the job with `LLM_INVALID_RESPONSE` and `prompt_json = null`.

### Correct corrupted-state behavior

`GET /api/jobs/{id}/prompts`:
- `409` when status ≠ `PROMPT_READY`
- `500` when `PROMPT_READY` but `prompt_json` missing
- `500` when stored envelope cannot be parsed/validated
- Never returns corrupted content

### Revised status transitions

Generic map:
- `PROMPT_GENERATING` → `PROMPT_READY` | `FAILED` only (removed `BASE_IMAGE_GENERATING`)
- `FAILED` has no outgoing transitions in the generic map
- Eligible prompt retries occur only via the atomic claim SQL (`failed_stage == prompt_generation`)
- `apply_status_transition()` cannot move unrelated FAILED jobs into prompt generation

## Implemented

- Status and database compatibility: added `PROMPT_READY`; Gate 2 transitions; deletable idle state; no SQLite CHECK migration required
- LLM provider: `WaveSpeedLLMProvider` via official `openai.OpenAI` (`chat.completions.create`) against `WAVESPEED_LLM_BASE_URL`
- Prompt contract: centralized system prompt requiring 9:16, one person, static camera, age-appropriate clothing, hand occlusion, background preservation
- JSON validation: plain object or single full-response ```json``` fence; strict Pydantic package; no repair calls
- Background service: atomic claim → commit → `TaskRunner.submit`; worker opens its own session; one LLM call; canonical envelope persistence
- API endpoints: `POST /api/jobs/{id}/generate-prompts` (`202`), `GET /api/jobs/{id}/prompts`
- Retry behavior: atomic claim for DRAFT or FAILED prompt-generation jobs only

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
| `POST` | `/api/jobs/{id}/generate-prompts` | Atomic claim → enqueue; `202`. `404` unknown. `409` conflict/ineligible. `500` on submit failure after safe FAIL. |
| `GET` | `/api/jobs/{id}/prompts` | Typed envelope when ready. `409` not ready. `500` missing/corrupt stored JSON. |

## Tests

- Commands:
  - `pytest -q`
  - `ruff check app tests`
- Total: 156
- Passed: 156
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

Gate 1 DDL stores `status` as `VARCHAR(24)` with **no CHECK constraint**. `PROMPT_READY` fits the column. **No compatibility migration was required.**

## Known limitations

- In-process tasks do not survive restarts.
- JSON mode is prompt-enforced and locally validated, not provider-guaranteed.
- No automatic paid repair request.
- No image or video generation yet.
- Live provider smoke test was not performed (no API key configured).

## Deferred work

- Base image generation (WaveSpeed GPT Image 2) — Gate 3+
- `PROMPT_READY` → `BASE_IMAGE_GENERATING`
- Reference upload and character edit
- Wan 2.2 image-to-video and Fun Control
- Transition detection and FFmpeg merge
- Frontend UI
- Public prediction polling for media jobs

## Git information

- Correction starting HEAD: `155d38925862ebfcb36f868f7c153431e80c2c72`
- Implementation commit: _(filled after commit)_
- Final HEAD: _(filled after commit)_
- Final Git status: _(filled after commit)_
