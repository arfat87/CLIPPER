---
id: tech-stack
owner: orchestrator
status: approved-v2
---
# Tech stack and rationale

| Layer | Choice | Why / notes | verify |
|---|---|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn | ML ecosystem, async, OpenAPI, SSE | - |
| Env | `uv`, `pnpm` | Fast, lockfiles | - |
| DB | SQLModel/SQLAlchemy 2 + Alembic | SQLite local -> Postgres by URL | - |
| Queue | Huey (SQLite local, Redis prod) | Zero infra locally | - |
| Download | yt-dlp (pinned + update command) + ffmpeg | YT/IG/FB; IG/FB need cookies | Breaks often: nightly integration test |
| Drive | `google-api-python-client` + `google-auth` | Service account read-only | Re-check scopes/quotas at T-9.1 |
| ASR | Pluggable. Candidates: faster-whisper (large-v3 / medium int8), Hinglish/Hindi fine-tunes, AI4Bharat IndicConformer (Hindi), optional cloud STT | Choose by bake-off on owner audio (T-3.1b). Vendor WER claims unverified | Bake-off |
| Alignment | WhisperX / wav2vec2 forced alignment (en, hi); Bengali via engine timestamps or bn wav2vec2 | Tight word timing for highlight captions | Median error <= 120 ms |
| VAD/Audio | Silero VAD, librosa | Silence snapping, energy | - |
| Scenes | PySceneDetect ContentDetector | Cut avoidance, dynamics | - |
| Faces | MediaPipe face detection + EMA/Kalman | CPU-friendly | Fallback: OpenCV DNN |
| LLM | Provider interface: Gemini API (default if key) / Ollama (qwen2.5:7b-instruct or llama3.1:8b) | Offline option, JSON-schema output | Check current model ids at T-5.3; keep in config |
| Vision (optional) | Gemini video/image understanding or local VLM on sampled frames | Visual moments, screenshare detection | A/B before enabling by default |
| Captions | pysubs2 -> ASS -> ffmpeg libass (HarfBuzz+FriBidi) | Only robust route for Devanagari/Bengali | T-0.4 shaping test |
| Fonts | Noto Sans / Devanagari / Bengali, Hind Siliguri, Mukta, Poppins, Montserrat, Bebas Neue + owner fonts | Per-script fallbacks mandatory | License per font |
| Frontend | React 18, Vite, TS, Tailwind, shadcn/ui, TanStack Query, Zustand | Fast, accessible | - |
| API types | openapi-typescript from `contracts/openapi.yaml` | No drift | - |
| Mock API | Prism or MSW from OpenAPI | UI agent works in parallel | - |
| Storage | `StorageBackend`: LocalFS / S3-compatible (R2) | Env switch | - |
| Packaging | Docker + compose (api, worker, redis, postgres, caddy) | One command deploy | - |
| Quality | ruff, mypy, pytest, Playwright, eslint, tsc | Gates | - |
| Optional notify | smtplib/IMAP (Gmail app password) | Email HITL | Late module |

## FFmpeg requirement
Build with `--enable-libass --enable-libharfbuzz --enable-libfribidi --enable-libfreetype --enable-libx264`. Windows: gyan.dev full build. Linux: static BtbN/johnvansickle. T-0.4 renders Hindi + Bengali shaping test and fails the build if tofu/boxes detected.

## Banned
MoviePy TextClip for captions; storing secrets in DB; shelling out with unsanitised strings (use arg lists); `eval` on template content.
