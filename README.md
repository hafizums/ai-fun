# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 1** delivers the application foundation only: FastAPI, SQLite, SQLAlchemy, local filesystem storage, an in-process task runner, WaveSpeed provider abstraction, FFmpeg/ffprobe detection, and automated tests.

Gate 1 does **not** generate images or videos.

## Requirements

- Python 3.11+
- FFmpeg and ffprobe on `PATH` (or set `FFMPEG_BINARY` / `FFPROBE_BINARY`)
- Optional: WaveSpeed API key for later gates ([access key](https://wavespeed.ai/accesskey))

## PowerShell setup

```powershell
cd "C:\Users\froxt\Downloads\AI FUN MOTION"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
# Edit .env and set WAVESPEED_API_KEY when you need provider access (later gates).
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
| `WAVESPEED_API_KEY` | empty | Provider credential (never commit) |
| `WAVESPEED_LLM_BASE_URL` | `https://api.wavespeed.ai` | Provider base URL |
| `LOCAL_TASK_WORKERS` | `1` | ThreadPoolExecutor size |
| `FFMPEG_BINARY` / `FFPROBE_BINARY` | `ffmpeg` / `ffprobe` | Media tooling |

Never commit a real `.env` or API key.

## API (Gate 1)

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/health` | Local dependency diagnostics (no paid WaveSpeed call) |
| `POST` | `/api/jobs` | Create job in `DRAFT` |
| `GET` | `/api/jobs` | List jobs newest first |
| `GET` | `/api/jobs/{id}` | Get one job |
| `DELETE` | `/api/jobs/{id}` | Delete `DRAFT` / `COMPLETED` / `FAILED` only |
| `POST` | `/api/settings/test-wavespeed` | Configuration-only WaveSpeed check |

## Architecture notes

- **Task runner**: in-process `ThreadPoolExecutor`. Tasks are **not** persisted across process restarts. On startup, active processing jobs are marked `FAILED` with `error_code=APP_RESTARTED`.
- **Database**: SQLite via SQLAlchemy. Gate 1 uses controlled `create_all`. Introduce schema migrations before material schema evolution.
- **Storage**: `storage/uploads`, `generated`, `temporary`, `final` with path-traversal protection.
- **WaveSpeed**: adapter wraps verified SDK methods `Client.upload` and `Client.run`. Generation is not called from Gate 1 routes.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
```

## Hard constraints (this project)

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, or authentication in Gate 1.
