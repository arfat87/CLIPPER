---
id: template-system
owner: pipeline
status: approved-v2
---
# Template system overview

Goal: the owner can add or change look-and-feel and publish text without touching code - via drop-in folders, zip import, Drive sync or the admin editor.

## Six template kinds
| Kind | Controls | Folder / file | Schema |
|---|---|---|---|
| `caption` | font per script, size, colours, outline/shadow/box, position, animation, emphasis | `assets/templates/<id>/template.json` | `contracts/schemas/caption_template.schema.json` |
| `layout` | fill / fit_blur / split / screen_focus parameters, headroom, smoothing | `.../layouts/<id>.json` | LAYOUT_TEMPLATE_SCHEMA.md |
| `brand_kit` | logo, watermark, intro/outro, palette, default fonts, CTA end-card | `assets/brandkits/<id>/brandkit.json` | BRAND_KIT_SCHEMA.md |
| `overlay` | hook title, progress bar, lower third, sticker slots with timing | `.../overlays/<id>.json` | LAYOUT_TEMPLATE_SCHEMA.md (overlay section) |
| `publish` | title/description/hashtag templates per platform and language | `.../publish/<id>.json` | PUBLISH_TEMPLATE_SCHEMA.md |
| `preset` | bundle: one of each kind + render profile | `.../presets/<id>.json` | below |

## Preset (the thing the owner actually picks)
```json
{ "id":"hinglish_podcast_bold","name":"Hinglish Podcast Bold","version":2,
  "caption":"bold_pop_yellow@3","layout":"fill_default@1","brand_kit":"my_channel@2",
  "overlays":["hook_title@1","progress_bar@1"],"publish":"yt_shorts_hinglish@1",
  "render_profile":"youtube_shorts","defaults":{"script":"latn","remove_fillers":true} }
```
References are `id@version` or `id@latest`. Clips pin exact versions at creation.

## Lifecycle
`draft -> published -> archived` (and `invalid` when validation fails). Published versions are immutable; edit = new version. Archive hides from pickers but keeps old clips working.

## Registry and loading
`template_loader` scans `assets/templates/`, `assets/brandkits/`, imported packages, and Drive-routed files; validates against schemas; writes `template` / `template_version` rows; invalid ones appear with reasons. Zero valid caption templates -> ASSET blocker. Adding a kind = add a schema + loader entry + renderer hook (documented extension point), not a rewrite.

## Packaging format `.cliptpl.zip`
```
manifest.json   {kind, id, version, name, author, license, min_engine_version}
template.json   (or brandkit.json / preset.json ...)
fonts/          optional, with LICENSE files
preview.png     optional (auto-generated if missing)
```
Import/export flows: TEMPLATE_IMPORT_AND_ADMIN_FLOW.md.

## Quality gates for any template
Schema-valid; fonts resolvable per script or fallbacks declared; contrast ratio >= 4.5:1 (primary vs outline/box, warning otherwise); safe-area compliant; test card renders in en/hi/hinglish/bn without tofu; license field present.
