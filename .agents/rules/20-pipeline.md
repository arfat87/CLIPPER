---
trigger: glob
globs: backend/pipeline/**
description: Pipeline rules
---
- Every stage = pure-ish function: inputs (artifact paths + config) -> outputs (artifact files + checkpoint). Idempotent. Input hash decides cache validity.
- `ffmpeg_cmd.py` and `ass_builder.py` are pure functions with golden-file tests.
- Long work reports progress via the progress callback (pct, message, eta).
- All LLM calls go through `providers/base.py`, JSON-schema enforced, transcript text wrapped as quoted data, temperature <= 0.3.
- Heavy models are loaded once per worker and reused.
