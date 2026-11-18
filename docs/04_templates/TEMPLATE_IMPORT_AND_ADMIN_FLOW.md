---
id: template-import-admin
owner: pipeline
status: approved-v2
---
# Adding, importing and editing templates (including the admin panel)

## Ways to add a template (all end in the same registry)
| Way | Steps | Result status |
|---|---|---|
| Drop-in folder | copy `assets/templates/<id>/` -> restart or click Rescan | published if valid |
| Zip import (admin) | Admin -> Templates -> Import `.cliptpl.zip` | draft, owner publishes |
| Google Drive | put package in `Clipper/02_Templates/` -> Drive sync routes it | draft, owner publishes |
| Admin editor | Duplicate existing -> edit -> publish | new version |
| From reference (v1.5 optional) | upload screenshot/description -> LLM drafts `template.json` | draft only, never auto-published |

## Import pipeline (`template_loader` + admin endpoint)
1. Unzip to temp dir with zip-slip protection (reject `..`, absolute paths, symlinks), size cap 50 MB, file count cap 200.
2. Read `manifest.json`; verify `kind`, `id`, `version`, `min_engine_version`.
3. Validate JSON against the kind's schema.
4. Fonts: register files; compute glyph coverage per script with fonttools; check family names match template references; require `license` text; warn on `UNKNOWN`.
5. Safety checks: no executable files, no external URLs in templates, assets only image/video/font types.
6. Conflict policy: same `id` + higher `version` -> new version; same version with different hash -> reject (immutability); same hash -> no-op.
7. Render a **test card** (4 languages) with libass; generate `preview.png`; fail on tofu detection.
8. Save as `draft` and show the validation report. Owner clicks Publish.

## Editing flow
- Editor works on a draft copy. Live CSS preview is instant; "Render test frame" runs the real libass renderer (source of truth) within ~1 s.
- Publish bumps `meta.version`. Presets referencing `@latest` pick it up; clips pinned to an older version keep it until the owner chooses "Update clip to latest".
- Rollback = set a previous version as latest (new version number pointing to old content, so history stays linear).

## Export
`GET /admin/templates/{id}/export?version=` -> `.cliptpl.zip` including fonts only if their license allows redistribution (`license_ok=true`), otherwise a `fonts.txt` listing required families.

## Extending with a new template kind (developer path)
1. Add `contracts/schemas/<kind>.schema.json`.
2. Register in `template_loader` kind registry with `validate`, `normalize`, and `apply(plan, template)` hook.
3. Add preview support in admin (generic JSON editor works automatically).
4. Add tests: valid, invalid, version immutability, import/export round trip.
No other module should need to change; reviewer checks this in the scope guard.

## Permissions and audit
Every import, publish, archive and rollback writes an `audit_log` row with a diff of the JSON.
