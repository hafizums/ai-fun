# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 5** generates a five-second source motion video from the original Gate 3 base image using WaveSpeed Wan 2.2 I2V. Gates 1–4 remain unchanged in behavior.

This gate does **not** run Fun Control, detect transitions, merge clips, or ship a frontend.

## Requirements

- Python 3.11+
- FFmpeg and ffprobe on `PATH` (or set `FFMPEG_BINARY` / `FFPROBE_BINARY`)
- Optional: WaveSpeed API key for live media/LLM smoke tests ([access key](https://wavespeed.ai/accesskey))

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

See `.env.example`. Gate 5 additions:

| Variable | Default | Purpose |
|----------|---------|---------|
| `WAVESPEED_SOURCE_VIDEO_MODEL` | `wavespeed-ai/wan-2.2/i2v-480p-ultra-fast` | Fixed I2V model |
| `WAVESPEED_SOURCE_VIDEO_DURATION_SECONDS` | `5` | Duration (`5` or `8`; MVP uses `5`) |
| `WAVESPEED_SOURCE_VIDEO_SEED` | `-1` | `-1` = provider-random; non-negative = fixed |
| `SOURCE_VIDEO_DOWNLOAD_TIMEOUT_SECONDS` | `300` | HTTPS download timeout |
| `SOURCE_VIDEO_MAX_DOWNLOAD_MB` | `100` | Streaming byte cap |
| `SOURCE_VIDEO_MIN/MAX_DURATION_SECONDS` | `4` / `7` | Accepted duration bounds |
| `SOURCE_VIDEO_DURATION_TOLERANCE_SECONDS` | `0.35` | Must be near target duration |
| `SOURCE_VIDEO_MIN_WIDTH` / `MIN_HEIGHT` | `240` / `400` | Minimum portrait dimensions |
| `SOURCE_VIDEO_MAX_PIXELS` | `5000000` | Max width×height |
| `SOURCE_VIDEO_MAX_FPS` | `60` | Maximum frame rate |

I2V uses `WAVESPEED_API_BASE_URL` only — never the LLM base URL. No model parameters are accepted from request bodies.

## API (Gate 5)

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/jobs/{id}/generate-source-video` | Accept async I2V (`202`) |
| `GET` | `/api/jobs/{id}/source-video` | Local metadata when ready |
| `GET` | `/api/jobs/{id}/source-video/file` | Local MP4 (`video/mp4`) |

### Workflow

`CHARACTER_EDIT_READY` → `POST .../generate-source-video` → `SOURCE_VIDEO_GENERATING` → `SOURCE_VIDEO_READY`

Eligible source failures (`failed_stage == source_video_generation`) may retry via the same generate endpoint.

**Source image:** only `storage/generated/{job_id}/base_image.png` is uploaded and animated. `edited_image.png` is validated as Gate 4 integrity proof and is never used as I2V input.

Storage:

- Final: `storage/generated/{job_id}/source_video.mp4`
- Partials: `source_video.download`, `source_video.source` (removed on success/failure)

## Architecture notes

- **Task runner**: in-process `ThreadPoolExecutor`. Restart marks `SOURCE_VIDEO_GENERATING` (and other active states) failed; idle `SOURCE_VIDEO_READY` is preserved and deletable.
- **I2V flow**: upload original base image once via public `Client.upload`, one `Client.run` on Wan 2.2 I2V with verified schema fields.
- **No LLM**, no character-edit call, and no Fun Control during source-video generation.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
```

Automated tests use fake media/LLM providers and never make paid network requests.

## Hard constraints

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, or authentication.
