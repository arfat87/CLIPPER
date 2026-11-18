---
name: add-caption-template
description: Use to add or modify a caption template folder under assets/templates and validate it against the schema, with a test render and preview image.
---
# Add a caption template

1. Create `assets/templates/<id>/template.json` following `docs/04_templates/CAPTION_TEMPLATE_SCHEMA.md`. `id` is snake_case and equals the folder name.
2. Declare fonts for `latn`, `deva`, `beng` with fallbacks. Put font files in `assets/fonts/` (or `<id>/fonts/`) and fill `meta.license` honestly. If you do not know the license, mark `license: "UNKNOWN"` and raise an ASSET blocker.
3. Validate: `python -m backend.pipeline.captions.template_loader validate assets/templates/<id>`.
4. Render the test card: `make render-template-card ID=<id>` (English, Hindi, Hinglish, Bengali sample lines). Check shaping visually.
5. Generate `preview.png` (540x960) via the same command.
6. Add the template to `docs/04_templates/STARTER_TEMPLATES_CATALOG.md` if it is a starter.
7. Published versions are immutable: to change a published template bump `meta.version`.
