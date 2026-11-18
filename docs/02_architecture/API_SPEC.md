---
id: api-spec
owner: orchestrator
status: approved-v2
note: human-readable contract; T-0.3 converts it to contracts/openapi.yaml
---
# REST API (`/api/v1`) and SSE

## Conventions
- JSON, UTF-8. Times in seconds (float) for media, ISO-8601 UTC for timestamps.
- Errors: `{ "error": { "code": "LOGIN_REQUIRED", "message": "...", "details": {}, "retryable": true } }` with proper HTTP status.
- Pagination: `?limit=&cursor=` -> `{ items: [], next_cursor }`.
- Auth: public endpoints open on localhost; `/admin/*` requires session cookie (see ADMIN_PANEL_SPEC).
- Idempotency: `POST /jobs` accepts `Idempotency-Key`.

## Jobs
| Method | Path | Body / notes |
|---|---|---|
| POST | `/jobs` | `{ source:{kind:url|upload|drive, url?, upload_id?, drive_file_id?}, language:auto|en|hi|hinglish|bn, script_pref:auto|latn|native, aspect:9:16|1:1|4:5, preset_id, auto_mode, max_clips, clip_brief?, rights_confirmed:true }` |
| GET | `/jobs`, `/jobs/{id}` | detail includes stages, clips summary, warnings |
| POST | `/jobs/{id}/retry` | `{ from_stage? }` resume at first invalid stage |
| POST | `/jobs/{id}/cancel` | |
| DELETE | `/jobs/{id}` | removes files |
| GET | `/jobs/{id}/events` | SSE |
| GET | `/jobs/{id}/transcript` | word-level JSON |
| PATCH | `/jobs/{id}/transcript` | word edits `[{idx, text}]` |
| POST | `/jobs/{id}/reselect` | re-run selection with new brief/threshold |
| POST | `/jobs/{id}/render-all` | `{ preset_id? }` |
| GET | `/jobs/{id}/download.zip` | all renders + sidecars |

## Clips
| Method | Path | Notes |
|---|---|---|
| GET | `/clips/{id}` | includes `clip_plan` |
| PATCH | `/clips/{id}` | RFC-7396 merge on `clip_plan` (trim, ops, layout, preset, caption overrides) |
| POST | `/clips/{id}/render` | `{ preset_id?, aspect?, profile? }` -> render id |
| GET | `/clips/{id}/renders` | |
| GET | `/clips/{id}/publish-pack` | |
| PUT | `/clips/{id}/publish-pack` | edit title/description/hashtags; server validates limits |
| POST | `/clips/{id}/publish-pack/regenerate` | LLM regenerate with template |
| POST | `/clips/{id}/feedback` | `{ value: up|down, actual_views? }` |
| GET | `/renders/{id}`, `/renders/{id}/file` | |

## Templates, presets (public read)
`GET /templates?kind=&status=` | `GET /templates/{id}` | `GET /templates/{id}/versions` | `GET /presets` | `POST /templates/{id}/preview` (server test-frame render, returns PNG).

## Uploads
`POST /uploads` (init) -> `PUT /uploads/{id}/chunk?n=` -> `POST /uploads/{id}/complete`.

## Drive
`GET /drive/status` | `POST /drive/sync` | `GET /drive/tree` | `GET /drive/files?route=source` | `POST /drive/pick` `{file_id}` -> upload_id-like source ref.

## Admin (session)
`/admin/templates` (import POST multipart, publish, archive, duplicate, export GET), `/admin/presets`, `/admin/fonts`, `/admin/brandkits`, `/admin/prompts` (list, get, put new version, activate, test), `/admin/scoring` (get/put weights, dry-run), `/admin/settings` (get/put, secrets report status only), `/admin/drive`, `/admin/blockers` (list, resolve), `/admin/jobs`, `/admin/audit`.

## Health
`GET /health` -> `{ ffmpeg:{version, libass, harfbuzz, fribidi}, gpu:{name, vram_gb, cuda}, models:{asr:[...], llm:{provider, ok}}, disk:{free_gb}, ytdlp:{version}, queue:{depth}, drive:{status}, templates:{valid, invalid} }`.

## SSE event
`event: progress` `data: { "job_id", "stage", "status", "pct", "message", "eta_s", "clip_id?" }`; also `event: state` (status change), `event: warning`, `event: done`. Heartbeat comment every 15 s. Client resumes with `Last-Event-ID`.

## Error codes (stable)
`LOGIN_REQUIRED, GEO_BLOCKED, PRIVATE_VIDEO, UNSUPPORTED_URL, TOO_LONG, DISK_FULL, ASR_FAILED, LLM_UNAVAILABLE, NO_CLIPS_FOUND, TEMPLATE_INVALID, FONT_MISSING, RENDER_FAILED, DRIVE_AUTH, DRIVE_NOT_FOUND, BLOCKER_REQUIRED, RATE_LIMITED, VALIDATION_ERROR`.
