from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import config as config_mod
from backend.app.config import get_settings
from backend.app.services.storage import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
    StorageConfigurationError,
    StorageError,
    get_storage,
)
from hitl.models import BlockerRequired


def test_local_storage_job_and_stage_dirs(tmp_path: Path):
    storage = LocalStorageBackend(base_dir=tmp_path)
    assert isinstance(storage, StorageBackend)

    jdir = storage.job_dir("job_123")
    assert jdir == (tmp_path / "jobs" / "job_123").resolve()
    for sub in ("source", "audio", "asr", "highlights", "clips", "renders"):
        assert (jdir / sub).is_dir()

    sdir = storage.stage_dir("job_123", "package")
    assert sdir == (tmp_path / "jobs" / "job_123" / "package").resolve()
    assert sdir.is_dir()

    storage.delete_job("job_123")
    assert not jdir.exists()
    # Idempotent delete on missing job
    storage.delete_job("job_123")


def test_local_storage_atomic_rw_exists_delete_and_traversal_guard(tmp_path: Path):
    storage = LocalStorageBackend(base_dir=tmp_path)

    # Atomic bytes write/read
    raw_path = storage.write_bytes("jobs/j1/asr/data.bin", b"\x00\x01\x02clipper")
    assert raw_path.exists()
    assert storage.exists("jobs/j1/asr/data.bin") is True
    assert storage.read_bytes("jobs/j1/asr/data.bin") == b"\x00\x01\x02clipper"
    # No leftover temp files
    assert [p.name for p in raw_path.parent.iterdir()] == ["data.bin"]

    # Multilingual text write/read + overwrite
    storage.write_text("jobs/j1/asr/captions.txt", "नमस्ते বাংলা")
    assert storage.read_text("jobs/j1/asr/captions.txt") == "नमस्ते বাংলা"
    storage.write_text("jobs/j1/asr/captions.txt", "updated")
    assert storage.read_text("jobs/j1/asr/captions.txt") == "updated"

    assert storage.delete("jobs/j1/asr/captions.txt") is True
    assert storage.exists("jobs/j1/asr/captions.txt") is False
    assert storage.delete("jobs/j1/asr/captions.txt") is False

    with pytest.raises(FileNotFoundError):
        storage.read_bytes("jobs/j1/asr/missing.bin")

    # Path traversal and invalid key guards
    for bad_key in ("", "../outside.txt", "jobs/../../etc/passwd", "/etc/passwd"):
        with pytest.raises(StorageError):
            storage.write_bytes(bad_key, b"x")

    for bad_seg in ("", ".", "..", "a/b", "a\\b"):
        with pytest.raises(StorageError):
            storage.job_dir(bad_seg)
        with pytest.raises(StorageError):
            storage.stage_dir("job_1", bad_seg)


def test_get_storage_selects_backend_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    try:
        storage = get_storage()
        assert isinstance(storage, LocalStorageBackend)
        assert storage.base_dir == (tmp_path / "data").resolve()
    finally:
        get_settings.cache_clear()


def test_s3_and_r2_storage_stub_unconfigured_and_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(config_mod, "ROOT_DIR", tmp_path)
    for key in (
        "S3_ENDPOINT",
        "S3_BUCKET",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "NOTIFY_SMTP_USER",
        "NOTIFY_SMTP_APP_PASSWORD",
        "OWNER_EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    get_settings.cache_clear()
    try:
        with pytest.raises(StorageConfigurationError, match="S3_BUCKET"):
            get_storage()

        with pytest.raises(BlockerRequired) as exc_info:
            get_storage(strict=True)
        assert exc_info.value.blocker.task_id == "T-1.2"
        assert (tmp_path / "BLOCKERS.md").exists()

        # R2 also requires S3_ENDPOINT
        monkeypatch.setenv("STORAGE_BACKEND", "r2")
        get_settings.cache_clear()
        with pytest.raises(StorageConfigurationError, match="S3_ENDPOINT"):
            get_storage()

        # Configured S3/R2 returns S3StorageBackend stub conforming to protocol
        monkeypatch.setenv("S3_ENDPOINT", "https://r2.example.com")
        monkeypatch.setenv("S3_BUCKET", "clipper-prod")
        monkeypatch.setenv("S3_ACCESS_KEY", "ak_test")
        monkeypatch.setenv("S3_SECRET_KEY", "sk_test")
        get_settings.cache_clear()

        s3_storage = get_storage()
        assert isinstance(s3_storage, S3StorageBackend)
        assert isinstance(s3_storage, StorageBackend)
        with pytest.raises(NotImplementedError):
            s3_storage.write_bytes("jobs/j1/test.txt", b"hello")

        # Invalid backend name fails cleanly
        monkeypatch.setenv("STORAGE_BACKEND", "ftp")
        get_settings.cache_clear()
        with pytest.raises(StorageConfigurationError, match="Unsupported STORAGE_BACKEND"):
            get_storage()
    finally:
        get_settings.cache_clear()
