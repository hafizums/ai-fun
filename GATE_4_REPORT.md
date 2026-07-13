# Gate 4 Report

## Result

IMPLEMENTED — AWAITING GATEKEEPER REVIEW

## Starting state

- Starting commit: `554a3856b9cc77bd3d7552a365a001b167f4fc44`
- Branch: `master`
- Initial Git status: clean, tracking `origin/master`

## Status workflow

```text
BASE_IMAGE_READY
    → WAITING_FOR_REFERENCE
    → REFERENCE_READY
    → CHARACTER_EDITING
    → CHARACTER_EDIT_READY | FAILED (failed_stage = character_editing)
```

- Idle (not restart-failed): `WAITING_FOR_REFERENCE`, `REFERENCE_READY`, `CHARACTER_EDIT_READY`
- Deletable: `REFERENCE_READY`, `CHARACTER_EDIT_READY` (plus prior Gate idle/terminal statuses)
- Generic `FAILED → CHARACTER_EDITING` forbidden; retry only via atomic claim when `failed_stage == character_editing`

## Reference upload

- Multipart field `file` only; filename/MIME ignored
- Atomic SQL claim from `BASE_IMAGE_READY` or `REFERENCE_READY` → `WAITING_FOR_REFERENCE` before reading bytes; commit before stream
- Streamed to job-scoped temp with byte cap; PNG/JPEG/WebP only; EXIF orientation applied; metadata stripped
- Replacement sequence: normalize to staging → verify reservation → backup prior final → publish staging → DB commit → delete backup
- Rollback on failure restores backup bytes to `reference_image.png` and prior job status/path
- DB stores relative path `uploads/{job_id}/reference_image.png`

## Atomic upload claim design

- Conditional `UPDATE generation_jobs SET status='WAITING_FOR_REFERENCE' ... WHERE status IN ('BASE_IMAGE_READY','REFERENCE_READY')`
- Only `rowcount == 1` proceeds; competing upload/edit requests receive `409`
- Character edit atomic claim requires `REFERENCE_READY`, so edit is blocked while upload is reserved
- Upload re-checks `WAITING_FOR_REFERENCE` before file publication and before DB commit

## Backup and rollback sequence

Files under `uploads/{job_id}/`:

```text
reference_image.upload          # streaming partial
reference_image.staging.png     # normalized candidate
reference_image.backup.png      # prior final during replacement
reference_image.png             # published final
```

On replacement failure after backup move: restore backup → final, revert status to `REFERENCE_READY`, preserve `reference_image_path`. On initial-upload failure after publish: remove final, revert to `BASE_IMAGE_READY`.

## Upload/edit race prevention

- Upload holds `WAITING_FOR_REFERENCE` from claim until commit; edit cannot claim during reservation
- Edit claim from `CHARACTER_EDITING` blocks upload claim (`409`)
- Startup reconciliation restores idle status for stranded `WAITING_FOR_REFERENCE` jobs without deleting valid references

## Restart reconciliation

On startup, `reconcile_waiting_for_reference_jobs()`:

- **Backup-authoritative rule:** if `reference_image.backup.png` exists and validates, treat it as evidence of an interrupted replacement. Restore backup → final even when the current final is also a valid PNG (uncommitted candidate). Discard the candidate.
- Validate restored final before committing `REFERENCE_READY`.
- Remove upload/staging artifacts; delete backup only after successful restore validation.
- If restore fails: keep `WAITING_FOR_REFERENCE`, preserve backup when possible, set `REFERENCE_IMAGE_STORAGE_FAILED` — never falsely mark ready.
- Invalid backup: do not discard a valid final; drop only the bad backup.
- No backup + valid final → `REFERENCE_READY`.
- No backup + no valid final → `BASE_IMAGE_READY`, clear path, remove invalid final.

Interrupted replacement state covered:

```text
status = WAITING_FOR_REFERENCE
reference_image.png = valid new candidate
reference_image.backup.png = valid prior reference
```

Recovery sequence: validate backup → remove candidate → `os.replace(backup, final)` → validate restored final → commit `REFERENCE_READY` → remove partial/staging.

## Edit model configuration

- Model: `openai/gpt-image-2/edit`
- Fixed parameters (prompt omitted):

```json
{
  "prompt": "<validated edit_prompt>",
  "images": ["<uploaded base URL>", "<uploaded reference URL>"],
  "aspect_ratio": "9:16",
  "resolution": "1k",
  "quality": "medium",
  "output_format": "png",
  "enable_sync_mode": false,
  "enable_base64_output": false
}
```

