---
name: raise-blocker
description: Use when a task needs something only the owner can provide (secret, credential/cookies, asset/template/font, a product decision, an environment fix, an account action). Creates a blocker file, marks the task BLOCKED and continues with unblocked work.
---
# Raise a blocker

1. Confirm it is truly human-only. Try the documented fallback first (e.g. Ollama instead of Gemini).
2. Pick the type: `SECRET | CREDENTIAL | ASSET | DECISION | ENVIRONMENT | ACCOUNT` (see `docs/06_agents/HITL_PROTOCOL.md`).
3. Create `.clipper-state/blockers/BLK-<next 4 digits>.yaml` from the template in HITL_PROTOCOL section 3. Fill: `task_id`, `step` (exact resume point), `needed`, `why`, `how_to_get`, `alternatives`, `unblocked_meanwhile`.
4. NEVER put a secret value in the file, a ticket, a log or a chat message. Ask the owner to place it in `.env` / `secrets/`.
5. Set the task `BLOCKED` in `.clipper-state/tasks.yaml`, add the blocker id, append one line to `BLOCKERS.md`.
6. Pick the next READY task whose dependencies are done. Do not idle.
7. When the owner says DONE: run the resolver validation listed in the blocker (live key test, file parse, health check). Only then set `RESOLVED` and resume at `step`.
