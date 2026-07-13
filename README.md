# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 3** adds asynchronous vertical base-image generation from the Gate 2 `image_prompt` via WaveSpeed’s media API. The artifact is downloaded, verified, normalized to PNG, and served locally. Gates 1–2 remain unchanged in behavior.

This gate does **not** upload reference images, edit characters, generate video, or ship a frontend.

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

Personal-use launcher (no reload — preserves in-process tasks):

```bat
run.bat
```

Or manually:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The app binds to `127.0.0.1` only and is not exposed to the LAN.

Development reload launcher (separate): `run.dev.bat`. Reloading can interrupt in-process tasks — prefer `run.bat` for real local runs.

Open interactive docs at http://127.0.0.1:8000/docs

## Configuration

See `.env.example`. Important variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | SQLite in project dir | SQLAlchemy database |
| `STORAGE_ROOT` | `./storage` | Local artifact root |
| `WAVESPEED_API_KEY` | empty | Shared credential for media SDK and LLM (never commit) |
| `WAVESPEED_API_BASE_URL` | `https://api.wavespeed.ai` | Media generation / upload SDK base URL |
| `WAVESPEED_LLM_BASE_URL` | `https://llm.wavespeed.ai/v1` | OpenAI-compatible LLM base URL |
| `WAVESPEED_LLM_MODEL` | `openai/gpt-5.1` | Chat model for prompt generation |
| `WAVESPEED_LLM_TIMEOUT_SECONDS` | `120` | LLM request timeout (1–600) |
| `WAVESPEED_BASE_IMAGE_MODEL` | `openai/gpt-image-2/text-to-image` | Fixed base-image model (config only) |
| `WAVESPEED_BASE_IMAGE_ASPECT_RATIO` | `9:16` | Fixed aspect ratio |
| `WAVESPEED_BASE_IMAGE_RESOLUTION` | `1k` | Fixed resolution |
| `WAVESPEED_BASE_IMAGE_QUALITY` | `medium` | Fixed quality |
| `WAVESPEED_BASE_IMAGE_OUTPUT_FORMAT` | `png` | Fixed output format |
| `WAVESPEED_MEDIA_TIMEOUT_SECONDS` | `600` | Media `Client.run` timeout |
| `WAVESPEED_MEDIA_POLL_INTERVAL_SECONDS` | `1` | Media poll interval |
| `BASE_IMAGE_DOWNLOAD_TIMEOUT_SECONDS` | `120` | HTTPS download timeout |
| `BASE_IMAGE_MAX_DOWNLOAD_MB` | `25` | Download byte cap |
| `BASE_IMAGE_MAX_PIXELS` | `25000000` | Max width×height after decode |
| `LOCAL_TASK_WORKERS` | `1` | ThreadPoolExecutor size |
| `FFMPEG_BINARY` / `FFPROBE_BINARY` | `ffmpeg` / `ffprobe` | Media tooling |

Never commit a real `.env` or API key. Media settings use `WAVESPEED_API_BASE_URL` only — never the LLM base URL.

## API

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/health` | Local dependency diagnostics (no paid WaveSpeed call) |
| `POST` | `/api/jobs` | Create job in `DRAFT` |
| `GET` | `/api/jobs` | List jobs newest first |
| `GET` | `/api/jobs/{id}` | Get one job |
| `DELETE` | `/api/jobs/{id}` | Delete idle/deletable jobs (`DRAFT`, `PROMPT_READY`, `BASE_IMAGE_READY`, `COMPLETED`, `FAILED`) |
| `POST` | `/api/jobs/{id}/generate-prompts` | Accept async prompt generation (`202`) |
| `GET` | `/api/jobs/{id}/prompts` | Typed prompt envelope when `PROMPT_READY` |
| `POST` | `/api/jobs/{id}/generate-base-image` | Accept async base-image generation (`202`) |
| `GET` | `/api/jobs/{id}/base-image` | Metadata when `BASE_IMAGE_READY` |
| `GET` | `/api/jobs/{id}/base-image/file` | Local PNG file when `BASE_IMAGE_READY` |
| `POST` | `/api/settings/test-wavespeed` | Configuration-only media WaveSpeed check |

### Prompt generation flow (Gate 2)

`DRAFT` → `POST .../generate-prompts` → `PROMPT_GENERATING` → background GPT-5.1 → `PROMPT_READY`

Failed prompt jobs (`failed_stage == prompt_generation`) may retry via the same endpoint.

### Base-image generation flow (Gate 3)

`PROMPT_READY` → `POST .../generate-base-image` → `BASE_IMAGE_GENERATING` → WaveSpeed media + local download → `BASE_IMAGE_READY`

Failed base-image jobs (`failed_stage == base_image_generation`) may retry via the same endpoint. Prompt-generation failures are not eligible.

Artifact path: `storage/generated/{job_id}/base_image.png` (served only via the local file endpoint; provider URLs are never exposed).

## Architecture notes

- **Task runner**: in-process `ThreadPoolExecutor`. Tasks are **not** persisted across process restarts. On startup, active processing jobs are marked `FAILED` with `error_code=APP_RESTARTED`.
- **Database**: SQLite via SQLAlchemy. `create_all` bootstrap; status is `VARCHAR(24)` without CHECK.
- **Storage**: `storage/uploads`, `generated`, `temporary`, `final` with path-traversal protection. Base images publish atomically under `generated/{job_id}/`.
- **Media WaveSpeed**: public SDK `Client.upload` / `Client.run` against `WAVESPEED_API_BASE_URL`. Gate 3 calls `run` once per accepted attempt with fixed model parameters.
- **LLM**: official `openai` client against `WAVESPEED_LLM_BASE_URL` (`chat.completions.create`, model `openai/gpt-5.1`). JSON is prompt-enforced and locally validated (no `response_format` assumption).
- **Images**: Pillow verifies and re-encodes to PNG (portrait ~9:16, no animation, size/pixel caps).

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
```

Automated tests use fake LLM/media providers and `httpx.MockTransport`. They never make paid network requests.

## Hard constraints

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, or authentication.
