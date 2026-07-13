# Gate 3 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `12ac5c15cb7b87f77919025fd7f3d5a74506321a`
- Branch: `master`
- Initial Git status: clean, tracking `origin/master` at the starting commit; Gate 3 work was uncommitted until this report’s implementation commit

## Implemented

- Status transitions: `PROMPT_READY → BASE_IMAGE_GENERATING`; `BASE_IMAGE_GENERATING → BASE_IMAGE_READY | FAILED`; `BASE_IMAGE_READY` idle and deletable; generic `FAILED → BASE_IMAGE_GENERATING` forbidden
- Atomic base-image claim: conditional SQL for `PROMPT_READY` or `FAILED` + `failed_stage == base_image_generation`; commit before enqueue; `rowcount == 1` only
- WaveSpeed model invocation: public `Client.run` via `WaveSpeedProvider.run_model` against `WAVESPEED_API_BASE_URL`
- Provider result validation: non-empty HTTPS outputs only; reject `data:` / Base64; map fixed `MEDIA_*` errors
- Secure download: streaming `httpx`, HTTPS-only redirects, byte cap, partial cleanup
- Pillow validation and normalization: reject animated / landscape / wrong ~9:16 / oversized; re-encode PNG; atomic `os.replace`
- Local artifact endpoints: metadata + PNG file under `/api/jobs/{id}/base-image*`
- Retry behavior: eligible base-image failures only; preserves `prompt_json`; clears partial files
- Gate 2 cleanup: `json.dumps(..., allow_nan=False)` for canonical prompt JSON

## Model configuration

- Model: `openai/gpt-image-2/text-to-image` (application config only)
- Fixed input parameters (prompt text omitted from this report):

```json
{
  "prompt": "<validated image_prompt>",
  "aspect_ratio": "9:16",
  "resolution": "1k",
  "quality": "medium",
  "output_format": "png",
  "enable_sync_mode": false,
  "enable_base64_output": false
}
```

- Timeouts/poll: `WAVESPEED_MEDIA_TIMEOUT_SECONDS`, `WAVESPEED_MEDIA_POLL_INTERVAL_SECONDS`
- Media base URL: `WAVESPEED_API_BASE_URL` (never `WAVESPEED_LLM_BASE_URL`)

## Storage

- Deterministic path: `storage/generated/{job_id}/base_image.png`
- Temporary path: job-scoped partial under the same job directory (via `StorageService`)
- Atomic publication: write temp PNG → `os.replace` to final name
- Cleanup: delete partials on failure; leave `prompt_json` intact; `base_image_url` stays `null` until ready

## API routes

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/jobs/{job_id}/generate-base-image` | `202` accepted; `404` unknown; `409` wrong/ineligible state |
| `GET` | `/api/jobs/{job_id}/base-image` | Metadata when `BASE_IMAGE_READY` |
| `GET` | `/api/jobs/{job_id}/base-image/file` | `image/png`; private cache header; no provider URL |

Persisted local URL shape: `/api/jobs/{job_id}/base-image/file`

## Tests

- Commands: `pytest -q`; `ruff check app tests`
- Total: 189
- Passed: 189
- Failed: 0
- Skipped: 0
- Warnings: 1 (Starlette/`httpx` TestClient deprecation from dependency)

Concurrent claim: `test_concurrent_claim_enqueues_once` — passed (barrier; exactly one accept, one conflict, one submit, one fake media call, one published image).

## Manual live smoke test

- Performed: no
- Reason: local `.env` has empty `WAVESPEED_API_KEY`
- Final status: n/a
- Image dimensions: n/a
- Aspect-ratio validation: n/a
- PNG validation: n/a
- Local storage validation: n/a
- No LLM/edit/video generation confirmed: n/a (live path not run)

## Security checks

- Secret handling: API key never logged; provider exception text not persisted
- Provider-result handling: validated app-owned result only; no raw response storage
- URL handling: HTTPS only; query strings never logged or stored in `base_image_url`
- Download limits: connect/read timeouts, redirect bound, max download MB
- Image validation: Pillow verify; reject animated / non-portrait / wrong ratio / excess pixels
- Path safety: paths via `StorageService` under storage root only
- Partial-file cleanup: removed on failure and before eligible retry

## Known limitations

- In-process tasks do not survive restarts.
- No automatic paid retry.
- Provider URLs are downloaded immediately and are not exposed through the local API.
- No reference upload or character replacement yet.
- No image-to-video yet.
- No frontend yet.

## Deferred work

- Gate 4+: reference-image upload, character editing, image-to-video, Fun Control, transition detection, video merging, frontend

## Git information

- Implementation commit: `d566f6522f90a9b49880fd77ebf872aa53a18bb6`
- Final HEAD: `203107731a1c1aa7170a9dc2160729e01e7a7927`
- Final Git status: clean working tree on `master`, synced with `origin/master`