## Provider input ordering

1. Upload base image (`generated/{job_id}/base_image.png`)
2. Upload reference image (`uploads/{job_id}/reference_image.png`)
3. `images[0]` = base, `images[1]` = reference

Public SDK only: `Client.upload`, `Client.run`. Media base URL: `WAVESPEED_API_BASE_URL`.

## Storage and atomic publication

- Reference staging → `os.replace` → `reference_image.png`
- Edited download → normalize → `os.replace` → `edited_image.png`
- Partials removed on failure; base/reference/prompt preserved on edit failure

## API routes

| Method | Path |
|--------|------|
| `POST` | `/api/jobs/{job_id}/reference-image` |
| `GET` | `/api/jobs/{job_id}/reference-image` |
| `GET` | `/api/jobs/{job_id}/reference-image/file` |
| `POST` | `/api/jobs/{job_id}/generate-character-edit` |
| `GET` | `/api/jobs/{job_id}/edited-image` |
| `GET` | `/api/jobs/{job_id}/edited-image/file` |

## Tests

- Commands: `pytest -q`; `ruff check app tests`
- Total: 227
- Passed: 227
- Failed: 0
- Skipped: 0
- Warnings: 1 (Starlette TestClient deprecation)

Concurrent claim: `test_concurrent_edit_claim_enqueues_once` — passed.

Concurrent upload: `test_concurrent_upload_claim_one_winner` — passed (claim barrier + reserved-state pause).

Upload/edit race: `test_edit_rejected_during_reserved_upload`, `test_upload_rejected_during_character_editing` — passed.

Rollback: commit-failure and post-publication restore tests — passed.

Backup-authoritative restart tests:

- `test_restart_reconcile_backup_authoritative_over_new_final`
- `test_restart_reconcile_backup_restores_when_final_missing`
- `test_restart_reconcile_backup_restores_when_final_corrupt`
- `test_restart_reconcile_backup_restore_failure_keeps_waiting`
- `test_startup_invokes_reference_reconciliation`

## Format-validation correction (Gate 4)

Correction starting HEAD: `5a1632a5bbb37c738997600badfebdf7fe81da28`.

### Finding

Reference replacement used `os.replace(staging, final)` before DB commit without backup/restore. A failure after publication could leave the prior reference lost. Upload reservation was not atomic, allowing edit/upload races.

### Correction

Atomic upload claim, backup/rollback publication, DB commit after file publish, startup reconciliation, and concurrency/race regression tests.

### Follow-up finding (backup-authoritative restart)

Starting HEAD: `2c4c60cf6b90d4069dc417c19de07065a4213caa`.

Startup reconciliation preferred a valid uncommitted candidate final over a valid backup, then deleted the backup — permanently discarding the prior reference after an interrupted replacement crash.

### Follow-up correction

When a valid backup exists for `WAITING_FOR_REFERENCE`, always restore it over the candidate final before marking `REFERENCE_READY`.

## Manual live smoke test

- Performed: no
- Reason: local `.env` has empty `WAVESPEED_API_KEY`

## Security checks

- No filename/MIME trust; streaming size limit; path via `StorageService`
- No full edit prompt / provider URL query / raw response / API key logging
- Genuine PNG required for published reference and edited artifacts
- Provider URLs never persisted as `edited_image_url` (local endpoint only)

## Database compatibility

- Reused existing `reference_image_path` and `edited_image_url` columns
- Added enum values `REFERENCE_READY` and `CHARACTER_EDIT_READY` (VARCHAR status; no SQLite migration required)
- No database reset

## Known limitations

- In-process tasks do not survive restarts.
- No automatic paid retry.
- Provider upload/output URLs are not exposed through the local API.
- No image-to-video, Fun Control, transition detection, merging, or frontend yet.

## Deferred work

- Gate 5+: image-to-video, Fun Control, transition detection, video merging, frontend

## Git information

- Implementation commit: `c9788ad146e87429ab66ca0198092a51ce327d06`
- Correction commit: `96c41c865f843434bab25cc859fd49cdf2e0fd24`
- Backup-authoritative restart correction: `ecb0b536aab5ce57f4da31e13626c01c69c4926e`
- Final HEAD: `abd9de98c0d4a1378b474cefa3c21ef2e9fa5883`
- Final Git status: clean working tree on `master`, synced with `origin/master`
