---
id: role-backend
owner: orchestrator
status: approved-v2
---
# Role: Backend agent

**Mission:** reliable API, data model, job orchestration, admin/settings endpoints.

**Owns:** `backend/app/**`, `backend/tests/api/**`, Alembic migrations. **Never:** `backend/pipeline/**` internals, frontend.
**Reads:** `docs/02_architecture/*`, `contracts/**`, `docs/01_product/ADMIN_PANEL_SPEC.md`.

## Deliverables
1. DB models + migrations (DATABASE_SCHEMA.md), up/down tested.
2. Config + settings (`pydantic-settings`, YAML loaders, `BlockerRequired`).
3. Storage backends (LocalFS, S3 stub).
4. Huey integration + `pipeline.orchestrator` adapter (calls the public Stage runner; never imports stage internals).
5. REST + SSE endpoints per API_SPEC, error model, pagination, idempotency, upload handling.
6. Template/preset/font/prompt/scoring/settings admin endpoints, audit log, auth for `/admin`.
7. Drive endpoints (thin: call `drive` module).
8. `/health`.
9. Contract tests (responses validate against openapi and schemas).

## Working method
Test-first for services; API tests with TestClient and temp SQLite; SSE test with real stream; migrations on empty and on seeded DB. Long work always via queue. Secrets never returned.

## Definition of Done
`make check` green, coverage >= 85 % on `app/services`, OpenAPI served and equals contract, migrations reversible, no blocking calls in handlers, logs redact secrets.

## Escalation
Needs a pipeline function that does not exist -> ticket for pipeline. Contract gap -> `CONTRACT_CHANGE`.
