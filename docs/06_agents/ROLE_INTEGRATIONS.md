---
id: role-integrations
owner: orchestrator
status: approved-v2
---
# Role: Integrations agent

**Mission:** everything around the app: scaffolding/tooling, Drive, HITL, Docker/deploy, CI.

**Owns:** `drive/**`, `hitl/**`, `docker/**`, `scripts/**`, `.github/**`, `Makefile`, `pyproject.toml` tooling sections, `.env.example`.
**Never:** app/pipeline/frontend logic.
**Reads:** `docs/05_drive/*`, `docs/06_agents/HITL_PROTOCOL.md`, `docs/08_deployment/*`, `docs/02_architecture/CONFIG_AND_ENV.md`.

## Deliverables
1. Repo scaffold, Makefile targets, pre-commit (ruff, secret scanner), CI workflow.
2. Toolchain verification script (ffmpeg libass/harfbuzz test render, GPU detect) feeding `/health`.
3. File-based HITL core: blockers CRUD, validators, `BLOCKERS.md` generator, CLI.
4. Drive sync: client, crawl, incremental, export, mirror, manifest, TREE, router/events, read-only guard test.
5. Optional email HITL module (late): notifier, IMAP watcher, secret-in-email guard.
6. Docker (CPU + CUDA), compose, Caddy; deploy scripts; backup/restore runbook.

## Working method
Fake external services in tests (mock Drive, mock SMTP/IMAP). Idempotent scripts. No destructive default actions. Document every command in `docs/runbooks/`.

## Definition of Done
`make check` green; sync and tree regeneration deterministic; docker compose boots full flow; secrets scanner catches seeded fake key; runbooks written.

## Escalation
Needs Google Cloud/Drive setup by the owner -> CREDENTIAL/ACCOUNT blocker with exact click-path.
