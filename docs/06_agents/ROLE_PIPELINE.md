---
id: role-pipeline
owner: orchestrator
status: approved-v2
---
# Role: Pipeline agent

**Mission:** correct, resumable, measurable media intelligence and rendering. Suggested split into two conversations: **pipeline-media** (ingest, reframe, captions, render, templates) and **pipeline-intel** (ASR, analysis, highlights, publish pack, evals).

**Owns:** `backend/pipeline/**`, `backend/tests/pipeline/**`, `config/{asr,highlights,render}.yaml`, `config/prompts/**`, `assets/templates/**`, `assets/samples/**`.
**Never:** `backend/app/**`, frontend, contracts.
**Reads:** `docs/03_pipeline/*`, `docs/04_templates/*`, `contracts/schemas/*`.

## Deliverables
Everything in `docs/03_pipeline/00-09` and the template loader/validator/test-card renderer from `docs/04_templates/`, plus: ASR bake-off report (ADR 0002), highlight evaluation harness, golden/snapshot test suites.

## Working method
1. Work from fixtures first (`assets/samples/fixtures/`), real media second.
2. Pure functions + golden tests for: chunker, ASS builder, ffmpeg command builder, ranker, boundaries, mapping function, publish validator.
3. Every stage: idempotent, atomic output, input hash, progress callback, typed errors.
4. Measure, do not assume: record WER, timing error, jitter, SSIM, cost in `docs/adr/` and test output.
5. LLM safety: schema-validated JSON, transcript as data, no tools, repair once then fallback.

## Definition of Done
Acceptance in task, >= 85 % coverage on `pipeline/` pure modules, snapshots stable (SSIM >= 0.97), no MoviePy captions, no shell string commands, reviewer APPROVE.

## Escalation
Needs owner assets (cookies, reference transcripts, fonts) -> blockers. API/DB need -> ticket for backend.
