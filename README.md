# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 6** transfers Gate 5 source motion onto the Gate 4 edited character via WaveSpeed Wan 2.2 Fun Control. Gates 1–5 remain unchanged in behavior.

This gate does **not** detect transitions, merge clips, or ship a frontend.

## Requirements

- Python 3.11+
- FFmpeg and ffprobe on `PATH` (or set `FFMPEG_BINARY` / `FFPROBE_BINARY`)
- Optional: WaveSpeed API key for live media/LLM smoke tests ([access key](https://wavespeed.ai/accesskey))
- WaveSpeed SDK: `wavespeed>=1.0.9,<1.1`

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

See `.env.example`. Gate 6 additions:

| Variable | Default | Purpose |
|----------|---------|---------|
| `WAVESPEED_CONTROL_VIDEO_MODEL` | `wavespeed-ai/wan-2.2/fun-control` | Fixed Fun Control model |
| `WAVESPEED_CONTROL_VIDEO_DURATION_SECONDS` | `5` | Local validation target only (schema has no duration field) |
| `WAVESPEED_CONTROL_VIDEO_RESOLUTION` | `480p` | Model `resolution` (`480p` or `720p`) |
| `WAVESPEED_CONTROL_VIDEO_SEED` | `-1` | `-1` = provider-random |
| `CONTROL_VIDEO_*` download/validation bounds | see `.env.example` | Download and ffprobe limits |

Uses `WAVESPEED_API_BASE_URL` only. No model parameters from request bodies.

## API (Gate 6)

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/jobs/{id}/generate-controlled-video` | Accept async Fun Control (`202`) |
| `GET` | `/api/jobs/{id}/controlled-video` | Local metadata when ready |
| `GET` | `/api/jobs/{id}/controlled-video/file` | Local MP4 (`video/mp4`) |

### Workflow

`SOURCE_VIDEO_READY` → `POST .../generate-controlled-video` → `CONTROL_VIDEO_GENERATING` → `CONTROL_VIDEO_READY`

**Inputs:** `edited_image.png` (identity) + `source_video.mp4` (motion). Base/reference images are never uploaded.

## Architecture notes

- **Paid generation retries:** dedicated generation client with `max_retries=0` and `max_connection_retries=0`; `run_model` always passes `max_task_retries=0`.
- **Uploads:** edited image first, source video second; one Fun Control `run`.
- **No LLM**, no base-image, edit, or I2V call during this stage.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
```

Automated tests use fake media/LLM providers and never make paid network requests.

## Hard constraints

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, or authentication.
