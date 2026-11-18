# Runbook 5 — Resolving HITL Blockers & Recovering Stuck Jobs

## 1. Checking Blocker Status
```bash
# List all open and resolved blockers
.venv/bin/python -m hitl.cli status
```
Or open **Admin -> Blockers** in the web UI (`/api/v1/admin/blockers`).

## 2. Resolving a Blocker
Once you have supplied the missing secret in `.env`, placed the required asset/font, or made a product decision:
```bash
.venv/bin/python -m hitl.cli resolve BLK-0001 --note "Added required asset/secret"
```
This validates the resolution, updates `.clipper-state/blockers/BLK-0001.yaml` (and `BLOCKERS.md`), and automatically unblocks dependent tasks in `.clipper-state/tasks.yaml`.

## 3. Recovering Stuck or Interrupted Jobs
CLIPPER's job orchestrator (`backend/app/services/orchestrator.py`) checkpoints every completed stage (`stage_manifest.json`) with an input hash:
- If the worker restarts after a crash, queued/running jobs resume from the last completed stage without re-downloading or re-transcribing the video.
- To cancel a stuck job via API:
  ```bash
  curl -X POST http://localhost:8000/api/v1/jobs/<job_id>/cancel
  ```
- To retry a failed job from its last valid checkpoint, re-submit or trigger `POST /api/v1/clips/<clip_id>/render`.
