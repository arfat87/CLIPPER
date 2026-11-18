from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts.backup_restore import (
    create_backup,
    restore_backup,
)
from scripts.backup_restore import (
    main as backup_cli_main,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_cpu_instructions_and_security() -> None:
    dockerfile_cpu = REPO_ROOT / "docker" / "Dockerfile.cpu"
    assert dockerfile_cpu.exists()
    content = dockerfile_cpu.read_text(encoding="utf-8")

    # Multi-stage Node 22 frontend build + Python 3.11 slim runtime
    assert "FROM node:22" in content
    assert "FROM python:3.11-slim" in content

    # Required toolchain & multi-script Noto font packages
    for pkg in (
        "ffmpeg",
        "libass9",
        "fontconfig",
        "fonts-noto-core",
        "fonts-noto-cjk",
        "fonts-noto-unhinted",
    ):
        assert pkg in content, f"Missing package {pkg} in Dockerfile.cpu"

    # Non-root user and /api/v1/health healthcheck
    assert "useradd" in content
    assert "USER clipper" in content
    assert "HEALTHCHECK" in content
    assert "/api/v1/health" in content


def test_dockerfile_cuda_instructions_and_gpu_defaults() -> None:
    dockerfile_cuda = REPO_ROOT / "docker" / "Dockerfile.cuda"
    assert dockerfile_cuda.exists()
    content = dockerfile_cuda.read_text(encoding="utf-8")

    assert "FROM nvidia/cuda:12." in content
    assert "python3.11" in content
    assert "CLIPPER_DEVICE=cuda" in content
    for pkg in ("ffmpeg", "libass9", "fontconfig", "fonts-noto-core", "fonts-noto-unhinted"):
        assert pkg in content, f"Missing package {pkg} in Dockerfile.cuda"
    assert "USER clipper" in content
    assert "HEALTHCHECK" in content
    assert "/api/v1/health" in content


def test_caddyfile_sse_flush_interval_and_security_headers() -> None:
    caddyfile = REPO_ROOT / "docker" / "Caddyfile"
    assert caddyfile.exists()
    content = caddyfile.read_text(encoding="utf-8")

    assert "/api/v1/jobs/*/events" in content
    assert "flush_interval -1" in content
    assert "reverse_proxy api:8000" in content
    assert "X-Content-Type-Options" in content
    assert "Strict-Transport-Security" in content
    assert "X-Frame-Options" in content


def test_docker_compose_local_and_cloud_override_yaml_structure() -> None:
    local_compose_path = REPO_ROOT / "docker-compose.yml"
    cloud_compose_path = REPO_ROOT / "docker" / "docker-compose.cloud.yml"
    assert local_compose_path.exists()
    assert cloud_compose_path.exists()

    local_cfg = yaml.safe_load(local_compose_path.read_text(encoding="utf-8"))
    assert isinstance(local_cfg, dict)
    services = local_cfg.get("services", {})
    assert "api" in services
    assert "worker" in services
    assert "caddy" in services
    assert "worker-gpu" in services
    assert services["worker-gpu"].get("profiles") == ["gpu"]

    api_volumes = " ".join(str(v) for v in services["api"].get("volumes", []))
    assert "./data:/app/data" in api_volumes
    assert "./assets:/app/assets" in api_volumes
    assert "./.clipper-state:/app/.clipper-state" in api_volumes
    assert "healthcheck" in services["api"]

    cloud_cfg = yaml.safe_load(cloud_compose_path.read_text(encoding="utf-8"))
    assert isinstance(cloud_cfg, dict)
    cloud_services = cloud_cfg.get("services", {})
    assert cloud_services["postgres"]["image"] == "postgres:16-alpine"
    assert cloud_services["redis"]["image"] == "redis:7-alpine"

    api_env = cloud_services["api"]["environment"]
    assert "CLIPPER_DB_URL" in api_env
    assert "CLIPPER_REDIS_URL" in api_env
    assert "CLIPPER_S3_BUCKET" in api_env
    assert "CLIPPER_S3_ENDPOINT" in api_env


def test_backup_and_restore_round_trip_and_cli(tmp_path: Path) -> None:
    source_root = tmp_path / "source_repo"
    (source_root / "data").mkdir(parents=True)
    (source_root / "assets" / "templates" / "clean_minimal").mkdir(parents=True)
    (source_root / "assets" / "fonts").mkdir(parents=True)
    (source_root / ".clipper-state" / "blockers").mkdir(parents=True)

    # Create a real SQLite DB with sample rows
    db_file = source_root / "data" / "clipper.db"
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO jobs (id, status) VALUES ('job_101', 'DONE')")
        conn.commit()

    tpl_file = source_root / "assets" / "templates" / "clean_minimal" / "template.json"
    tpl_file.write_text(json.dumps({"id": "clean_minimal", "status": "published"}), encoding="utf-8")

    state_file = source_root / ".clipper-state" / "state.json"
    state_file.write_text(json.dumps({"current_phase": 11, "blocked_tasks": {}}), encoding="utf-8")

    blocker_file = source_root / ".clipper-state" / "blockers" / "BLK-0001.yaml"
    blocker_file.write_text("id: BLK-0001\nstatus: RESOLVED\n", encoding="utf-8")

    fixed_ts = datetime(2026, 11, 18, 19, 45, 0, tzinfo=UTC)
    bkp_summary = create_backup(
        source_root,
        output_dir=tmp_path / "backups",
        timestamp=fixed_ts,
    )
    assert bkp_summary.archive_path.exists()
    assert bkp_summary.archive_path.name == "clipper-backup-20261118T194500Z.tar.gz"
    assert "data/clipper.db" in bkp_summary.included_files
    assert "assets/templates/clean_minimal/template.json" in bkp_summary.included_files
    assert ".clipper-state/state.json" in bkp_summary.included_files
    assert ".clipper-state/blockers/BLK-0001.yaml" in bkp_summary.included_files

    # Restore into a clean directory and verify SQLite table & files round-trip intact
    restore_root = tmp_path / "restored_repo"
    rst_summary = restore_backup(bkp_summary.archive_path, restore_root)
    assert rst_summary.restored_files == bkp_summary.included_files

    restored_db = restore_root / "data" / "clipper.db"
    assert restored_db.exists()
    with sqlite3.connect(str(restored_db)) as conn:
        rows = conn.execute("SELECT id, status FROM jobs").fetchall()
    assert rows == [("job_101", "DONE")]

    restored_tpl = json.loads(
        (restore_root / "assets" / "templates" / "clean_minimal" / "template.json").read_text(
            encoding="utf-8"
        )
    )
    assert restored_tpl["id"] == "clean_minimal"
    assert (restore_root / ".clipper-state" / "blockers" / "BLK-0001.yaml").exists()

    # Verify CLI entrypoint round-trip
    cli_archive = tmp_path / "cli_backup.tar.gz"
    rc_bkp = backup_cli_main(
        ["backup", "--root", str(source_root), "--archive", str(cli_archive)]
    )
    assert rc_bkp == 0
    assert cli_archive.exists()

    cli_restore_dir = tmp_path / "cli_restored"
    rc_rst = backup_cli_main(
        ["restore", str(cli_archive), "--target-root", str(cli_restore_dir)]
    )
    assert rc_rst == 0
    assert (cli_restore_dir / "data" / "clipper.db").exists()


def test_restore_backup_rejects_tar_slip_traversal(tmp_path: Path) -> None:
    evil_archive = tmp_path / "evil.tar.gz"
    with tarfile.open(evil_archive, "w:gz") as tf:
        payload = b"malicious"
        info = tarfile.TarInfo(name="../../escape.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Path traversal"):
        restore_backup(evil_archive, tmp_path / "target")
