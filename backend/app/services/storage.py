from __future__ import annotations

import shutil
from pathlib import Path

from backend.app.config import get_settings


class LocalStorageBackend:
    """Local filesystem storage backend (swappable with S3/R2 in production)."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or get_settings().data_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        d = self.base_dir / "jobs" / job_id
        for sub in ("source", "audio", "asr", "highlights", "clips", "renders"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def delete_job(self, job_id: str) -> None:
        d = self.base_dir / "jobs" / job_id
        if d.exists():
            shutil.rmtree(d)


def get_storage() -> LocalStorageBackend:
    return LocalStorageBackend()
