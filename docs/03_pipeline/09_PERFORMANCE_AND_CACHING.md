---
id: performance-caching
owner: pipeline
status: approved-v2
---
# Performance, caching and resource policy

## Targets
| Scenario | Target |
|---|---|
| 30-min video, GPU, auto mode | clips ready < 15 min |
| 30-min video, CPU only | < 60 min (medium int8) with clear ETA |
| Render 60 s clip | < 1.5x realtime CPU; < 0.3x with NVENC |
| Editor draft preview render | < 10 s for 60 s clip (`draft_fast`) |
| Template switch preview in UI | < 100 ms (CSS, no render) |

## Caching
- Stage cache keyed by `input_hash` (see JOB_STATE_MACHINE).
- ASR cache keyed by `source_key + engine + model + language + options`: re-processing the same URL skips ASR.
- LLM cache keyed by `prompt_version + chunk_hash + model`: re-select with a new brief reuses map results when valid.
- Frame/vision cache keyed by frame hash.
- Render cache keyed by `plan_hash + template versions`.

## Resource management
- Models loaded once per worker; unloaded after idle timeout to free VRAM.
- One GPU-bound stage at a time per device; renders limited by `render.max_parallel` (default 2 CPU, 1 NVENC).
- Long videos: chunked ASR (30 s windows) streamed to disk; analysis sampled adaptively; LLM map-reduce bounded by token budget.
- Disk: pre-flight space check; cleanup policy: delete `source/` after N days (default 14) if renders exist, never delete renders unless job deleted; quota warning at 80 %.
- Memory guard: abort stage with `RetryableError` if RSS > configured limit.

## Observability
Per-stage timings, peak memory, cache hit/miss, token counts stored in `metrics_json` and shown in admin Jobs; `job_report.md` summarises.

## Benchmarks (T-11.3)
Maintain `scripts/bench.py` producing a table per hardware profile; CI nightly on CPU, manual on GPU.
