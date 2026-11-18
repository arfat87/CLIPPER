---
id: testing-gates
owner: orchestrator
status: approved-v2
---
# Testing and quality gates

| Layer | What | Tooling | Target |
|---|---|---|---|
| Unit | Pure functions: ffmpeg_cmd, chunker, ass_builder, ranker, boundaries, mapping function, publish validator, template loader | pytest | >= 85 % on `pipeline/` pure modules, >= 85 % `app/services` |
| Golden | Fixed transcripts -> expected `.ass`, crop paths, clip sets, arg lists | pytest + files | tolerance-based where numeric |
| Visual snapshot | Frames at fixed timestamps from renders | ffmpeg + SSIM | SSIM >= 0.97 |
| Language | WER/CER per language, Devanagari/Bengali shaping, tofu detection | jiwer, fonttools | thresholds in task acceptance |
| Contract | API responses vs openapi; artefacts vs JSON Schemas; TS types generated | schemathesis/jsonschema | zero drift |
| Integration | Stage chain on fixtures; resume after kill; mock Drive | pytest | pass |
| Chaos | Kill worker, network loss, disk full, corrupt artefact | scripts | auto-recover or clear error |
| E2E | Paste URL (local sample over HTTP) -> clips -> preset -> render -> publish pack -> download | Playwright | nightly + before merge to main for UI tasks |
| Security | Secret scan, zip-slip, SSRF block, read-only Drive guard, prompt-injection fixture | pre-commit + tests | pass |
| Performance | bench script per hardware | scripts/bench.py | docs/03_pipeline/09 |
| Evaluation | Highlight precision@k, ASR bake-off | harness | reported, not gating |

## Gate
`make check` = ruff + mypy + pytest (unit/golden/contract) + eslint + tsc + secret scan. Nightly adds Playwright, snapshots, chaos, evaluation. Reviewer verdict required to merge.

## Test data
`assets/samples/` (CC-licensed clips en/hi/hinglish/bn), `fixtures/` (transcripts, plans, packs), `reference/` (owner correct transcripts, from Drive `06_Reference`). Large media stays out of git (LFS or download script with checksums).

## Rules
A bug fix includes a regression test. Flaky tests are fixed or quarantined with a ticket within one phase. Snapshot updates need reviewer approval and an explanation.
