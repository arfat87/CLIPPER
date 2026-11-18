---
trigger: glob
globs: backend/app/**
description: Backend API rules
---
- FastAPI + SQLModel + Alembic. All I/O through pydantic schemas that match `contracts/`.
- Every endpoint: typed request/response, error model from `docs/02_architecture/API_SPEC.md`, tests.
- Never import from `backend/pipeline` internals; call through `pipeline.orchestrator` public API only.
- DB changes only via Alembic migration with down-revision tested.
- No blocking work in request handlers; enqueue to Huey.
