# CLIPPER — Autonomous Execution Progress Log

- **[DONE] Phase 0 (Foundations & HITL):**
  - `T-0.1` Repository scaffold, `pyproject.toml`, `Makefile`, `.env.example`, `AGENTS.md`
  - `T-0.2` Human-in-the-Loop (`hitl/`) service: `Blocker` models, `raise_blocker`, SMTP HTML/text notifier, `BLOCKERS.md` fallback, reply parser with raw-secret leak protection, CLI
  - `T-0.3` Config system (`backend/app/config.py` + `config/*.yaml`)
  - `T-0.4` Toolchain verification (`verify_toolchain()`): FFmpeg + `libass` + `fribidi` verified with multi-script English/Hindi/Bengali test burn-in
  - `T-0.5` Sample media generator (`ensure_sample_media()`): 4 multilingual test videos (`en`, `hi`, `hinglish`, `bn`)
- **[DONE] Phase 1 (Backend Core):**
  - `T-1.1` SQLModel database entities (`Job`, `StageCheckpoint`, `Clip`)
  - `T-1.2` `LocalStorageBackend` (`data/jobs/<job_id>/...`)
  - `T-1.3` Stage-checkpointed pipeline orchestrator (`run_job_pipeline`)
  - `T-1.4` FastAPI application (`backend/app/main.py`) with REST + SSE + ZIP export + HITL endpoints
- **[DONE] Phase 2–5 (Media Pipeline):**
  - `T-2.1–2.3` `yt-dlp` ingest + FFmpeg normalization + cookie HITL blocker support
  - `T-3.1–3.4` `faster-whisper` ASR wrapper + Hinglish detection + Devanagari/Roman/Bengali script normaliser
  - `T-4.1–4.7` Candidate window builder, signal features, Gemini/Ollama/heuristic viral scorer, hybrid fusion ranker, NMS, boundary snapping
  - `T-5.1–5.6` 9:16 `fill` and `fit_blur` reframe filters, 6 starter caption templates, `pysubs2` ASS builder with per-word highlight/karaoke/pop/typewriter animations, single-pass FFmpeg `libass` renderer
- **[DONE] Phase 6–7 (Web Studio UI & Verification):**
  - `T-6.1–6.5` Interactive Clip Studio UI (`frontend/index.html`) with 9:16 vertical player, instant CSS caption preview, tap-to-select template carousel, trim/layout editor, and HITL dashboard
  - `T-7.1` End-to-end test suite (`backend/tests/test_clipper_e2e.py`) passing
- **[DONE] Spec Pack v2 Sync (from Google Drive `CLIPPER/clipper-spec.zip`):**
  - Synced `docs/` (10 architectural modules + original blueprint), `contracts/schemas/*.json`, `.agents/rules/*`, `.agents/skills/*`, `.clipper-state/tasks.yaml`, `config/prompts/*.md`, `config/{drive,platforms}.yaml`, `assets/samples/fixtures/*`, `BLOCKERS.md`, and `DECISIONS.md`.
  - Preserved v1 runtime compatibility (`config/{asr,highlights,render}.yaml` and `assets/templates/*/template.json`); placed v2 starter templates under `assets/templates_v2/` and v2 configs under `config/spec_v2/` pending Phase 6 schema migration (`T-6.3`).
- **[DONE] Phase 0 Orchestrator (`T-0.2`, `T-0.3`):**
  - `T-0.2` Verified all 6 `.agents/rules/*.md` (`00-global`, `50-security` always-on; `10-backend`, `20-pipeline`, `30-frontend`, `40-templates-captions` glob-triggered) and 5 workspace `.agents/skills/*/SKILL.md`.
  - `T-0.3` Converted `API_SPEC.md` + `ADMIN_PANEL_SPEC.md` into `contracts/openapi.yaml` (validated with `openapi-spec-validator`), added `drive_manifest.example.json` fixture, validated all 5 JSON schemas (`contracts/schemas/*.json`), and tagged `contracts-v1`.
  - Recorded Day-0 machine facts in `DECISIONS.md`; raised `BLK-0001` for missing Devanagari/Bengali fonts (`T-0.4`).
