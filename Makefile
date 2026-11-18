.PHONY: dev api ui worker test check lint render-sample loop update-ytdlp

PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy
UVICORN := .venv/bin/uvicorn

dev:
	@echo "Starting CLIPPER API + UI server on http://localhost:8000"
	$(UVICORN) backend.app.main:app --host 0.0.0.0 --port 8000 --reload

api:
	$(UVICORN) backend.app.main:app --host 0.0.0.0 --port 8000

ui:
	cd frontend && pnpm run build

worker:
	$(PYTHON) -m huey.bin.huey_consumer backend.pipeline.tasks.huey -w 2

test:
	$(PYTEST) -v

lint:
	$(RUFF) check backend hitl scripts
	$(MYPY) hitl backend/app/config.py scripts/secret_scan.py

secret-scan:
	$(PYTHON) scripts/secret_scan.py

check: lint secret-scan test

render-sample:
	$(PYTHON) -m backend.pipeline.sample_generator

loop:
	$(PYTHON) -m hitl.cli loop

update-ytdlp:
	uv pip install -U yt-dlp
