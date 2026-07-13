# Gate 8 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `4cea05c84201cc914ac29970c882b25082e7f7ff`
- Branch: `master`
- Gates 1–7 approved

## Frontend architecture

Vanilla HTML/CSS/JavaScript served by FastAPI — no React, Vite, npm, or second dev server.

```text
app/web/
  index.html
  styles.css
  workflow.js   # pure status/action helpers
  app.js        # UI shell, poller, API client
app/api/ui.py   # GET /, GET /jobs/{id}, /static mount, CSP middleware
```

## FastAPI integration

- `GET /` and `GET /jobs/{job_id}` return the same HTML shell
- `StaticFiles` mounted at `/static`
- Global headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`
- Strict UI CSP (`UI_CONTENT_SECURITY_POLICY`) only on `/`, `/jobs/*`, `/static/*`
- App version `0.8.0`; `/docs`, `/openapi.json`, `/api/*` unchanged

## Browser security

- No `innerHTML` for server strings (`textContent` / DOM create)
- No `eval` / `new Function` / remote scripts
- UI CSP: `default-src 'self'`; `img-src`/`media-src` allow `blob:`; no `unsafe-eval`

### Global-CSP finding (Gate 8 FAIL) and correction

**Finding:** The initial Gate 8 middleware attached the strict self-only CSP to every response, including `/docs`. Swagger UI loads CDN assets and an inline bootstrap script, so `/docs` could return HTTP 200 while remaining unusable in a browser.

**Correction:** Path-aware scoping:

| Route class | Strict UI CSP |
|-------------|---------------|
| `/`, `/jobs/{id}`, `/static/*` | Yes |
| `/docs`, `/redoc`, `/openapi.json`, `/api/*`, `/health` | No |

HTML shell responses also set the CSP explicitly via `_html_shell()`. The UI CSP was not weakened with `unsafe-inline`, `unsafe-eval`, `*`, or `https:`.

**`/docs` verification:** Header tests assert `/docs` has no UI CSP and still embeds Swagger markup; manual browser load confirms Swagger UI renders.
## Workflow and status mapping

Centralized in `workflow.js` (`AIFunWorkflow.STATUS_VIEW`):

| Status | Step | Primary action |
|--------|------|----------------|
| DRAFT | prompt | generate-prompts |
| PROMPT_READY | base-image | generate-base-image |
| BASE_IMAGE_READY | reference | upload-reference |
| REFERENCE_READY | character-edit | generate-character-edit (+ replace reference) |
| CHARACTER_EDIT_READY | source-motion | generate-source-video |
| SOURCE_VIDEO_READY | motion-transfer | generate-controlled-video |
| CONTROL_VIDEO_READY | final-video | assemble-final-video |
| COMPLETED | final-video | download-final |
| FAILED | failed stage | eligible retry only |

Backend status remains authoritative. `409` refreshes the job; no automatic POST retry.

## Paid-stage confirmation

Native `<dialog>` before paid POSTs:

> This starts a paid AI generation request. It will not retry automatically.

Final assembly labeled: **Local processing — no provider charge**.

## Polling design

- Single timer; one `AbortController` at a time
- Active states polled ~1.75s
- Idle/ready/COMPLETED/FAILED stop polling
- Network backoff 2s → 4s → 8s capped
- Resume on `visibilitychange`
- Page load never starts paid work

## Reference upload UX

Drag/drop + file chooser, local object-URL preview (revoked after use), `FormData` field `file`. While `WAITING_FOR_REFERENCE`, editing/replacement disabled. On `REFERENCE_READY`, **Replace reference image** with warning that edit does not auto-rerun.

## Artifact previews

Cards for base, reference, edit, source, controlled, final. Media from local `/api/jobs/.../file` URLs with `?v=<updated_at>` cache-bust only when the job updates. Metadata fetched lazily for the completed final view.

## Failure and retry UX

Shows safe `error_code`, `error_message`, `failed_stage`, and **Copy error code**. Retry mapped to exact backend stage constants. No raw stderr/paths/tracebacks.

## Final result and download

On `COMPLETED`: large `<video controls playsinline preload="metadata">`, transition method/confidence, no-audio note, download via `/api/jobs/{id}/final-video/file`.

## Recent projects

List via `GET /api/jobs?limit=10&offset=…`, open/delete with confirmation, respects `409` for non-deletable states.

## Responsive behavior

Desktop two-column workspace; tablet/mobile stack with compact stepper. Touch targets ≥44px. CSS breakpoints at 900px / 560px.

## Accessibility

Semantic regions, labels, `aria-live`, native dialogs, visible `:focus-visible`, `prefers-reduced-motion`, meaningful image alts.

## API usage

Uses existing Gate 1–7 endpoints only. No provider calls from the browser. No frontend-supplied model names or generation parameters.

## Tests

- `tests/test_gate8_ui.py` — HTML/static/scoped CSP headers/contracts
- `tests/test_gate8_workflow.py` + `tests/js/workflow.test.mjs` — JS syntax and pure mapping
- Full suite regression: Gates 1–7 retained
- CSP scope: UI routes have strict CSP; `/docs`, `/openapi.json`, `/api/*`, `/health` do not

## Manual QA

### Desktop (browser automation at 127.0.0.1:8000)

- `/` loads empty state with stepper and health chips
- **New project** creates job, updates URL to `/jobs/{id}`, shows prompt form
- **Generate prompts** opens paid confirmation dialog (cancelled — no paid run)
- Refresh-restore path supported via URL + optional `last_job_id` in localStorage
- `/docs` loads Swagger UI after CSP scoping correction (no restrictive UI CSP on docs)

### Mobile (390×844)

Layout CSS verified for narrow breakpoints (stepper wraps, workspace stacks). Full device lab pass not performed in this session; no horizontal-overflow rules rely on `overflow-x: hidden` on `body`.

### Live paid end-to-end

**Not performed** — provider not configured in the local smoke environment (`Provider not configured`). No live WaveSpeed/LLM calls from the UI.

## Security checks

- Frontend files scanned: no `WAVESPEED_API_KEY`, `sk-`, `eval(`, `new Function`, `unsafe-eval`, or remote `http(s)://` asset loads
- UI page load does not invoke the media provider
- No automatic paid chaining or paid retry
- Strict UI CSP excludes `unsafe-eval` and remote asset permissions
- Documentation/API routes are not covered by the UI CSP

## Known limitations

- No offline PWA / service worker
- Prompt generation confirm appears even when provider is unconfigured (backend still rejects safely)
- Artifact cards do not fetch per-item metadata on every poll (by design)
- Node is required only for optional JS unit checks, not for app startup

## Deferred production work

Auth, deployment hardening, cloud storage, billing, analytics, multi-user support (Gate 9+)

## Git information

- Starting HEAD (Gate 8): `4cea05c84201cc914ac29970c882b25082e7f7ff`
- Implementation commit: `e49ea2c0ad0c0207a0380e28c6b98932ec28ab1c`
- Correction starting HEAD: `b700975af18bdb8272680e6314901a4c5eba2728`
- Correction commit: `3afc6d3dc258475a619693fe2ee1f6beb2024157`
- Final HEAD: `3afc6d3dc258475a619693fe2ee1f6beb2024157`
- Validation: `312 passed`; Ruff clean; Node workflow tests ok
