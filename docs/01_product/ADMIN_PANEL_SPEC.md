---
id: admin-panel
owner: uiux
status: approved-v2
---
# Admin Panel Spec (`/admin`, owner-only)

Auth: single-owner password (hashed, argon2) for local; session cookie; rate-limited. Optional passkey later. Not exposed unless `ADMIN_ENABLED=true`.

## Sections
| Section | What the owner can do | Backend contract |
|---|---|---|
| Templates | List by kind, filter by status (draft/published/archived/invalid), import zip/folder/Drive, export, duplicate, edit (form + JSON), validate, preview on test card (en/hi/hinglish/bn), publish new version, archive | `/admin/templates/*` |
| Presets | Bundle one template per kind + render profile; set default | `/admin/presets/*` |
| Fonts | Upload/register fonts, show license field, glyph coverage check per script (latn/deva/beng), mark default fallbacks | `/admin/fonts/*` |
| Brand kits | Logo, watermark, intro/outro, palette, default preset | `/admin/brandkits/*` |
| Prompts | View/edit versioned prompt files (`config/prompts/*.md`), diff, rollback, run against fixture and see JSON validity | `/admin/prompts/*` |
| Scoring | Edit fusion weights and thresholds per genre, see feedback stats, "dry-run on job X" to compare old vs new picks | `/admin/scoring/*` |
| Models & providers | ASR engine/model, device, LLM provider/model, key status (set/not set, never the value), connectivity test | `/admin/settings/*` |
| Drive | Connection status, root folder, last sync, tree view, routing overrides, force resync, per-folder enable | `/admin/drive/*` |
| Blockers inbox | Open/resolved blockers, step-by-step instructions, Done button triggers validator | `/admin/blockers/*` |
| Jobs | Browse, retry, delete, disk usage, artifact explorer | `/admin/jobs/*` |
| System health | ffmpeg/libass/harfbuzz, GPU, models cached, disk, yt-dlp version, queue depth | `/health` |
| Audit log | Who changed what (template publish, prompt edit, setting change) | `/admin/audit` |

## Template editor requirements
- Left: structured form generated from the JSON Schema. Right: live preview (CSS) + "Render test frame" button (server, libass) because CSS is only an approximation.
- JSON tab with schema validation inline. Diff vs previous version.
- Language switcher for sample text, font fallback visualiser, safe-area overlay toggle per platform profile.
- Cannot publish while validation errors exist. Warnings (missing font license, low contrast) need explicit acknowledge.

## Non-functional
Every admin write is audited; every destructive action needs confirm; no secret value is ever returned by any endpoint (only `set: true/false`).
