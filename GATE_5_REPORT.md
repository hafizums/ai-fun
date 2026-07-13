# Gate 5 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `fc520a632d97eca4186b870d606699a8b21794c4`
- Branch: `master`
- Gates 1–4 approved; working tree began at Gate 4 HEAD

## Verified WaveSpeed model schema

Docs inspected: https://wavespeed.ai/docs/docs-api/wavespeed-ai/wan-2.2-i2v-480p-ultra-fast

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `prompt` | string | yes | Motion prompt from Gate 2 envelope |
| `image` | string | yes | Uploaded **original base-image** URL |
| `negative_prompt` | string | no | Motion negative prompt from envelope |
| `duration` | integer | no (default 5) | Allowed `5` or `8`; MVP fixed to `5` |
| `seed` | integer | no (default -1) | Range `-1` … `2147483647`; `-1` = random |
| `last_image` | string | no | **Not used** for Gate 5 |

No `enable_sync_mode` / `enable_base64_output` fields on this model schema.

Exact application input:

```json
{
  "image": "<uploaded original base-image URL>",
  "prompt": "<validated motion_prompt>",
  "negative_prompt": "<validated motion_negative_prompt>",
  "duration": 5,
  "seed": -1
}
```

Media API only (`WAVESPEED_API_BASE_URL`). Public `upload_file` / `run_model` only.

## Status workflow

```text
CHARACTER_EDIT_READY
    → SOURCE_VIDEO_GENERATING
    → SOURCE_VIDEO_READY | FAILED (failed_stage = source_video_generation)
```

- Idle / deletable: `SOURCE_VIDEO_READY`
- Restart-failed: `SOURCE_VIDEO_GENERATING`
- Generic `FAILED → SOURCE_VIDEO_GENERATING` forbidden
- Retry only via atomic claim when `failed_stage == source_video_generation`

## Source image selection

The original Gate 3 file `storage/generated/{job_id}/base_image.png` is uploaded and animated.

`edited_image.png` is validated only as Gate 4 state-integrity proof. It is never uploaded and never placed in the I2V `image` field.

## Provider input

Built strictly from the verified schema above. One upload of the base image, one Wan I2V generation call, no automatic retry, no model fallback, no LLM, no edit, no Fun Control.

## Secure download

Reusable `SecureArtifactDownloader` extracted from Gate 3/4 image download:

- HTTPS only; bounded redirects; reject redirect to HTTP
- Explicit timeouts; byte cap while streaming; reject empty
- Partial cleanup on failure; query strings redacted from logs
- Paths: `source_video.download` → `source_video.source` → `source_video.mp4`

## FFprobe validation

`FFPROBE_BINARY` via `subprocess` list argv (no shell). Requires one video stream, portrait orientation, duration within min/max and target±tolerance, finite positive FPS below max, pixels below max. Rotation metadata interpreted for displayed dimensions. Safe codes only; raw stderr never exposed.

## FFmpeg normalization

Prefer stream-copy remux into MP4 with `+faststart`. Fallback bounded H.264/`yuv420p` re-encode (`-an`). Explicit `-f mp4` for partial `.mp4.partial` outputs and `.source` inputs. Final file re-validated then published with `os.replace`.

## Storage and atomic publication

```text
storage/generated/{job_id}/source_video.download
storage/generated/{job_id}/source_video.source
storage/generated/{job_id}/source_video.mp4
```

On failure: remove partials/final; keep prompt, base, reference, and edited artifacts. If DB commit fails after publish: remove uncommitted final and mark failed (`MEDIA_REQUEST_FAILED`).

Persisted URL is local only: `/api/jobs/{job_id}/source-video/file`.

## API routes

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/jobs/{id}/generate-source-video` | Atomic claim → `202` |
| `GET` | `/api/jobs/{id}/source-video` | Metadata when `SOURCE_VIDEO_READY` |
| `GET` | `/api/jobs/{id}/source-video/file` | `video/mp4` + `Cache-Control: private, max-age=3600` |

## Tests

Offline suite in `tests/test_gate5_api.py` covering status/transitions, concurrent claim, base-not-edited proof, verified schema keys, download rules, ffprobe validation (mocked + real fixtures), normalization, endpoints, artifact preservation, and no Fun Control. Full suite: **247 passed**.

## Manual live smoke test

Not performed — local `.env` has empty `WAVESPEED_API_KEY`.

## Security checks

- No full prompts, uploaded URLs, provider output query strings, raw responses, API keys, or video bytes in application logs / persisted errors
- No private SDK methods
- No real network or paid calls in automated tests

## Database compatibility

Reuses existing `source_video_url`. No new columns. No database reset.

## Known limitations

- Duration acceptance is strict: must satisfy both min/max bounds and target±tolerance (defaults ≈ 4.65–5.35s)
- Normalization drops audio on H.264 fallback (`-an`); stream-copy preserves streams when remux succeeds
- Provider output must be MP4-compatible for `-f mp4` input hint on `.source` files

## Deferred work

- Fun Control / control video
- Transition detection
- Video merging
- Frontend

## Git information

- Starting commit: `fc520a632d97eca4186b870d606699a8b21794c4`
- Implementation commit: `0c173326f252b4b007db216e99dbb30bbcc18f47`
- Final HEAD: `5d7c493ab4994936682fbf6dfb44042d1f04263c`
