---
id: database-schema
owner: backend
status: approved-v2
---
# Database schema (SQLModel / Alembic)

Conventions: ids are ULID strings; `created_at/updated_at` UTC; JSON columns use SQLAlchemy JSON (JSONB on Postgres); soft delete via `deleted_at` where noted. Never store secrets. Every migration has a tested downgrade.

## Core
| Table | Columns (key ones) | Notes |
|---|---|---|
| `job` | id, source_kind (url/upload/drive), source_url, source_ref, platform, title, duration_s, language, script, genre, status, stage, error_code, error_message, settings_json, versions_json, clip_brief, preset_id, auto_mode, created_at, updated_at, deleted_at | `settings_json` = snapshot of all options at creation |
| `stage_checkpoint` | id, job_id, stage, status (PENDING/RUNNING/DONE/FAILED/STALE), input_hash, artefact_path, attempt, started_at, finished_at, error_json, metrics_json | unique(job_id, stage, attempt) |
| `transcript` | id, job_id, language, script, engine, model_id, path_json, mean_conf, edited (bool) | one active per job |
| `clip` | id, job_id, rank, start_s, end_s, score, score_mode, score_breakdown_json, title, hashtags_json, reason, hook_line, layout, preset_id, plan_path, status, feedback (up/down/null), actual_views, created_at | `clip_plan.json` is truth for edits; DB mirrors summary |
| `render` | id, clip_id, plan_hash, template_versions_json, aspect, profile, path_mp4, path_srt, path_ass, path_thumb, status, progress_pct, duration_s, created_at | render is valid iff `plan_hash` == current plan hash |
| `publish_pack` | id, clip_id, platform, path_json, valid (bool), warnings_json, template_id | |

## Templates and assets
| Table | Columns | Notes |
|---|---|---|
| `template` | id (slug), kind (caption/layout/brand_kit/overlay/publish/preset), name, status (draft/published/archived/invalid), latest_version, source (builtin/import/drive/admin), created_at | |
| `template_version` | id, template_id, version (int), path, content_hash, valid, validation_json, license_note, published_at | immutable once published |
| `preset` | id, name, caption_tpl, layout_tpl, brandkit_tpl, overlay_tpls_json, publish_tpl, render_profile, is_default | refs are `template_id@version` or `@latest` |
| `font` | id, family, style, path, scripts_json (latn/deva/beng coverage), license, license_ok (bool), source | glyph coverage computed at register |
| `prompt_version` | id, name, version, path, content_hash, active (bool), created_by, created_at | prompts are files; table tracks activation |

## Settings, Drive, blockers
| Table | Columns | Notes |
|---|---|---|
| `setting` | key, value_json, updated_at | non-secret only; secrets only report `set` status |
| `drive_state` | id, root_folder_id, start_page_token, last_sync_at, last_error, status | single row |
| `drive_file` | id (drive file id), parent_ids_json, name, mime, size, md5, modified_time, path_in_mirror, route (template/font/doc/source/unsorted), local_hash, status (SYNCED/STALE/REMOVED/ERROR), seen_at | |
| `blocker` | id (BLK-####), type, status, task_id, step, title, payload_json, created_at, resolved_at | mirrors `.clipper-state/blockers/*.yaml` |
| `audit_log` | id, actor, action, target, diff_json, created_at | admin writes |
| `feedback_event` | id, clip_id, kind, value, created_at | feeds scoring tuning |

## Indexes
`job(status, created_at)`, `stage_checkpoint(job_id, stage)`, `clip(job_id, rank)`, `render(clip_id, plan_hash)`, `drive_file(parent_ids, status)`, `template_version(template_id, version)`.

## Invariants
1. A job in `COMPLETED` has all stages `DONE` and at least one render or an explicit "no clips" flag.
2. Deleting a job deletes its folder; renders referenced by exports are removed too; cascade in DB, files by service.
3. `template_version.valid=false` can never be referenced by a preset marked default.
4. Editing a transcript sets `transcript.edited=true` and marks dependent stages STALE (select onward are NOT auto-rerun; only affected clip plans and renders).