- **[DONE] Phase 0 Parallel Lanes (`T-0.1`, `T-0.5`, `T-0.6`, `T-0.7`, `T-0.8` — `REV-0001` APPROVE):**
  - `T-0.1` (`integrations`): Added `scripts/secret_scan.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `Makefile` (`lint` = ruff + mypy, `secret-scan`), and `hitl/tests` testpath in `pyproject.toml`.
  - `T-0.5` (`backend`): Updated `backend/app/config.py` with Spec v2 `Settings` fields (`OLLAMA_HOST`/`OLLAMA_BASE_URL` alias, `drive_*`, `queue_url`, `storage_backend`, `admin_*`), `load_spec_v2_yaml_config()`, and `BlockerRequired` tests in `backend/tests/api/test_config.py`.
  - `T-0.6` (`integrations`): Aligned `hitl/` (`raise_blocker.py`, `cli.py`, `models.py`, `notifier.py`, `validators.py`) to `.clipper-state/` with `.agent/` backward compatibility + `hitl/tests/test_hitl_v2.py`.
  - `T-0.7` (`pipeline`): Generated deterministic `assets/samples/SHA256SUMS` and `backend/tests/pipeline/test_samples_and_fixtures.py` validating all sample media, transcripts, fixtures, and v2 templates.
  - `T-0.8` (`uiux`): Built React 18 + Vite + Tailwind + strict TS scaffold in `frontend/`, generated `src/api/schema.d.ts` from `contracts/openapi.yaml`, added MSW mock API handlers, and verified `tsc --noEmit`, `vite build`, and `vitest run` (3/3 passed).
- **[DONE] Phase 1+ Parallel Lanes (`T-1.1`, `T-1.2`, `T-2.1`, `T-5.1`, `T-9.1`, `T-10.1` — `REV-0002` APPROVE):**
  - `T-1.1` & `T-1.2` (`backend`): Added all 17 Spec v2 SQLModel tables, reversible Alembic migrations (`migrations/`), atomic `LocalStorageBackend`, and `S3StorageBackend` stub (`11 passed` API tests).
  - `T-2.1` (`pipeline`): Enhanced `backend/pipeline/ingest/downloader.py` with URL canonicalization, SSRF IP guard, duration/disk guards, heatmap capture, and typed `IngestError` (`50 passed` ingest tests).
  - `T-5.1` (`pipeline`): Built `backend/pipeline/providers/` (`GeminiProvider`, `OllamaProvider`, prompt-injection data wrapper, JSON-Schema + repair-once) (`9 passed` provider tests).
  - `T-9.1` (`integrations`): Built read-only Google Drive sync (`drive/`), BFS crawl, incremental `changes.list`, native export, MD5 verify, traversal sanitizer, and read-only guard (`10 passed` drive tests).
  - `T-10.1` (`uiux`): Built design system primitives (`frontend/src/components/ui/`), mobile/desktop + `/admin` shell routing, typed TanStack Query hooks (`schema.d.ts`), and Vitest tests (`8 passed`).




- **[DONE] Phase 1–2 Batch 2 Parallel Lanes (`T-1.3`, `T-2.2`, `T-2.3`, `T-9.2`, `T-10.2`, `T-10.6` — `REV-0003` APPROVE):**
  - `T-1.3` (`backend`): Implemented Huey queue (`SqliteHuey`/`MemoryHuey`/`RedisHuey`), Spec v2 checkpointed orchestrator (`fetch`..`package`), `input_hash` cache/staleness, atomic temp-dir staging, retry/blocker/cancel handlers (`backend/app/services/{queue,orchestrator}.py`, `backend/tests/api/test_queue_orchestrator.py`).
  - `T-2.2` (`pipeline`): Added Netscape cookie validation/redaction + Instagram/Facebook login-wall detection raising secret-free `CREDENTIAL` HITL blockers (`backend/pipeline/ingest/{cookies,downloader}.py`, `backend/tests/pipeline/test_ingest_cookies.py`).
  - `T-2.3` (`pipeline`): Added `prepare.py` (`compute_media_fingerprint`, atomic FFmpeg CFR 30fps `mezzanine.mp4`, 16kHz mono `audio.wav`, bounded `media/thumbs/`, loudness/clipping/silence audio probe) with `ffprobe` synthetic media tests (`backend/tests/pipeline/test_prepare.py`).
  - `T-9.2` (`integrations`): Added `.clipper.yaml` untrusted-data routing/ignore parser, deterministic offline `TREE.md` renderer, and `DriveSyncEvent` bus wired into `DriveSync` (`drive/{routing,tree,events,sync}.py`, `drive/tests/test_drive_tree_and_routing.py`).
  - `T-10.2` & `T-10.6` (`uiux`): Implemented Home URL/Upload/Drive job creator + live `EventSource`/`Last-Event-ID` SSE timeline (`studio/HomeView.tsx`, `studio/JobTimelineView.tsx`), and Admin Templates (import -> validate -> CSS preview/test-frame -> publish), Presets, Fonts & Brand Kits UI (`admin/AdminTemplatesView.tsx`, `admin/AdminAssetsViews.tsx`) (`17` Vitest tests passed, `103.58 kB` gzipped JS).

## Phase 3–11 (Batches 3–7 — Spec Pack v2 Full Implementation & Hardening) — 2026-11-18
- **Backend (`T-1.4`, `T-1.5`, `T-1.6`, `T-7.2`, `T-11.2`, `T-12.3`)**: Jobs/Clips/Uploads + SSE endpoints, Admin CRUD + auth + audit log + secret masking, `/api/v1/health` Spec v2 subsystem aggregation, `/api/v1/drive/*`, `/api/v1/admin/blockers*`, `/api/v1/clips/{id}/publish-pack*`, `/api/v1/jobs/{id}/download.zip`, 17-code error mapper + retry/backoff + LRU disk quota eviction + crash recovery, Bearer auth middleware, rate limiting, rights confirmation, and SSRF/path guards.
- **Pipeline (`T-3.1..T-3.4`, `T-4.1..T-4.4`, `T-5.2..T-5.5`, `T-5.8`, `T-6.1..T-6.8`, `T-7.1`, `T-11.3`)**: `faster-whisper` ASR + WhisperX forced alignment + Hinglish/NFC script normalization, scene/audio/face/topic analysis + `VideoProfile` + visual scorer, Highlight Engine v2 (thought-unit segmenter, Map-Reduce LLM virality scorer, signal fusion, NMS, semantic dedupe, diversity caps, adaptive count, silence/cut boundary snapping, `clip_plan.json`), 4-language golden evaluation harness (`precision@5 = 1.0`), 1-Euro smart reframe + 4 layout modes, script-aware chunker, Spec v2 6-kind template loader + 6 starters + tofu check + Drive template importer, ASS builder (6 animations + mixed-script `\fn` + SRT/VTT), `clip_plan` timeline mapping (`remove_fillers`, `trim_silence`, overlays), FFmpeg command builder + `render_clip_v2` + NVENC auto-detect, PublishPack builder, and `bench.py`.
- **Integrations (`T-9.3`, `T-12.1`, `T-12.2`)**: 5 Drive workspace consumers (templates, fonts, docs mirror with untrusted header, reference loader, source picker), CPU/CUDA Dockerfiles, Caddyfile TLS + SSE proxy, `docker-compose.yml`, `docker/docker-compose.cloud.yml`, and `scripts/backup_restore.py`.
- **UI/UX (`T-10.3..T-10.8`)**: Clip Grid + Score Breakdown, NLE Clip Editor (`J/K/L`, `I/O`, `[`/`]`, Safe-Zone Guides), Publish Pack screen, Admin Console (Templates, Assets, Prompts, Scoring, Settings, Drive, Blockers, Ops), dual real/auto/mock API client, and 30-test Vitest Studio+Admin E2E suite.
- **Reviewer & Orchestrator (`T-11.1`, `T-11.4`, `REV-0004`, `REV-0005`)**: 4-language E2E golden suite (`en`, `hi`, `hinglish`, `bn`), `README.md`, `docs/USER_GUIDE.md`, `docs/TEMPLATE_AUTHORING_GUIDE.md`, and 5 operational runbooks under `docs/runbooks/`.
