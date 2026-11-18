---
id: config-env
owner: backend
status: approved-v2
---
# Configuration and environment

Two layers: **env/secrets** (`.env`, `secrets/`) via `pydantic-settings`, and **tunable config** (`config/*.yaml`, `config/prompts/*.md`) loaded at startup and hot-reloadable from admin. Missing required secret -> `BlockerRequired`.

## `.env.example` (all documented, no values)
```
APP_ENV=local                # local|prod
DATABASE_URL=sqlite:///data/clipper.db
QUEUE_URL=sqlite:///data/huey.db
STORAGE_BACKEND=local        # local|s3
DATA_DIR=./data
SECRETS_DIR=./secrets
ADMIN_ENABLED=true
ADMIN_PASSWORD_HASH=         # argon2 hash, set via `make admin-password`
LLM_PROVIDER=ollama          # gemini|ollama
GEMINI_API_KEY=              # SECRET (optional)
OLLAMA_HOST=http://localhost:11434
ASR_DEVICE=auto              # auto|cuda|cpu
HF_TOKEN=                    # SECRET (optional: gated models, diarization later)
DRIVE_ENABLED=false
DRIVE_SA_FILE=secrets/drive_sa.json
DRIVE_ROOT_FOLDER_ID=
DRIVE_POLL_MINUTES=5
S3_ENDPOINT= S3_BUCKET= S3_ACCESS_KEY= S3_SECRET_KEY=   # prod only
NOTIFY_ENABLED=false         # optional email HITL
NOTIFY_SMTP_USER= NOTIFY_SMTP_APP_PASSWORD= OWNER_EMAIL=
MAX_VIDEO_HOURS=3
```

## Config files (summaries; full defaults in `config/`)
| File | Purpose |
|---|---|
| `asr.yaml` | engine order, model per language, device, beam, VAD, hinglish rules, script defaults |
| `highlights.yaml` | clip length bounds, thresholds, fusion weights per genre, NMS overlap, diversity |
| `render.yaml` | output profiles (resolution, crf, preset, audio), loudness target, NVENC, parallelism |
| `platforms.yaml` | per-platform safe areas, text limits, hashtag rules, max duration |
| `drive.yaml` | folder routing rules, mime export map, size limits, poll interval |
| `prompts/*.md` | versioned LLM prompts (see 04_HIGHLIGHT_ENGINE) |

## Rules
1. Code reads config only through `core.config` typed accessors, never raw YAML.
2. Each config file has a schema (pydantic) and a `version:` key; an invalid file stops startup with a clear message.
3. Config hash is part of every stage `input_hash`.
4. Admin edits write a new file revision to `data/config_revisions/` and an audit row; rollback is one click.
