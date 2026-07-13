# Gate 6 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `e13fe4386a1ab41979797e37fec1964ee91b1582`
- Branch: `master`
- Gates 1–5 approved

## Verified WaveSpeed Fun Control schema

Docs: https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-fun-control

Model ID: `wavespeed-ai/wan-2.2/fun-control`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `image` | string | yes | Uploaded **edited-image** URL (identity) |
| `video` | string | yes | Uploaded **source-video** URL (motion) |
| `prompt` | string | no | Gate 2 `motion_prompt` |
| `resolution` | string | no (default `480p`) | `480p` or `720p` |
| `seed` | integer | no (default `-1`) | `-1` … `2147483647`; `-1` = random |

**Not in schema (not sent):** `negative_prompt`, `duration`, sync/Base64 fields.

`WAVESPEED_CONTROL_VIDEO_DURATION_SECONDS` is used only as the local ffprobe validation target.

## Status workflow

```text
SOURCE_VIDEO_READY
    → CONTROL_VIDEO_GENERATING
    → CONTROL_VIDEO_READY | FAILED (failed_stage = control_video_generation)
```

- Idle / deletable: `CONTROL_VIDEO_READY`
- Restart-failed: `CONTROL_VIDEO_GENERATING`
- Generic `FAILED → CONTROL_VIDEO_GENERATING` forbidden
- Retry only when `failed_stage == control_video_generation`

## Input artifact selection

- **Identity:** `storage/generated/{job_id}/edited_image.png`
- **Motion:** `storage/generated/{job_id}/source_video.mp4`
- Base image and reference image are **not** uploaded

## Provider upload order

1. `upload_file(edited_image.png)`
2. `upload_file(source_video.mp4)`

## Exact provider input

```json
{
  "image": "<uploaded edited-image URL>",
  "video": "<uploaded source-video URL>",
  "prompt": "<validated motion_prompt>",
  "resolution": "480p",
  "seed": -1
}
```

## Paid submission retry policy

Generation client: `max_retries=0`, `max_connection_retries=0`.  
`run_model(..., max_task_retries=0)`. Upload client remains separate.

## Secure download

`SecureArtifactDownloader` with `CONTROL_VIDEO_DOWNLOAD_FAILED` / `CONTROL_VIDEO_TOO_LARGE`.

Paths: `controlled_video.download` → `controlled_video.source` → `controlled_video.mp4`

## Video validation and normalization

Generalized Gate 5 tooling with injectable `ControlVideo*` error classes. Portrait ~5s MP4 rules. Stream-copy preferred; H.264/`yuv420p`/`-an` fallback. Atomic `os.replace`.

## Storage and atomic publication

Final: `storage/generated/{job_id}/controlled_video.mp4`  
Persisted URL: `/api/jobs/{job_id}/controlled-video/file`  
DB commit failure after publish removes uncommitted final and marks failed.

## API routes

| Method | Path |
|--------|------|
| `POST` | `/api/jobs/{id}/generate-controlled-video` |
| `GET` | `/api/jobs/{id}/controlled-video` |
| `GET` | `/api/jobs/{id}/controlled-video/file` |

## Tests

`tests/test_gate6_api.py` covers status/claim concurrency, edited+source upload order/roles, verified schema keys, integrity failures, timeout artifact preservation, endpoints, no I2V confusion. Existing Gates 1–5 retained.

## Manual live smoke test

Not performed — local `.env` has empty `WAVESPEED_API_KEY`.

## Security checks

- No full prompts, uploaded URLs, provider query strings, keys, or bytes in app logs / DB errors
- No private SDK methods
- No real network in automated tests

## Database compatibility

Reuses `controlled_video_url`. No new columns. No reset.

## Known limitations

- Fun Control schema has no duration or negative_prompt; output length follows control video ≈5s validation
- Motion/identity quality is structural only; visual QA is manual smoke
- H.264 fallback drops audio (`-an`)

## Deferred work

- Transition detection
- Video merging
- Frontend

## Git information

- Starting commit: `e13fe4386a1ab41979797e37fec1964ee91b1582`
- Implementation commit: `e3b468fb4d90765995887455a92a02b86bd6e8c6`
- Final HEAD: `e3b468fb4d90765995887455a92a02b86bd6e8c6`
