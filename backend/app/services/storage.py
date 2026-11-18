from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from backend.app import config as config_mod
from backend.app.config import get_settings
from hitl.models import BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker

JOB_SUBDIRS = ("source", "audio", "asr", "highlights", "clips", "renders")


class StorageError(RuntimeError):
    """Base error for storage backend operations."""


class StorageConfigurationError(StorageError):
    """Raised when storage backend configuration is invalid or incomplete."""


@runtime_checkable
class StorageBackend(Protocol):
    def job_dir(self, job_id: str) -> Path: ...

    def stage_dir(self, job_id: str, stage: str) -> Path: ...

    def delete_job(self, job_id: str) -> None: ...

    def write_bytes(self, key: str, data: bytes) -> Path | str: ...

    def write_text(self, key: str, text: str, encoding: str = "utf-8") -> Path | str: ...

    def read_bytes(self, key: str) -> bytes: ...

    def read_text(self, key: str, encoding: str = "utf-8") -> str: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> bool: ...


def _validate_segment(value: str, label: str) -> str:
    clean = value.strip()
    if not clean or clean in (".", "..") or "/" in clean or "\\" in clean:
        raise StorageError(f"Invalid {label}: {value!r}")
    return clean


class LocalStorageBackend:
    """Local filesystem storage backend (swappable with S3/R2 in production)."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or get_settings().data_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, key: str) -> Path:
        raw = key.strip()
        if not raw:
            raise StorageError("Storage key must not be empty")
        candidate = Path(raw)
        if candidate.is_absolute():
            raise StorageError(f"Absolute storage keys are not allowed: {key!r}")
        resolved = (self.base_dir / candidate).resolve()
        try:
            resolved.relative_to(self.base_dir)
        except ValueError as exc:
            raise StorageError(f"Storage key escapes base directory: {key!r}") from exc
        if resolved == self.base_dir:
            raise StorageError(f"Invalid storage key: {key!r}")
        return resolved

    def job_dir(self, job_id: str) -> Path:
        safe_job_id = _validate_segment(job_id, "job_id")
        d = self.base_dir / "jobs" / safe_job_id
        for sub in JOB_SUBDIRS:
            (d / sub).mkdir(parents=True, exist_ok=True)
        return d

    def stage_dir(self, job_id: str, stage: str) -> Path:
        safe_job_id = _validate_segment(job_id, "job_id")
        safe_stage = _validate_segment(stage, "stage")
        d = self.base_dir / "jobs" / safe_job_id / safe_stage
        d.mkdir(parents=True, exist_ok=True)
        return d

    def delete_job(self, job_id: str) -> None:
        safe_job_id = _validate_segment(job_id, "job_id")
        d = self.base_dir / "jobs" / safe_job_id
        if d.exists():
            shutil.rmtree(d)

    def write_bytes(self, key: str, data: bytes) -> Path:
        target = self._resolve_key(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(target.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
        return target

    def write_text(self, key: str, text: str, encoding: str = "utf-8") -> Path:
        return self.write_bytes(key, text.encode(encoding))

    def read_bytes(self, key: str) -> bytes:
        target = self._resolve_key(key)
        if not target.exists():
            raise FileNotFoundError(f"Storage key not found: {key}")
        return target.read_bytes()

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def exists(self, key: str) -> bool:
        target = self._resolve_key(key)
        return target.exists()

    def delete(self, key: str) -> bool:
        target = self._resolve_key(key)
        if not target.exists() and not target.is_symlink():
            return False
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True


class S3StorageBackend:
    """Config-driven S3/R2 storage backend stub for production object storage."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        bucket: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        is_r2: bool = False,
        strict: bool = False,
        validate: bool = True,
    ):
        settings = get_settings()
        self.endpoint = (settings.s3_endpoint if endpoint is None else endpoint).strip()
        self.bucket = (settings.s3_bucket if bucket is None else bucket).strip()
        self.access_key = (settings.s3_access_key if access_key is None else access_key).strip()
        self.secret_key = (settings.s3_secret_key if secret_key is None else secret_key).strip()
        self.region = (settings.s3_region if region is None else region).strip() or "auto"
        self.is_r2 = is_r2
        self.strict = strict
        if validate:
            self.validate_config(strict=strict)

    def missing_config_keys(self) -> list[str]:
        missing: list[str] = []
        if self.is_r2 and not self.endpoint:
            missing.append("S3_ENDPOINT")
        if not self.bucket:
            missing.append("S3_BUCKET")
        if not self.access_key:
            missing.append("S3_ACCESS_KEY")
        if not self.secret_key:
            missing.append("S3_SECRET_KEY")
        return missing

    def validate_config(self, *, strict: bool = False) -> None:
        missing = self.missing_config_keys()
        if not missing:
            return
        if strict:
            needed = [
                NeededItem(
                    key=key,
                    where=".env",
                    how_to_get=f"Provide {key} for S3/R2 object storage in .env",
                )
                for key in missing
            ]
            create_or_get_blocker(
                btype=BlockerType.SECRET,
                task_id="T-1.2",
                title="Missing S3/R2 storage configuration",
                needed=needed,
                why=f"STORAGE_BACKEND={'r2' if self.is_r2 else 's3'} requires {', '.join(missing)}.",
                alternatives=["Set STORAGE_BACKEND=local in .env for local-first storage."],
                root_dir=config_mod.ROOT_DIR,
                raise_exc=True,
            )
        raise StorageConfigurationError(
            f"Incomplete S3/R2 storage configuration; missing: {', '.join(missing)}. "
            "Set these values in .env or use STORAGE_BACKEND=local."
        )

    def _stub_not_implemented(self) -> None:
        self.validate_config(strict=self.strict)
        raise NotImplementedError(
            "S3/R2 object storage operations are stubbed in local-first v1 (T-12.2); "
            "use STORAGE_BACKEND=local."
        )

    def job_dir(self, job_id: str) -> Path:
        self._stub_not_implemented()
        raise AssertionError("unreachable")

    def stage_dir(self, job_id: str, stage: str) -> Path:
        self._stub_not_implemented()
        raise AssertionError("unreachable")

    def delete_job(self, job_id: str) -> None:
        self._stub_not_implemented()

    def write_bytes(self, key: str, data: bytes) -> str:
        self._stub_not_implemented()
        raise AssertionError("unreachable")

    def write_text(self, key: str, text: str, encoding: str = "utf-8") -> str:
        self._stub_not_implemented()
        raise AssertionError("unreachable")

    def read_bytes(self, key: str) -> bytes:
        self._stub_not_implemented()
        raise AssertionError("unreachable")

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        self._stub_not_implemented()
        raise AssertionError("unreachable")

    def exists(self, key: str) -> bool:
        self._stub_not_implemented()
        raise AssertionError("unreachable")

    def delete(self, key: str) -> bool:
        self._stub_not_implemented()
        raise AssertionError("unreachable")


def get_storage(*, strict: bool = False) -> StorageBackend:
    settings = get_settings()
    backend = (settings.storage_backend or "local").strip().lower()
    if backend == "local":
        return LocalStorageBackend(base_dir=settings.data_dir)
    if backend in ("s3", "r2"):
        return S3StorageBackend(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            is_r2=(backend == "r2"),
            strict=strict,
        )
    raise StorageConfigurationError(
        f"Unsupported STORAGE_BACKEND: {settings.storage_backend!r}. Expected 'local', 's3', or 'r2'."
    )
