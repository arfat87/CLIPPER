---
trigger: glob
globs: assets/templates/**
description: Template and caption rules
---
- Templates are drop-in folders validated by `contracts/schemas/caption_template.schema.json`. No code change may be required to add one.
- Each template declares fonts per script (latn, deva, beng) with fallbacks.
- Never ship a font without a license note in the template manifest.
- Published template versions are immutable; edits create a new version.
