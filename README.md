# AI Fun Motion

Local personal-use AI video transformation application.

**Gate 4** adds local reference-image upload and asynchronous character-only replacement via WaveSpeed’s GPT Image 2 Edit model. Gates 1–3 remain unchanged in behavior.

This gate does **not** generate video, detect transitions, merge clips, or ship a frontend.

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

See `.env.example`. Gate 4 additions:

| Variable | Default | Purpose |
|----------|---------|---------|
| `WAVESPEED_CHARACTER_EDIT_MODEL` | `openai/gpt-image-2/edit` | Fixed edit model (config only) |
| `WAVESPEED_CHARACTER_EDIT_ASPECT_RATIO` | `9:16` | Fixed aspect ratio |
| `WAVESPEED_CHARACTER_EDIT_RESOLUTION` | `1k` | Fixed resolution |
| `WAVESPEED_CHARACTER_EDIT_QUALITY` | `medium` | Fixed quality |
| `WAVESPEED_CHARACTER_EDIT_OUTPUT_FORMAT` | `png` | Fixed output format |
| `REFERENCE_IMAGE_MAX_UPLOAD_MB` | `15` | Streaming upload byte cap |
| `REFERENCE_IMAGE_MAX_PIXELS` | `25000000` | Max reference pixels |
| `REFERENCE_IMAGE_MIN_WIDTH` / `MIN_HEIGHT` | `256` | Minimum reference dimensions |

Media upload/edit use `WAVESPEED_API_BASE_URL` only — never the LLM base URL.

## API (Gate 4)

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/api/jobs/{id}/reference-image` | Multipart `file` upload → `REFERENCE_READY` |
| `GET` | `/api/jobs/{id}/reference-image` | Reference metadata |
| `GET` | `/api/jobs/{id}/reference-image/file` | Local normalized PNG |
| `POST` | `/api/jobs/{id}/generate-character-edit` | Accept async edit (`202`) |
| `GET` | `/api/jobs/{id}/edited-image` | Edited metadata when ready |
| `GET` | `/api/jobs/{id}/edited-image/file` | Local edited PNG |

### Workflow

`BASE_IMAGE_READY` → upload reference → `WAITING_FOR_REFERENCE` → `REFERENCE_READY` → `POST .../generate-character-edit` → `CHARACTER_EDITING` → `CHARACTER_EDIT_READY`

Eligible edit failures (`failed_stage == character_editing`) may retry via the same generate endpoint.

Storage:

- Reference: `storage/uploads/{job_id}/reference_image.png`
- Edited: `storage/generated/{job_id}/edited_image.png`

## Architecture notes

- **Task runner**: in-process `ThreadPoolExecutor`. Restart marks `CHARACTER_EDITING` (and other active states) failed; idle states including `WAITING_FOR_REFERENCE`, `REFERENCE_READY`, and `CHARACTER_EDIT_READY` are preserved.
- **Edit flow**: upload base + reference via public `Client.upload`, one `Client.run` on `openai/gpt-image-2/edit` with `images: [base, reference]`.
- **No LLM** during reference upload or character edit.

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
ruff check app tests
```

Automated tests use fake media/LLM providers and never make paid network requests.

## Hard constraints

No Docker, Redis, Celery/RQ/Dramatiq, PostgreSQL, cloud storage, or authentication.
