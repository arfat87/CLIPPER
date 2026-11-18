---
id: deployment-path
owner: integrations
status: approved-v2
---
# Deployment path (local -> web)

| Stage | Setup | Notes |
|---|---|---|
| Local dev | `make dev`: API :8000, UI :5173, Huey worker (SQLite), files in `./data` | no paid services |
| Local app | `make serve`: one process serving built UI, optional desktop shortcut | |
| Self-hosted | docker compose on GPU VM: api, worker(cuda), redis, postgres, caddy (auto TLS) | admin on VPN/IP allow-list |
| Scaled | API on cheap VPS; GPU workers on demand (Modal/RunPod) pulling from Redis; R2 + signed URLs | v2 |

## Switches (env only)
`DATABASE_URL`, `QUEUE_URL`, `STORAGE_BACKEND`, `ADMIN_ENABLED`. Same code, same tests on both profiles (T-12.2).

## Production checklist
Auth enabled; rights checkbox enforced; rate limits; backups (DB dump + `data/` policy) with restore runbook; log rotation; yt-dlp update job; disk alerts; secrets via env/secret manager; Drive service account mounted read-only; health endpoint monitored.

## Cost notes
GPU VM only when processing; CPU-only works but slower. LLM cost is the Gemini API (optional) or zero with Ollama. No per-minute credits.
