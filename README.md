# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 7** assembles the final local video by splicing Gate 5 source motion with Gate 6 controlled character video at a detected transition point. Gates 1–6 remain unchanged in behavior.

This gate does **not** ship a frontend or production hardening.

## Requirements

- Python 3.11+
- FFmpeg and ffprobe on `PATH` (or set `FFMPEG_BINARY` / `FFPROBE_BINARY`)
- Optional: WaveSpeed API key for live media/LLM smoke tests ([access key](https://wavespeed.ai/accesskey))
- WaveSpeed SDK: `wavespeed>=1.0.9,<1.1`
- Numpy (bounded) for lightweight frame-difference transition analysis

## PowerShell setup

```powershell
cd "C:\Users\froxt\Downloads\AI FUN MOTION"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
# Edit .env and set WAVESPEED_API_KEY for live provider calls.
```

## Start (local only)

```bat
run.bat
```

Or:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Binds to `127.0.0.1` only. Docs: http://127.0.0.1:8000/docs

## Configuration

See `.env.example`. Gate 7 additions:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRANSITION_ANALYSIS_FPS` | `8` | Downscaled grayscale analysis FPS |
| `TRANSITION_SEARCH_START_RATIO` | `0.35` | Search window start |
| `TRANSITION_SEARCH_END_RATIO` | `0.70` | Search window end |
| `TRANSITION_MIN_SECONDS_FROM_EDGE` | `0.75` | Clamp away from clip edges |
| `TRANSITION_CONFIDENCE_THRESHOLD` | `0.08` | Below → midpoint fallback |
| `TRANSITION_CROSSFADE_SECONDS` | `0.12` | Short visual crossfade |
| `FINAL_VIDEO_MAX_*` / input deltas | see `.env.example` | Final validation and compatibility |

Gate 7 is entirely local: no WaveSpeed, LLM, uploads, or network.

## API (Gate 7)

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/jobs/{id}/assemble-final-video` | Accept async local assembly (`202`) |
| `GET` | `/api/jobs/{id}/transition` | Transition method/confidence when completed |
| `GET` | `/api/jobs/{id}/final-video` | Final metadata when completed |
| `GET` | `/api/jobs/{id}/final-video/file` | Local MP4 (`video/mp4`) |

### Workflow

`CONTROL_VIDEO_READY` → `POST .../assemble-final-video` → `FINAL_VIDEO_ASSEMBLING` → `COMPLETED`

**Inputs:** `source_video.mp4` + `controlled_video.mp4`. **Output:** `final/{job_id}/final_video.mp4` (~one clip duration, no audio).

## Architecture notes

- Motion-energy peak in the middle search window; midpoint fallback when inconclusive.
- Source before transition, controlled after; short `xfade`; H.264 / yuv420p / faststart / `-an`.
- Atomic publish under `storage/final/{job_id}/`; transition sidecar `transition.json`.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
```

Automated tests use fake media/LLM providers and never make paid network requests.

## Hard constraints

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, or authentication.
