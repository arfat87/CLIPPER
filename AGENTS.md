# CLIPPER — Autonomous Agent Operating Manual (`AGENTS.md`)

## 1. Mission
Build, test, and maintain **CLIPPER**, a local-first AI Video Clipping & Multi-language Captioning web application supporting YouTube, Instagram, Facebook, and local uploads across English, Hindi, Hinglish, and Bengali (Bangla).

## 2. Ground Rules for Executors
1. **Local-first, cloud-ready.** Everything must run with `make dev` on a single machine with no paid services required. Optional cloud services (Gemini API, S3/R2, SMTP/IMAP) activate automatically when configured in `.env`.
2. **Every task is idempotent and checkpointed.** `.agent/state.json` and `.agent/tasks.yaml` track exact progress.
3. **One task = one commit** (`[T-<id>] <title>`). Run `make check` before marking any task `DONE`.
4. **Never hard-code secrets.** Read settings via `backend.app.config.get_settings()`. When a required credential/asset is missing and `strict=True`, call `hitl.raise_blocker(...)` which persists `.agent/blockers/BLK-XXXX.yaml`, sends an email (or writes to `BLOCKERS.md` in offline fallback), and pauses only the dependent task branch.
5. **FFmpeg + libass is the caption rendering engine.** Always generate `.ass` subtitle files via `pysubs2` with per-script font fallbacks (`latn`, `deva`, `beng`) and burn in with FFmpeg `ass=` filter.

## 3. Human-in-the-Loop (HITL) CLI Commands
- `python -m hitl.cli status` — list all open and resolved blockers and task progress.
- `python -m hitl.cli raise --type SECRET --task T-4.3 --key GEMINI_API_KEY --title "Gemini API Key"` — manually raise a blocker.
- `python -m hitl.cli resolve BLK-0001 --note "Added to .env"` — validate & resolve a blocker and unblock dependent tasks.
- `python -m hitl.cli watch` — poll IMAP inbox for owner replies (`DONE`, `USE OLLAMA`, attachments) and auto-resolve.
- `python -m hitl.cli loop` — run the continuous autonomous task scheduler.
