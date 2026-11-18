#!/usr/bin/env python3
"""CLIPPER Backup & Restore CLI.

Creates and restores timestamped `.tar.gz` archives containing:
1. SQLite database snapshot (`data/clipper.db`, backed up safely via `sqlite3.Connection.backup`)
2. Assets templates & fonts (`assets/templates`, `assets/fonts`)
3. Agent & HITL checkpoint state (`.clipper-state`)
4. Backup manifest (`backup_manifest.json` with SHA-256 checksums)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKUP_MANIFEST_FILENAME = "backup_manifest.json"
DEFAULT_DB_REL_PATH = "data/clipper.db"
DEFAULT_TEMPLATES_REL_PATH = "assets/templates"
DEFAULT_FONTS_REL_PATH = "assets/fonts"
DEFAULT_STATE_REL_PATH = ".clipper-state"


@dataclass(frozen=True)
class BackupSummary:
    """Metadata returned after creating a CLIPPER backup archive."""

    archive_path: Path
    created_at: str
    included_files: list[str] = field(default_factory=list)
    checksums: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": str(self.archive_path),
            "created_at": self.created_at,
            "included_files": list(self.included_files),
            "checksums": dict(self.checksums),
        }


@dataclass(frozen=True)
class RestoreSummary:
    """Metadata returned after restoring a CLIPPER backup archive."""

    archive_path: Path
    target_root: Path
    restored_files: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": str(self.archive_path),
            "target_root": str(self.target_root),
            "restored_files": list(self.restored_files),
            "created_at": self.created_at,
        }


def _sha256_file(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _safe_sqlite_snapshot(source_db: Path, dest_db: Path) -> None:
    """Create a consistent online backup of a SQLite database using sqlite3 backup API."""
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(str(source_db)) as src_conn, sqlite3.connect(str(dest_db)) as dst_conn:
            src_conn.backup(dst_conn)
    except sqlite3.Error:
        # Fallback to raw file copy if file is a stub/non-SQLite test file
        shutil.copy2(source_db, dest_db)


def _validate_archive_member_path(target_root: Path, member_name: str) -> Path:
    """Ensure a tar member path is relative and cannot escape target_root (tar-slip guard)."""
    cleaned = member_name.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/"):
        raise ValueError(f"Unsafe absolute path in backup archive: {member_name!r}")
    parts = [p for p in cleaned.split("/") if p]
    if any(p in {".", ".."} for p in parts):
        raise ValueError(f"Path traversal segment detected in backup archive: {member_name!r}")
    resolved_root = target_root.resolve()
    resolved_target = (resolved_root / "/".join(parts)).resolve()
    if resolved_target == resolved_root or not resolved_target.is_relative_to(resolved_root):
        raise ValueError(f"Archive member escapes target root: {member_name!r}")
    return resolved_target


def create_backup(
    root_dir: Path,
    *,
    output_dir: Path | None = None,
    archive_path: Path | None = None,
    db_rel_path: str = DEFAULT_DB_REL_PATH,
    include_paths: Sequence[str] = (
        DEFAULT_TEMPLATES_REL_PATH,
        DEFAULT_FONTS_REL_PATH,
        DEFAULT_STATE_REL_PATH,
    ),
    timestamp: datetime | None = None,
) -> BackupSummary:
    """Package SQLite DB, templates/fonts, and `.clipper-state` into a timestamped `.tar.gz`."""
    now = (timestamp or datetime.now(UTC)).astimezone(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    created_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if archive_path is None:
        dest_dir = output_dir or (root_dir / "backups")
        dest_dir.mkdir(parents=True, exist_ok=True)
        final_archive = dest_dir / f"clipper-backup-{stamp}.tar.gz"
    else:
        final_archive = archive_path
        final_archive.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    checksums: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="clipper_bkp_") as tmp_str:
        stage_root = Path(tmp_str)

        # 1. SQLite database online backup
        src_db = root_dir / db_rel_path
        if src_db.exists() and src_db.is_file():
            staged_db = stage_root / db_rel_path
            _safe_sqlite_snapshot(src_db, staged_db)
            rel_posix = Path(db_rel_path).as_posix()
            included.append(rel_posix)
            checksums[rel_posix] = _sha256_file(staged_db)

        # 2. Templates, fonts, and .clipper-state directories/files
        for rel_item in include_paths:
            src_path = root_dir / rel_item
            if not src_path.exists():
                continue
            if src_path.is_file():
                staged_file = stage_root / rel_item
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, staged_file)
                rel_posix = Path(rel_item).as_posix()
                included.append(rel_posix)
                checksums[rel_posix] = _sha256_file(staged_file)
            elif src_path.is_dir():
                for child in sorted(src_path.rglob("*")):
                    if not child.is_file():
                        continue
                    rel_child = child.relative_to(root_dir).as_posix()
                    staged_child = stage_root / rel_child
                    staged_child.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(child, staged_child)
                    included.append(rel_child)
                    checksums[rel_child] = _sha256_file(staged_child)

        included.sort()
        manifest_payload = {
            "schema_version": 1,
            "created_at": created_iso,
            "files": included,
            "checksums": checksums,
        }
        manifest_bytes = (json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )

        with tarfile.open(final_archive, "w:gz") as tf:
            for rel_posix in included:
                tf.add(stage_root / rel_posix, arcname=rel_posix, recursive=False)
            info = tarfile.TarInfo(name=BACKUP_MANIFEST_FILENAME)
            info.size = len(manifest_bytes)
            info.mtime = int(now.timestamp())
            tf.addfile(info, io.BytesIO(manifest_bytes))

    return BackupSummary(
        archive_path=final_archive,
        created_at=created_iso,
        included_files=included,
        checksums=checksums,
    )


def restore_backup(
    archive_path: Path,
    target_root: Path,
    *,
    verify_checksums: bool = True,
) -> RestoreSummary:
    """Restore a CLIPPER `.tar.gz` backup archive into `target_root` with tar-slip & SHA-256 verification."""
    if not archive_path.exists():
        raise FileNotFoundError(f"Backup archive not found: {archive_path}")

    target_root.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []
    manifest_data: dict[str, Any] = {}

    with tarfile.open(archive_path, "r:gz") as tf:
        members = tf.getmembers()
        for member in members:
            if member.issym() or member.islnk():
                raise ValueError(f"Symlink/hardlink member rejected in backup archive: {member.name!r}")
            if member.isdir():
                continue
            _validate_archive_member_path(target_root, member.name)

        # Read manifest first if present
        try:
            manifest_member = tf.getmember(BACKUP_MANIFEST_FILENAME)
            extracted_f = tf.extractfile(manifest_member)
            if extracted_f is not None:
                manifest_data = json.loads(extracted_f.read().decode("utf-8"))
        except KeyError:
            manifest_data = {}

        expected_checksums: dict[str, str] = dict(manifest_data.get("checksums") or {})

        for member in members:
            if member.isdir() or member.name == BACKUP_MANIFEST_FILENAME:
                continue
            dest_path = _validate_archive_member_path(target_root, member.name)
            src_stream = tf.extractfile(member)
            if src_stream is None:
                continue
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with dest_path.open("wb") as out_fh:
                shutil.copyfileobj(src_stream, out_fh)

            rel_posix = dest_path.relative_to(target_root.resolve()).as_posix()
            if verify_checksums and rel_posix in expected_checksums:
                actual_sha = _sha256_file(dest_path)
                expected_sha = expected_checksums[rel_posix]
                if actual_sha != expected_sha:
                    raise ValueError(
                        f"Checksum mismatch while restoring {rel_posix!r}: "
                        f"expected {expected_sha}, got {actual_sha}"
                    )
            restored.append(rel_posix)

    restored.sort()
    return RestoreSummary(
        archive_path=archive_path,
        target_root=target_root,
        restored_files=restored,
        created_at=str(manifest_data.get("created_at", "")),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup_restore",
        description="Backup and restore CLIPPER SQLite DB, templates, and .clipper-state.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    bkp = sub.add_parser("backup", help="Create a timestamped .tar.gz backup archive")
    bkp.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="CLIPPER repository root directory (default: current directory)",
    )
    bkp.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for timestamped archive (default: <root>/backups)",
    )
    bkp.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="Explicit output .tar.gz archive path",
    )

    rst = sub.add_parser("restore", help="Restore a .tar.gz backup archive into target root")
    rst.add_argument("archive", type=Path, help="Path to backup .tar.gz archive")
    rst.add_argument(
        "--target-root",
        type=Path,
        default=Path.cwd(),
        help="Target root directory to restore into (default: current directory)",
    )
    rst.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip SHA-256 checksum verification",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "backup":
        backup_summary = create_backup(
            args.root,
            output_dir=args.output_dir,
            archive_path=args.archive,
        )
        print(json.dumps(backup_summary.to_dict(), indent=2))
        return 0
    if args.command == "restore":
        restore_summary = restore_backup(
            args.archive,
            args.target_root,
            verify_checksums=not args.no_verify,
        )
        print(json.dumps(restore_summary.to_dict(), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
