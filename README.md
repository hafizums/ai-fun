# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 8** adds a thin local web UI over the existing FastAPI workflow (Gates 1–7). The backend remains authoritative for eligibility, retries, progress, errors, and artifacts.

## Requirements

- Python 3.11+
- FFmpeg and ffprobe on `PATH` (or set `FFMPEG_BINARY` / `FFPROBE_BINARY`)
- Optional: WaveSpeed API key for live media/LLM stages ([access key](https://wavespeed.ai/accesskey))
- WaveSpeed SDK: `wavespeed>=1.0.9,<1.1`
- Optional for JS unit checks: Node.js on `PATH` (Python app starts without Node)

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

Open http://127.0.0.1:8000/ — API docs remain at http://127.0.0.1:8000/docs

No second frontend server. No npm build step.

## UI

| Route | Behavior |
|-------|----------|
| `GET /` | Application shell |
| `GET /jobs/{id}` | Same shell; client restores the job from the API |
| `GET /static/*` | CSS/JS assets |

Workflow: create project → prompts → base image → reference → character edit → source motion → controlled video → local final assembly → download.

Paid stages require an explicit confirmation click. Final assembly is labeled local (no provider charge). The browser never calls WaveSpeed or the LLM directly.

## Configuration

See `.env.example`. Gate 8 adds no required UI-specific settings.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
node --check app/web/workflow.js
node --check app/web/app.js
node tests/js/workflow.test.mjs
```

## Hard constraints

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, authentication, or external frontend hosting.
