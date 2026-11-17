# CLIPPER — AI Video Clipping & Multi-Language Captioning Web Application

Local-first AI video clipping and dynamic captioning platform supporting **YouTube**, **Instagram**, **Facebook**, and local video uploads across **English**, **Hindi (हिन्दी)**, **Hinglish**, and **Bengali (বাংলা)**.

## Quick Start

```bash
# 1. Run linter & test suite
make check

# 2. Launch API + Studio Web UI on port 8000
make dev

# 3. Check autonomous task graph & HITL blockers
.venv/bin/python -m hitl.cli status
```

## Key Features
- **Ingest (`backend/pipeline/ingest/downloader.py`)**: `yt-dlp` + FFmpeg mezzanine normaliser (30fps H.264 MP4 + 16kHz mono WAV) with automatic HITL `CREDENTIAL` blocker generation when Instagram/Facebook require browser cookies.
- **Multi-Language ASR (`backend/pipeline/asr/engine.py`)**: `faster-whisper` word-level timestamp decoding, Hinglish detection heuristic, Unicode NFC normalisation, and Devanagari-to-Roman transliteration.
- **Viral Highlight Engine (`backend/pipeline/highlights/ranker.py`)**: Thought-unit candidate windowing, acoustic/prosodic signal scoring, LLM semantic scoring (Gemini API / Ollama / offline viral-hook scorer), hybrid fusion ranking, Non-Maximum Suppression (NMS), and silence boundary snapping.
- **Dynamic Styled Captions & Reframe (`backend/pipeline/captions/` & `backend/pipeline/render/`)**: JSON-Schema validated caption templates, 6 built-in styles, script-aware line chunking (`latn`, `deva`, `beng`), `pysubs2` ASS generation (`highlight_word`, `karaoke_fill`, `pop`, `typewriter`, `fade_slide`), and single-pass FFmpeg + `libass` 9:16 vertical burn-in.
- **Human-in-the-Loop Email Protocol (`hitl/`)**: Checkpointed `.agent/tasks.yaml` + `.agent/state.json` + `.agent/blockers/BLK-XXXX.yaml`, SMTP HTML email notifications, `BLOCKERS.md` fallback, and email reply parser with raw-secret leak protection.
