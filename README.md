# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 2** adds asynchronous structured prompt generation via WaveSpeed’s OpenAI-compatible GPT-5.1 endpoint. Gate 1 foundation (FastAPI, SQLite, local storage, task runner, media provider abstraction) remains.

This gate does **not** generate images or videos.

## Requirements

- Python 3.11+
- FFmpeg and ffprobe on `PATH` (or set `FFMPEG_BINARY` / `FFPROBE_BINARY`)
- Optional: WaveSpeed API key for live LLM smoke tests ([access key](https://wavespeed.ai/accesskey))

## PowerShell setup

```powershell
cd "C:\Users\froxt\Downloads\AI FUN MOTION"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
# Edit .env and set WAVESPEED_API_KEY for live LLM calls.
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
| `LOCAL_TASK_WORKERS` | `1` | ThreadPoolExecutor size |
| `FFMPEG_BINARY` / `FFPROBE_BINARY` | `ffmpeg` / `ffprobe` | Media tooling |

Never commit a real `.env` or API key.

## API

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/health` | Local dependency diagnostics (no paid WaveSpeed call) |
| `POST` | `/api/jobs` | Create job in `DRAFT` |
| `GET` | `/api/jobs` | List jobs newest first |
| `GET` | `/api/jobs/{id}` | Get one job |
| `DELETE` | `/api/jobs/{id}` | Delete `DRAFT` / `PROMPT_READY` / `COMPLETED` / `FAILED` |
| `POST` | `/api/jobs/{id}/generate-prompts` | Accept async prompt generation (`202`) |
| `GET` | `/api/jobs/{id}/prompts` | Typed prompt envelope when `PROMPT_READY` |
| `POST` | `/api/settings/test-wavespeed` | Configuration-only media WaveSpeed check |

### Prompt generation flow

`DRAFT` → `POST .../generate-prompts` → `PROMPT_GENERATING` → background GPT-5.1 → `PROMPT_READY`

Failed prompt jobs (`failed_stage == prompt_generation`) may retry via the same endpoint.

## Architecture notes

- **Task runner**: in-process `ThreadPoolExecutor`. Tasks are **not** persisted across process restarts. On startup, active processing jobs are marked `FAILED` with `error_code=APP_RESTARTED`.
- **Database**: SQLite via SQLAlchemy. `create_all` bootstrap; Gate 2 adds `PROMPT_READY` with no SQLite migration (status is `VARCHAR(24)` without CHECK).
- **Storage**: `storage/uploads`, `generated`, `temporary`, `final` with path-traversal protection.
- **Media WaveSpeed**: public SDK `Client.upload` / `Client.run` against `WAVESPEED_API_BASE_URL` (not called in Gate 2 routes).
- **LLM**: official `openai` client against `WAVESPEED_LLM_BASE_URL` (`chat.completions.create`, model `openai/gpt-5.1`). JSON is prompt-enforced and locally validated (no `response_format` assumption).

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
```

Automated tests use fake LLM providers and never make paid network requests.

## Hard constraints

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, or authentication.
