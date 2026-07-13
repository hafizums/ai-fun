# Gate 7 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `b596fca5510a4d76b2754cb45bad0e788071f183`
- Branch: `master`
- Gates 1–6 approved

## Status workflow

```text
CONTROL_VIDEO_READY
    → FINAL_VIDEO_ASSEMBLING
    → COMPLETED | FAILED (failed_stage = final_video_assembly)
```

- Idle / deletable: `COMPLETED`
- Restart-failed: `FINAL_VIDEO_ASSEMBLING`
- Generic `FAILED → FINAL_VIDEO_ASSEMBLING` forbidden
- Retry only when `failed_stage == final_video_assembly`

## Transition detector

Lightweight local detector (`app/services/transition_detector.py`):

1. FFmpeg extracts grayscale frames at configured FPS, scaled to ~96px wide (max 200 frames).
2. Numpy mean-absolute frame difference plus upper-middle central-region weighting.
3. Short 3-tap temporal smoothing.
4. Peak search inside the configured window; confidence = normalized peak prominence in `[0, 1]`.

Methods:

- `motion_peak` — strongest smoothed score in window with confidence ≥ threshold
- `midpoint_fallback` — flat scores, too few frames, extraction failure, or low confidence

Does **not** claim semantic hand/face detection.

## Search window and fallback

Defaults:

- Search: `35%–70%` of duration
- Edge clamp: `≥ 0.75s` from start/end
- Confidence threshold: `0.08`
- Analysis FPS: `8`

Fallback to clamped midpoint when detection is inconclusive. Temporary frames are always deleted.

## Assembly timeline

```text
source [0 → transition]  +  controlled [transition−crossfade → end]
→ short xfade (0.12s) → final MP4
```

**Final duration remains approximately one clip length (~5s), not the sum of both clips.**  
xfade overlaps the cut so output duration ≈ `control_duration` ≈ `source_duration`.

## Audio policy

Final output contains **no audio**. Assembly uses `-an` explicitly. Input clip audio is ignored to avoid sync complexity.

## FFmpeg command design

- Argument arrays only (`shell=False`)
- Bounded timeout (`FINAL_VIDEO_FFMPEG_TIMEOUT_SECONDS`)
- Filter graph: `trim` + `setpts` + `xfade` (or hard `concat` if clips are too short for fade)
- Encode: `libx264`, `yuv420p`, `+faststart`, `-an`
- Atomic publish: `final_video.assembling.mp4` → `os.replace` → `final_video.mp4`

## Input compatibility checks

Before assembly both inputs must be genuine portrait videos with compatible duration (≤ `0.35s` delta) and dimensions (≤ `8px` delta). Rotation metadata is interpreted consistently with Gate 5/6 probing.

Error codes include:

- `SOURCE_VIDEO_MISSING_OR_INVALID`
- `CONTROL_VIDEO_MISSING_OR_INVALID`
- `VIDEO_INPUT_DURATION_MISMATCH`
- `VIDEO_INPUT_DIMENSION_MISMATCH`
- `TRANSITION_ANALYSIS_FAILED` / `FINAL_VIDEO_ASSEMBLY_FAILED` / `FINAL_VIDEO_INVALID_*`
- `FFMPEG_NOT_AVAILABLE` / `FFPROBE_NOT_AVAILABLE`

Raw stderr is never exposed.

## Storage and atomic publication

```text
storage/generated/{job_id}/source_video.mp4
storage/generated/{job_id}/controlled_video.mp4
storage/final/{job_id}/final_video.assembling.mp4
storage/final/{job_id}/final_video.mp4
storage/final/{job_id}/transition.json
storage/temporary/{job_id}/   # analysis frames (deleted)
```

DB stores only relative `final/{job_id}/final_video.mp4` and finite `transition_time_seconds`.  
Method/confidence live in `transition.json` (canonical JSON, no raw scores).  
On DB commit failure after publish, the uncommitted final is removed.

## API routes

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/api/jobs/{id}/assemble-final-video` | `202` accept; `404`/`409`/`500` |
| `GET` | `/api/jobs/{id}/transition` | Completed sidecar metadata |
| `GET` | `/api/jobs/{id}/final-video` | Completed probe metadata |
| `GET` | `/api/jobs/{id}/final-video/file` | `video/mp4`, safe filename |

## Tests

- `tests/test_transition_detector.py` — synthetic scores, edge bounds, cleanup, timeout, stderr safety
- `tests/test_gate7_api.py` — claim/concurrency, integrity, assembly, endpoints, commit-failure cleanup
- Full suite: Gates 1–6 regression retained

## Manual smoke test

**Not performed** — no live Gate 5/6 artifacts were present under `storage/generated/` at validation time.

## Security checks

- No provider/LLM/network calls in Gate 7
- No user-controlled path components
- Paths via `StorageService`
- No raw FFmpeg/ffprobe stderr in API/logs payloads
- Temporary analysis and assembling artifacts removed on success and failure
- Source/controlled videos preserved on assembly failure

## Database compatibility

No schema reset. Uses existing `final_video_path` and `transition_time_seconds`. Added status enum value `FINAL_VIDEO_ASSEMBLING` (SQLite non-native enum).

## Known limitations

- Detector is motion-energy based, not true occlusion/face recognition
- Crossfade may slightly soften the cut; hard-cut path exists for very short clips
- Flat synthetic fixtures typically exercise midpoint fallback unless scores are injected
- Final FPS follows encoder defaults of the filter graph (validated against max FPS)

## Deferred work

- Gate 8 / frontend
- Production hardening, auth, cloud storage
- Heavier ML-based transition detection

## Git information

- Starting HEAD: `b596fca5510a4d76b2754cb45bad0e788071f183`
- Implementation commit: `5c056e2fd35c56e9fdf877ca36e2b71edc874c4e`
- Final HEAD: `026496c2d05295b42340d9a9f93980191a624195`
