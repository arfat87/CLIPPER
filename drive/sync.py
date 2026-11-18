from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]

from drive.client import (
    ChangesPage,
    DriveClient,
    DriveError,
    DriveExportTooLargeError,
    DriveFileMetadata,
    DriveRateLimitError,
    ListFilesPage,
)
from drive.events import (
    EVENT_FILE_ERROR,
    EVENT_FILE_REMOVED,
    EVENT_FILE_STALE,
    EVENT_FILE_SYNCED,
    EVENT_SYNC_COMPLETED,
    DriveEventBus,
    DriveSyncEvent,
    EventCallback,
)
from drive.routing import (
    CLIPPER_YAML_FILENAME,
    MAX_CLIPPER_YAML_BYTES,
    FolderRoutingOverride,
    classify_default_route,
    parse_clipper_yaml,
    resolve_folder_routing,
    validate_manifest_invariants,
)
from drive.security import (
    DrivePolicyConfig,
    assert_safe_mirror_path,
    check_download_size,
    check_export_size,
    is_skipped_extension,
    load_drive_config,
    sanitize_mirror_path,
    sanitize_segment,
)
from drive.tree import write_tree_md

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "contracts" / "schemas" / "drive_manifest.schema.json"
)
REMOVED_RETENTION_DAYS = 7

_EXPORT_EXTENSIONS: dict[str, str] = {
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/pdf": ".pdf",
}


@dataclass
class SyncResult:
    """Outcome summary of a single Drive sync pass."""

    mode: str  # "full" | "incremental"
    manifest: dict[str, Any]
    manifest_path: Path
    added_ids: list[str] = field(default_factory=list)
    changed_ids: list[str] = field(default_factory=list)
    removed_ids: list[str] = field(default_factory=list)
    error_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tree_path: Path | None = None
    events: list[DriveSyncEvent] = field(default_factory=list)


def classify_route(rel_path: str, routing: tuple[dict[str, str], ...]) -> str:
    """Determine route from first workspace folder segment using config/drive.yaml rules."""
    return classify_default_route(rel_path, routing)


def validate_manifest(
    manifest: dict[str, Any],
    schema_path: Path | None = None,
) -> None:
    """Validate manifest dict against contracts/schemas/drive_manifest.schema.json."""
    path = schema_path or DEFAULT_SCHEMA_PATH
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=manifest, schema=schema)


def _utc_now(now_fn: Callable[[], datetime] | None = None) -> datetime:
    dt = now_fn() if now_fn is not None else datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime:
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = f"{cleaned[:-1]}+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _file_md5(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "md5").hexdigest()


def _atomic_write_text(tmp_dir: Path, target_path: Path, text: str) -> None:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    staged = tmp_dir / f".stage_{uuid.uuid4().hex}_{target_path.name}"
    staged.write_text(text, encoding="utf-8")
    os.replace(staged, target_path)


def _atomic_stage_move(staged_file: Path, final_path: Path) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged_file, final_path)


def _with_backoff(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 5,
    base_delay: float = 0.5,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except DriveRateLimitError:
            if attempt >= max_attempts:
                raise
            jitter = random.uniform(0.0, base_delay * 0.25)
            sleep_fn(base_delay * (2 ** (attempt - 1)) + jitter)


class DriveSync:
    """Read-only Google Drive crawler, incremental syncer, exporter, and atomic mirror."""

    def __init__(
        self,
        client: DriveClient,
        root_folder_id: str,
        *,
        mirror_dir: Path,
        root_dir: Path | None = None,
        policy: DrivePolicyConfig | None = None,
        schema_path: Path | None = None,
        tree_output_path: Path | None = None,
        on_event: EventCallback | None = None,
        event_bus: DriveEventBus | None = None,
        max_md5_retries: int = 3,
        max_rate_retries: int = 5,
        backoff_base_seconds: float = 0.5,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if not root_folder_id.strip():
            raise ValueError("DRIVE_ROOT_FOLDER_ID must be non-empty.")
        self.client = client
        self.root_folder_id = root_folder_id.strip()
        self.mirror_dir = mirror_dir
        self.root_dir = root_dir
        self.policy = policy or load_drive_config()
        self.schema_path = schema_path or DEFAULT_SCHEMA_PATH
        self.tree_output_path = tree_output_path
        self.on_event = on_event
        self.event_bus = event_bus
        self.max_md5_retries = max(1, max_md5_retries)
        self.max_rate_retries = max(1, max_rate_retries)
        self.backoff_base_seconds = backoff_base_seconds
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn

        self.tmp_dir = self.mirror_dir / ".tmp"
        self.manifest_path = self.mirror_dir / "manifest.json"
        self.state_path = self.mirror_dir / ".drive_state.json"

    def _mirror_path_label(self, rel_mirror_path: str) -> str:
        resolved_target = assert_safe_mirror_path(self.mirror_dir, rel_mirror_path)
        if self.root_dir is not None:
            try:
                return resolved_target.relative_to(self.root_dir.resolve()).as_posix()
            except ValueError:
                pass
        return f"{self.mirror_dir.name}/{rel_mirror_path}"

    def _local_path_for_record(self, record: dict[str, Any]) -> Path | None:
        rel_mirror = record.get("_mirror_rel")
        if not rel_mirror:
            return None
        return assert_safe_mirror_path(self.mirror_dir, str(rel_mirror))

    def _call(self, fn: Callable[[], Any]) -> Any:
        return _with_backoff(
            fn,
            max_attempts=self.max_rate_retries,
            base_delay=self.backoff_base_seconds,
            sleep_fn=self.sleep_fn,
        )

    def _load_state(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("root_folder_id") != self.root_folder_id:
            return None
        if not data.get("start_page_token"):
            return None
        return data

    def _build_paths_for_item(
        self,
        item: DriveFileMetadata,
        parent_segments: list[str],
        used_workspace_paths: set[str],
        used_mirror_paths: set[str],
        *,
        extension_override: str | None = None,
    ) -> tuple[str, str]:
        workspace_path = sanitize_mirror_path(
            [*parent_segments, item.name],
            item.id,
            used_paths=used_workspace_paths,
        )
        mirror_rel = sanitize_mirror_path(
            [*parent_segments, item.name],
            item.id,
            used_paths=used_mirror_paths,
            extension_override=extension_override,
        )
        return workspace_path, mirror_rel

    def _folder_chain(
        self,
        folder_id: str,
        folder_parents: dict[str, str | None],
    ) -> list[str]:
        chain: list[str] = []
        visited: set[str] = set()
        cur: str | None = folder_id
        while cur is not None and cur not in visited:
            visited.add(cur)
            chain.append(cur)
            if cur == self.root_folder_id:
                break
            cur = folder_parents.get(cur)
        chain.reverse()
        return chain

    def _load_clipper_yaml_item(
        self,
        item: DriveFileMetadata,
        folder_label: str,
        warnings: list[str],
    ) -> FolderRoutingOverride:
        location = folder_label or "<root>"
        if item.size is not None and item.size > MAX_CLIPPER_YAML_BYTES:
            msg = (
                f"Invalid .clipper.yaml in '{location}': size {item.size} exceeds "
                f"{MAX_CLIPPER_YAML_BYTES} bytes; falling back to default routing."
            )
            logger.warning(msg)
            warnings.append(msg)
            return FolderRoutingOverride()

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        staged = self.tmp_dir / f"{uuid.uuid4().hex}_clipper.yaml"
        try:
            try:
                self._call(lambda: self.client.download_file(item.id, staged))
            except DriveError as exc:
                msg = (
                    f"Failed to read .clipper.yaml in '{location}' ({exc}); "
                    "falling back to default routing."
                )
                logger.warning(msg)
                warnings.append(msg)
                return FolderRoutingOverride()
            raw_bytes = staged.read_bytes()
        finally:
            staged.unlink(missing_ok=True)

        parsed = parse_clipper_yaml(raw_bytes, folder_path=location)
        for warn in parsed.warnings:
            logger.warning(warn)
            warnings.append(warn)
        return parsed.override

    def _preserve_stale_record(
        self,
        base_record: dict[str, Any],
        existing_record: dict[str, Any],
        target_local: Path,
        mirror_rel: str,
        error_msg: str,
    ) -> dict[str, Any] | None:
        if existing_record.get("status") != "SYNCED":
            return None
        prev_local = self._local_path_for_record(existing_record)
        if prev_local is None or not prev_local.exists():
            return None
        if prev_local.resolve() != target_local.resolve():
            _atomic_stage_move(prev_local, target_local)
        base_record["mirror_path"] = self._mirror_path_label(mirror_rel)
        base_record["size"] = existing_record.get("size")
        base_record["md5"] = existing_record.get("md5")
        base_record["exported_as"] = existing_record.get("exported_as")
        base_record["status"] = "STALE"
        base_record["error"] = error_msg
        base_record["_mirror_rel"] = mirror_rel
        return base_record

    def _sync_file_content(
        self,
        *,
        item: DriveFileMetadata,
        content_file_id: str,
        parent_segments: list[str],
        used_workspace_paths: set[str],
        used_mirror_paths: set[str],
        existing_record: dict[str, Any] | None = None,
        folder_id: str | None = None,
        folder_chain: list[str] | None = None,
        folder_overrides: dict[str, FolderRoutingOverride] | None = None,
        folder_segments: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        is_native = item.mime_type in self.policy.export_map
        target_export_mime = self.policy.export_map.get(item.mime_type)
        ext_override = _EXPORT_EXTENSIONS.get(target_export_mime, "") if target_export_mime else None

        workspace_path, mirror_rel = self._build_paths_for_item(
            item,
            parent_segments,
            used_workspace_paths,
            used_mirror_paths,
            extension_override=ext_override,
        )
        if folder_chain is not None and folder_overrides is not None:
            route, is_ignored = resolve_folder_routing(
                workspace_path,
                item.name,
                default_routing=self.policy.routing,
                folder_chain=folder_chain,
                folder_overrides=folder_overrides,
                folder_segments=folder_segments,
            )
        else:
            route = classify_route(workspace_path, self.policy.routing)
            is_ignored = False

        target_local = assert_safe_mirror_path(self.mirror_dir, mirror_rel)

        base_record: dict[str, Any] = {
            "id": item.id,
            "name": item.name,
            "mime": item.mime_type,
            "is_folder": False,
            "path": workspace_path,
            "mirror_path": None,
            "size": item.size,
            "md5": item.md5_checksum,
            "modified_time": item.modified_time,
            "head_revision_id": item.head_revision_id,
            "route": route,
            "status": "SYNCED",
            "error": None,
            "exported_as": None,
            "_parents": list(item.parents),
            "_folder_id": folder_id,
            "_mirror_rel": None,
            "_content_file_id": content_file_id,
        }

        if is_skipped_extension(item.name, self.policy.skip_extensions) or is_ignored:
            if existing_record is not None and existing_record.get("_mirror_rel"):
                prev_local = self._local_path_for_record(existing_record)
                if prev_local is not None and prev_local.exists():
                    prev_local.unlink(missing_ok=True)
            base_record["status"] = "SKIPPED"
            return base_record

        # Check if existing local copy is still valid and unchanged
        if existing_record is not None and existing_record.get("status") == "SYNCED":
            prev_local = self._local_path_for_record(existing_record)
            same_revision = (
                (not is_native and existing_record.get("md5") == item.md5_checksum and existing_record.get("size") == item.size)
                or (
                    is_native
                    and existing_record.get("modified_time") == item.modified_time
                    and existing_record.get("head_revision_id") == item.head_revision_id
                )
            )
            if same_revision and prev_local is not None and prev_local.exists():
                if prev_local.resolve() != target_local.resolve():
                    _atomic_stage_move(prev_local, target_local)
                base_record["mirror_path"] = self._mirror_path_label(mirror_rel)
                base_record["size"] = existing_record.get("size")
                base_record["md5"] = existing_record.get("md5")
                base_record["exported_as"] = existing_record.get("exported_as")
                base_record["_mirror_rel"] = mirror_rel
                return base_record

        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        if is_native and target_export_mime is not None:
            used_mime = target_export_mime
            ext = _EXPORT_EXTENSIONS.get(used_mime, ".bin")
            staged = self.tmp_dir / f"{uuid.uuid4().hex}{ext}"
            try:
                try:
                    written = self._call(
                        lambda: self.client.export_file(content_file_id, used_mime, staged)
                    )
                except DriveExportTooLargeError:
                    staged.unlink(missing_ok=True)
                    base_record["status"] = "ERROR"
                    base_record["error"] = "too_large"
                    base_record["exported_as"] = used_mime
                    return base_record
                except DriveRateLimitError as rate_exc:
                    staged.unlink(missing_ok=True)
                    if existing_record is not None:
                        stale_rec = self._preserve_stale_record(
                            base_record, existing_record, target_local, mirror_rel, str(rate_exc)
                        )
                        if stale_rec is not None:
                            return stale_rec
                    base_record["status"] = "ERROR"
                    base_record["error"] = str(rate_exc)
                    base_record["exported_as"] = used_mime
                    return base_record
                except DriveError as first_exc:
                    staged.unlink(missing_ok=True)
                    if (
                        item.mime_type == "application/vnd.google-apps.document"
                        and used_mime == "text/markdown"
                    ):
                        used_mime = "text/plain"
                        ext = _EXPORT_EXTENSIONS[used_mime]
                        staged = self.tmp_dir / f"{uuid.uuid4().hex}{ext}"
                        # Recompute mirror path extension for fallback .txt
                        used_mirror_paths.discard(mirror_rel.lower())
                        mirror_rel = sanitize_mirror_path(
                            [*parent_segments, item.name],
                            item.id,
                            used_paths=used_mirror_paths,
                            extension_override=ext,
                        )
                        target_local = assert_safe_mirror_path(self.mirror_dir, mirror_rel)
                        written = self._call(
                            lambda: self.client.export_file(content_file_id, used_mime, staged)
                        )
                    else:
                        base_record["status"] = "ERROR"
                        base_record["error"] = str(first_exc)
                        base_record["exported_as"] = used_mime
                        return base_record
            except DriveExportTooLargeError:
                staged.unlink(missing_ok=True)
                base_record["status"] = "ERROR"
                base_record["error"] = "too_large"
                base_record["exported_as"] = used_mime
                return base_record
            except DriveRateLimitError as rate_exc:
                staged.unlink(missing_ok=True)
                if existing_record is not None:
                    stale_rec = self._preserve_stale_record(
                        base_record, existing_record, target_local, mirror_rel, str(rate_exc)
                    )
                    if stale_rec is not None:
                        return stale_rec
                base_record["status"] = "ERROR"
                base_record["error"] = str(rate_exc)
                base_record["exported_as"] = used_mime
                return base_record
            except DriveError as exc:
                staged.unlink(missing_ok=True)
                base_record["status"] = "ERROR"
                base_record["error"] = str(exc)
                base_record["exported_as"] = used_mime
                return base_record

            ok_size, size_err = check_export_size(written, self.policy.max_export_mb)
            if not ok_size:
                staged.unlink(missing_ok=True)
                base_record["status"] = "ERROR"
                base_record["error"] = size_err or "too_large"
                base_record["size"] = written
                base_record["exported_as"] = used_mime
                return base_record

            _atomic_stage_move(staged, target_local)
            base_record["mirror_path"] = self._mirror_path_label(mirror_rel)
            base_record["size"] = written
            base_record["md5"] = None
            base_record["exported_as"] = used_mime
            base_record["_mirror_rel"] = mirror_rel
            return base_record

        # Binary download
        ok_cap, cap_err = check_download_size(item.size, self.policy.max_download_mb)
        if not ok_cap:
            base_record["status"] = "ERROR"
            base_record["error"] = cap_err or "too_large"
            return base_record

        last_err: str | None = None
        for _attempt in range(self.max_md5_retries):
            staged = self.tmp_dir / f"{uuid.uuid4().hex}.part"
            try:
                written = self._call(lambda: self.client.download_file(content_file_id, staged))
            except DriveRateLimitError as rate_exc:
                staged.unlink(missing_ok=True)
                if existing_record is not None:
                    stale_rec = self._preserve_stale_record(
                        base_record, existing_record, target_local, mirror_rel, str(rate_exc)
                    )
                    if stale_rec is not None:
                        return stale_rec
                last_err = str(rate_exc)
                break
            except DriveError as exc:
                staged.unlink(missing_ok=True)
                last_err = str(exc)
                break

            ok_actual, actual_err = check_download_size(written, self.policy.max_download_mb)
            if not ok_actual:
                staged.unlink(missing_ok=True)
                last_err = actual_err or "too_large"
                break

            actual_md5 = _file_md5(staged)
            if item.md5_checksum and actual_md5 != item.md5_checksum.lower():
                staged.unlink(missing_ok=True)
                last_err = f"md5_mismatch: expected {item.md5_checksum}, got {actual_md5}"
                continue

            _atomic_stage_move(staged, target_local)
            base_record["mirror_path"] = self._mirror_path_label(mirror_rel)
            base_record["size"] = written
            base_record["md5"] = actual_md5
            base_record["_mirror_rel"] = mirror_rel
            return base_record

        base_record["status"] = "ERROR"
        base_record["error"] = last_err or "download_failed"
        return base_record

    def _crawl_subtree(
        self,
        start_folders: list[tuple[str, list[str]]],
        folder_ids: set[str],
        folder_segments: dict[str, list[str]],
        folder_parents: dict[str, str | None],
        visited_folders: set[str],
    ) -> tuple[
        list[tuple[DriveFileMetadata, str, list[str]]],
        list[tuple[DriveFileMetadata, str, list[str]]],
        list[tuple[DriveFileMetadata, str, list[str]]],
    ]:
        queue: deque[tuple[str, list[str]]] = deque(start_folders)
        discovered_files: list[tuple[DriveFileMetadata, str, list[str]]] = []
        discovered_shortcuts: list[tuple[DriveFileMetadata, str, list[str]]] = []
        discovered_configs: list[tuple[DriveFileMetadata, str, list[str]]] = []

        while queue:
            cur_id, cur_segs = queue.popleft()
            if cur_id in visited_folders:
                continue
            visited_folders.add(cur_id)

            page_token: str | None = None
            while True:
                def _fetch_files(
                    tok: str | None = page_token,
                    fid: str = cur_id,
                ) -> ListFilesPage:
                    return self.client.list_files(
                        fid, page_token=tok, page_size=self.policy.page_size
                    )

                page: ListFilesPage = self._call(_fetch_files)
                for item in page.files:
                    if item.trashed:
                        continue
                    if item.is_folder:
                        seg = sanitize_segment(item.name, item.id)
                        child_segs = [*cur_segs, seg]
                        folder_ids.add(item.id)
                        folder_segments[item.id] = child_segs
                        folder_parents[item.id] = cur_id
                        queue.append((item.id, child_segs))
                    elif item.is_shortcut:
                        discovered_shortcuts.append((item, cur_id, cur_segs))
                    elif item.name == CLIPPER_YAML_FILENAME:
                        discovered_configs.append((item, cur_id, cur_segs))
                    else:
                        discovered_files.append((item, cur_id, cur_segs))

                if not page.next_page_token:
                    break
                page_token = page.next_page_token

        return discovered_files, discovered_shortcuts, discovered_configs

    def _emit_events(self, events: list[DriveSyncEvent]) -> None:
        for ev in events:
            if self.event_bus is not None:
                self.event_bus.emit(ev)
            if self.on_event is not None:
                self.on_event(ev)

    def _build_and_commit_manifest(
        self,
        *,
        mode: str,
        start_page_token: str,
        folder_ids: set[str],
        folder_segments: dict[str, list[str]],
        folder_parents: dict[str, str | None],
        folder_overrides: dict[str, FolderRoutingOverride],
        override_files_by_id: dict[str, dict[str, Any]],
        records_by_id: dict[str, dict[str, Any]],
        added_ids: list[str],
        changed_ids: list[str],
        removed_ids: list[str],
        warnings: list[str],
    ) -> SyncResult:
        now = _utc_now(self.now_fn)
        cutoff = now - timedelta(days=REMOVED_RETENTION_DAYS)

        # Enforce 7-day retention on REMOVED records
        final_records: dict[str, dict[str, Any]] = {}
        active_paths: set[str] = {
            str(rec["path"])
            for rec in records_by_id.values()
            if rec.get("status") != "REMOVED" and rec.get("path")
        }
        for fid, rec in records_by_id.items():
            if rec.get("status") == "REMOVED":
                removed_at_raw = rec.get("_removed_at")
                if (removed_at_raw and _parse_iso(str(removed_at_raw)) <= cutoff) or (
                    rec.get("path") in active_paths
                ):
                    local_path = self._local_path_for_record(rec)
                    if local_path is not None and local_path.exists() and rec.get("path") not in active_paths:
                        local_path.unlink(missing_ok=True)
                    continue
            final_records[fid] = rec

        manifest_files: list[dict[str, Any]] = []
        error_ids: list[str] = []
        total_bytes = 0
        unsorted_count = 0
        active_file_count = 0

        for fid in sorted(final_records, key=lambda k: (final_records[k]["path"], k)):
            rec = final_records[fid]
            public_rec = {
                "id": rec["id"],
                "name": rec["name"],
                "mime": rec["mime"],
                "is_folder": bool(rec.get("is_folder", False)),
                "path": rec["path"],
                "mirror_path": rec.get("mirror_path"),
                "size": rec.get("size"),
                "md5": rec.get("md5"),
                "modified_time": rec.get("modified_time", ""),
                "head_revision_id": rec.get("head_revision_id"),
                "route": rec["route"],
                "status": rec["status"],
                "error": rec.get("error"),
                "exported_as": rec.get("exported_as"),
            }
            manifest_files.append(public_rec)
            if rec["status"] != "REMOVED":
                active_file_count += 1
                if isinstance(rec.get("size"), int) and rec["status"] == "SYNCED":
                    total_bytes += int(rec["size"])
                if rec["route"] == "unsorted":
                    unsorted_count += 1
                if rec["status"] == "ERROR":
                    error_ids.append(fid)

        subfolder_count = max(0, len(folder_ids) - (1 if self.root_folder_id in folder_ids else 0))
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "root_folder_id": self.root_folder_id,
            "generated_at": _iso_utc(now),
            "start_page_token": start_page_token,
            "stats": {
                "files": active_file_count,
                "folders": subfolder_count,
                "bytes": total_bytes,
                "unsorted": unsorted_count,
                "errors": len(error_ids),
            },
            "files": manifest_files,
        }
        if warnings:
            manifest["warnings"] = list(warnings)

        validate_manifest(manifest, schema_path=self.schema_path)
        validate_manifest_invariants(manifest)

        state_payload = {
            "schema_version": 1,
            "root_folder_id": self.root_folder_id,
            "start_page_token": start_page_token,
            "generated_at": manifest["generated_at"],
            "folder_ids": sorted(folder_ids),
            "folder_segments": {k: v for k, v in folder_segments.items()},
            "folder_parents": {k: v for k, v in folder_parents.items()},
            "folder_overrides": {
                fid: ov.to_dict() for fid, ov in folder_overrides.items() if not ov.is_empty
            },
            "override_files_by_id": dict(override_files_by_id),
            "files": final_records,
        }

        _atomic_write_text(
            self.tmp_dir,
            self.manifest_path,
            json.dumps(manifest, indent=2) + "\n",
        )
        _atomic_write_text(
            self.tmp_dir,
            self.state_path,
            json.dumps(state_payload, indent=2) + "\n",
        )

        written_tree_path: Path | None = None
        if self.tree_output_path is not None:
            written_tree_path = write_tree_md(manifest, self.tree_output_path)

        # Clean up any leftover staging files
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

        # Emit structured events after commit
        ts = manifest["generated_at"]
        emitted_events: list[DriveSyncEvent] = []
        touched_ids = set(added_ids) | set(changed_ids) | set(error_ids)
        for fid in sorted(
            (i for i in touched_ids if i in final_records),
            key=lambda k: (final_records[k]["path"], k),
        ):
            rec = final_records[fid]
            status = rec.get("status")
            if status == "SYNCED":
                ev_type = EVENT_FILE_SYNCED
            elif status == "STALE":
                ev_type = EVENT_FILE_STALE
            elif status == "ERROR":
                ev_type = EVENT_FILE_ERROR
            else:
                continue
            emitted_events.append(
                DriveSyncEvent(
                    event_type=ev_type,
                    file_id=fid,
                    path=rec["path"],
                    route=rec["route"],
                    mirror_path=rec.get("mirror_path"),
                    status=status,
                    md5=rec.get("md5"),
                    exported_as=rec.get("exported_as"),
                    timestamp=ts,
                    error=rec.get("error"),
                )
            )

        for fid in sorted(
            (i for i in set(removed_ids) if (i in final_records or i in records_by_id)),
            key=lambda k: ((final_records.get(k) or records_by_id[k])["path"], k),
        ):
            rec = final_records.get(fid) or records_by_id[fid]
            emitted_events.append(
                DriveSyncEvent(
                    event_type=EVENT_FILE_REMOVED,
                    file_id=fid,
                    path=rec["path"],
                    route=rec["route"],
                    mirror_path=rec.get("mirror_path"),
                    status="REMOVED",
                    md5=rec.get("md5"),
                    exported_as=rec.get("exported_as"),
                    timestamp=ts,
                )
            )

        emitted_events.append(
            DriveSyncEvent(
                event_type=EVENT_SYNC_COMPLETED,
                timestamp=ts,
                mode=mode,
                stats=dict(manifest["stats"]),
            )
        )
        self._emit_events(emitted_events)

        return SyncResult(
            mode=mode,
            manifest=manifest,
            manifest_path=self.manifest_path,
            added_ids=added_ids,
            changed_ids=changed_ids,
            removed_ids=removed_ids,
            error_ids=error_ids,
            warnings=warnings,
            tree_path=written_tree_path,
            events=emitted_events,
        )

    def _run_full_crawl(self) -> SyncResult:
        start_page_token = str(self._call(self.client.get_start_page_token))
        folder_ids: set[str] = {self.root_folder_id}
        folder_segments: dict[str, list[str]] = {self.root_folder_id: []}
        folder_parents: dict[str, str | None] = {self.root_folder_id: None}
        visited_folders: set[str] = set()

        discovered_files, discovered_shortcuts, discovered_configs = self._crawl_subtree(
            [(self.root_folder_id, [])],
            folder_ids,
            folder_segments,
            folder_parents,
            visited_folders,
        )

        prev_state = self._load_state()
        prev_files: dict[str, dict[str, Any]] = (
            dict(prev_state.get("files") or {}) if prev_state else {}
        )

        files_by_id: dict[str, DriveFileMetadata] = {f.id: f for f, _, _ in discovered_files}
        used_workspace_paths: set[str] = set()
        used_mirror_paths: set[str] = set()
        records_by_id: dict[str, dict[str, Any]] = {}
        added_ids: list[str] = []
        changed_ids: list[str] = []
        warnings: list[str] = []

        folder_overrides: dict[str, FolderRoutingOverride] = {}
        override_files_by_id: dict[str, dict[str, Any]] = {}
        for cfg_item, cfg_folder_id, cfg_segs in discovered_configs:
            folder_label = "/".join(cfg_segs) if cfg_segs else "<root>"
            override = self._load_clipper_yaml_item(cfg_item, folder_label, warnings)
            folder_overrides[cfg_folder_id] = override
            override_files_by_id[cfg_item.id] = {
                "folder_id": cfg_folder_id,
                "md5": cfg_item.md5_checksum,
                "modified_time": cfg_item.modified_time,
                "override": override.to_dict(),
            }

        for item, parent_folder_id, parent_segs in discovered_files:
            prev_rec = prev_files.get(item.id)
            chain = self._folder_chain(parent_folder_id, folder_parents)
            rec = self._sync_file_content(
                item=item,
                content_file_id=item.id,
                parent_segments=parent_segs,
                used_workspace_paths=used_workspace_paths,
                used_mirror_paths=used_mirror_paths,
                existing_record=prev_rec,
                folder_id=parent_folder_id,
                folder_chain=chain,
                folder_overrides=folder_overrides,
                folder_segments=folder_segments,
            )
            records_by_id[item.id] = rec
            if prev_rec is None:
                added_ids.append(item.id)
            elif (
                prev_rec.get("md5") != rec.get("md5")
                or prev_rec.get("path") != rec.get("path")
                or prev_rec.get("route") != rec.get("route")
                or prev_rec.get("status") != rec.get("status")
            ):
                changed_ids.append(item.id)

        for sc, parent_folder_id, parent_segs in discovered_shortcuts:
            target_id = sc.shortcut_target_id
            if not target_id or (target_id not in files_by_id and target_id not in folder_ids):
                msg = (
                    f"Shortcut '{sc.name}' ({sc.id}) points outside root folder "
                    f"(target={target_id}); ignored."
                )
                logger.warning(msg)
                warnings.append(msg)
                continue
            if target_id in folder_ids:
                continue
            target_meta = files_by_id[target_id]
            resolved_meta = DriveFileMetadata(
                id=sc.id,
                name=sc.name,
                mime_type=target_meta.mime_type,
                parents=list(sc.parents),
                size=target_meta.size,
                md5_checksum=target_meta.md5_checksum,
                modified_time=target_meta.modified_time or sc.modified_time,
                head_revision_id=target_meta.head_revision_id,
            )
            prev_rec = prev_files.get(sc.id)
            chain = self._folder_chain(parent_folder_id, folder_parents)
            rec = self._sync_file_content(
                item=resolved_meta,
                content_file_id=target_id,
                parent_segments=parent_segs,
                used_workspace_paths=used_workspace_paths,
                used_mirror_paths=used_mirror_paths,
                existing_record=prev_rec,
                folder_id=parent_folder_id,
                folder_chain=chain,
                folder_overrides=folder_overrides,
                folder_segments=folder_segments,
            )
            records_by_id[sc.id] = rec
            if prev_rec is None:
                added_ids.append(sc.id)
            elif (
                prev_rec.get("md5") != rec.get("md5")
                or prev_rec.get("path") != rec.get("path")
                or prev_rec.get("route") != rec.get("route")
                or prev_rec.get("status") != rec.get("status")
            ):
                changed_ids.append(sc.id)

        return self._build_and_commit_manifest(
            mode="full",
            start_page_token=start_page_token,
            folder_ids=folder_ids,
            folder_segments=folder_segments,
            folder_parents=folder_parents,
            folder_overrides=folder_overrides,
            override_files_by_id=override_files_by_id,
            records_by_id=records_by_id,
            added_ids=added_ids,
            changed_ids=changed_ids,
            removed_ids=[],
            warnings=warnings,
        )

    def _run_incremental(self, state: dict[str, Any]) -> SyncResult:
        page_token = str(state["start_page_token"])
        folder_ids: set[str] = set(state.get("folder_ids") or [self.root_folder_id])
        folder_ids.add(self.root_folder_id)
        folder_segments: dict[str, list[str]] = {
            str(k): [str(s) for s in v]
            for k, v in (state.get("folder_segments") or {}).items()
        }
        folder_segments[self.root_folder_id] = []
        folder_parents: dict[str, str | None] = {
            str(k): (str(v) if v is not None else None)
            for k, v in (state.get("folder_parents") or {}).items()
        }
        folder_parents[self.root_folder_id] = None
        folder_overrides: dict[str, FolderRoutingOverride] = {
            str(k): FolderRoutingOverride.from_dict(v)
            for k, v in (state.get("folder_overrides") or {}).items()
        }
        override_files_by_id: dict[str, dict[str, Any]] = {
            str(k): dict(v) for k, v in (state.get("override_files_by_id") or {}).items()
        }
        records_by_id: dict[str, dict[str, Any]] = {
            str(k): dict(v) for k, v in (state.get("files") or {}).items()
        }

        changes: list[Any] = []
        new_start_token = page_token
        while True:
            def _fetch_changes(tok: str = page_token) -> ChangesPage:
                return self.client.list_changes(tok, page_size=self.policy.page_size)

            page: ChangesPage = self._call(_fetch_changes)
            changes.extend(page.changes)
            if page.next_page_token:
                page_token = page.next_page_token
                continue
            if page.new_start_page_token:
                new_start_token = page.new_start_page_token
            break

        now_iso = _iso_utc(_utc_now(self.now_fn))
        added_ids: list[str] = []
        changed_ids: list[str] = []
        removed_ids: list[str] = []
        warnings: list[str] = []
        affected_override_folders: set[str] = set()

        # Track occupied paths excluding records being replaced/moved
        used_workspace_paths: set[str] = {
            str(rec["path"]).lower()
            for rec in records_by_id.values()
            if rec.get("status") != "REMOVED" and rec.get("path")
        }
        used_mirror_paths: set[str] = {
            str(rec["_mirror_rel"]).lower()
            for rec in records_by_id.values()
            if rec.get("status") != "REMOVED" and rec.get("_mirror_rel")
        }

        def _mark_removed(fid: str) -> None:
            rec = records_by_id.get(fid)
            if rec is None or rec.get("status") == "REMOVED":
                return
            if rec.get("path"):
                used_workspace_paths.discard(str(rec["path"]).lower())
            if rec.get("_mirror_rel"):
                used_mirror_paths.discard(str(rec["_mirror_rel"]).lower())
            rec["status"] = "REMOVED"
            rec["_removed_at"] = now_iso
            removed_ids.append(fid)

        def _mark_folder_removed(folder_id: str) -> None:
            old_segs = folder_segments.pop(folder_id, None)
            folder_ids.discard(folder_id)
            folder_parents.pop(folder_id, None)
            folder_overrides.pop(folder_id, None)
            for ov_fid in [k for k, v in override_files_by_id.items() if v.get("folder_id") == folder_id]:
                override_files_by_id.pop(ov_fid, None)
            if old_segs is None:
                return
            prefix = "/".join(old_segs) + "/"
            for fid, rec in list(records_by_id.items()):
                if (
                    rec.get("_folder_id") == folder_id
                    or folder_id in (rec.get("_parents") or [])
                    or str(rec.get("path", "")).startswith(prefix)
                ):
                    _mark_removed(fid)

        for change in changes:
            fid = change.file_id
            if change.removed or (change.file is not None and change.file.trashed):
                if fid in override_files_by_id:
                    old_ov = override_files_by_id.pop(fid)
                    old_folder_id = str(old_ov.get("folder_id", ""))
                    if old_folder_id:
                        folder_overrides.pop(old_folder_id, None)
                        affected_override_folders.add(old_folder_id)
                elif fid in folder_ids and fid != self.root_folder_id:
                    _mark_folder_removed(fid)
                else:
                    _mark_removed(fid)
                continue

            item = change.file
            if item is None:
                continue

            in_root_parent = next((p for p in item.parents if p in folder_ids), None)
            if in_root_parent is None:
                # Item moved outside root (or was never inside root)
                if item.id in override_files_by_id:
                    old_ov = override_files_by_id.pop(item.id)
                    old_folder_id = str(old_ov.get("folder_id", ""))
                    if old_folder_id:
                        folder_overrides.pop(old_folder_id, None)
                        affected_override_folders.add(old_folder_id)
                elif item.id in folder_ids and item.id != self.root_folder_id:
                    _mark_folder_removed(item.id)
                elif item.id in records_by_id:
                    _mark_removed(item.id)
                continue

            parent_segs = folder_segments[in_root_parent]

            if item.is_folder:
                new_segs = [*parent_segs, sanitize_segment(item.name, item.id)]
                folder_parents[item.id] = in_root_parent
                if item.id in folder_ids:
                    old_segs = folder_segments.get(item.id, [])
                    if old_segs != new_segs:
                        old_prefix = "/".join(old_segs) + "/"
                        new_prefix = "/".join(new_segs) + "/"
                        folder_segments[item.id] = new_segs
                        for sub_id, sub_segs in list(folder_segments.items()):
                            if sub_id != item.id and len(sub_segs) > len(old_segs) and sub_segs[: len(old_segs)] == old_segs:
                                folder_segments[sub_id] = [*new_segs, *sub_segs[len(old_segs) :]]
                        # Re-route and relocate child files
                        for child_id, rec in list(records_by_id.items()):
                            if rec.get("status") == "REMOVED":
                                continue
                            rec_path = str(rec.get("path", ""))
                            if rec_path.startswith(old_prefix):
                                used_workspace_paths.discard(rec_path.lower())
                                suffix = rec_path[len(old_prefix) :]
                                updated_path = f"{new_prefix}{suffix}"
                                used_workspace_paths.add(updated_path.lower())
                                rec["path"] = updated_path
                                child_folder_id = str(rec.get("_folder_id") or item.id)
                                child_chain = self._folder_chain(child_folder_id, folder_parents)
                                new_route, _ = resolve_folder_routing(
                                    updated_path,
                                    str(rec.get("name", "")),
                                    default_routing=self.policy.routing,
                                    folder_chain=child_chain,
                                    folder_overrides=folder_overrides,
                                    folder_segments=folder_segments,
                                )
                                rec["route"] = new_route
                                old_mirror_rel = rec.get("_mirror_rel")
                                if old_mirror_rel and str(old_mirror_rel).startswith(old_prefix):
                                    used_mirror_paths.discard(str(old_mirror_rel).lower())
                                    new_mirror_rel = f"{new_prefix}{str(old_mirror_rel)[len(old_prefix):]}"
                                    used_mirror_paths.add(new_mirror_rel.lower())
                                    old_local = assert_safe_mirror_path(self.mirror_dir, str(old_mirror_rel))
                                    new_local = assert_safe_mirror_path(self.mirror_dir, new_mirror_rel)
                                    if old_local.exists():
                                        _atomic_stage_move(old_local, new_local)
                                    rec["_mirror_rel"] = new_mirror_rel
                                    rec["mirror_path"] = self._mirror_path_label(new_mirror_rel)
                                changed_ids.append(child_id)
                else:
                    folder_ids.add(item.id)
                    folder_segments[item.id] = new_segs
                    sub_files, sub_shortcuts, sub_configs = self._crawl_subtree(
                        [(item.id, new_segs)],
                        folder_ids,
                        folder_segments,
                        folder_parents,
                        set(),
                    )
                    for cfg_item, cfg_folder_id, cfg_segs in sub_configs:
                        folder_label = "/".join(cfg_segs) if cfg_segs else "<root>"
                        override = self._load_clipper_yaml_item(cfg_item, folder_label, warnings)
                        folder_overrides[cfg_folder_id] = override
                        override_files_by_id[cfg_item.id] = {
                            "folder_id": cfg_folder_id,
                            "md5": cfg_item.md5_checksum,
                            "modified_time": cfg_item.modified_time,
                            "override": override.to_dict(),
                        }
                    for sub_item, sub_folder_id, sub_parent_segs in sub_files:
                        chain = self._folder_chain(sub_folder_id, folder_parents)
                        rec = self._sync_file_content(
                            item=sub_item,
                            content_file_id=sub_item.id,
                            parent_segments=sub_parent_segs,
                            used_workspace_paths=used_workspace_paths,
                            used_mirror_paths=used_mirror_paths,
                            existing_record=records_by_id.get(sub_item.id),
                            folder_id=sub_folder_id,
                            folder_chain=chain,
                            folder_overrides=folder_overrides,
                            folder_segments=folder_segments,
                        )
                        records_by_id[sub_item.id] = rec
                        added_ids.append(sub_item.id)
                    for sc, _, _ in sub_shortcuts:
                        warnings.append(f"Shortcut '{sc.name}' ({sc.id}) skipped during folder move.")
                continue

            if item.name == CLIPPER_YAML_FILENAME or item.id in override_files_by_id:
                if item.id in override_files_by_id:
                    old_folder_id = str(override_files_by_id[item.id].get("folder_id", ""))
                    if old_folder_id and old_folder_id != in_root_parent:
                        folder_overrides.pop(old_folder_id, None)
                        affected_override_folders.add(old_folder_id)
                if item.name == CLIPPER_YAML_FILENAME:
                    folder_label = "/".join(parent_segs) if parent_segs else "<root>"
                    override = self._load_clipper_yaml_item(item, folder_label, warnings)
                    folder_overrides[in_root_parent] = override
                    override_files_by_id[item.id] = {
                        "folder_id": in_root_parent,
                        "md5": item.md5_checksum,
                        "modified_time": item.modified_time,
                        "override": override.to_dict(),
                    }
                    affected_override_folders.add(in_root_parent)
                    continue
                # Renamed away from .clipper.yaml -> clear old override and treat as regular file
                override_files_by_id.pop(item.id, None)

            # Regular file or shortcut
            content_file_id = item.id
            effective_item = item
            if item.is_shortcut:
                target_id = item.shortcut_target_id
                target_rec = records_by_id.get(target_id or "")
                if not target_id or (target_rec is None and target_id not in folder_ids):
                    msg = (
                        f"Shortcut '{item.name}' ({item.id}) points outside root folder "
                        f"(target={target_id}); ignored."
                    )
                    logger.warning(msg)
                    warnings.append(msg)
                    continue
                if target_id in folder_ids or target_rec is None:
                    continue
                content_file_id = target_id
                effective_item = DriveFileMetadata(
                    id=item.id,
                    name=item.name,
                    mime_type=str(target_rec["mime"]),
                    parents=list(item.parents),
                    size=target_rec.get("size"),
                    md5_checksum=target_rec.get("md5"),
                    modified_time=str(target_rec.get("modified_time", item.modified_time)),
                    head_revision_id=target_rec.get("head_revision_id"),
                )

            prev_rec = records_by_id.get(item.id)
            if prev_rec is not None:
                if prev_rec.get("path"):
                    used_workspace_paths.discard(str(prev_rec["path"]).lower())
                if prev_rec.get("_mirror_rel"):
                    used_mirror_paths.discard(str(prev_rec["_mirror_rel"]).lower())

            chain = self._folder_chain(in_root_parent, folder_parents)
            new_rec = self._sync_file_content(
                item=effective_item,
                content_file_id=content_file_id,
                parent_segments=parent_segs,
                used_workspace_paths=used_workspace_paths,
                used_mirror_paths=used_mirror_paths,
                existing_record=prev_rec,
                folder_id=in_root_parent,
                folder_chain=chain,
                folder_overrides=folder_overrides,
                folder_segments=folder_segments,
            )
            if (
                prev_rec is not None
                and prev_rec.get("_mirror_rel")
                and prev_rec.get("_mirror_rel") != new_rec.get("_mirror_rel")
            ):
                prev_mirror_file = self._local_path_for_record(prev_rec)
                if prev_mirror_file is not None and prev_mirror_file.exists():
                    prev_mirror_file.unlink(missing_ok=True)
            records_by_id[item.id] = new_rec
            if prev_rec is None:
                added_ids.append(item.id)
            else:
                changed_ids.append(item.id)

        if affected_override_folders:
            for fid, rec in list(records_by_id.items()):
                if rec.get("status") == "REMOVED":
                    continue
                rec_folder_id = rec.get("_folder_id")
                if not rec_folder_id:
                    parents_list = rec.get("_parents") or []
                    rec_folder_id = next(
                        (p for p in parents_list if p in folder_ids),
                        self.root_folder_id,
                    )
                chain = self._folder_chain(str(rec_folder_id), folder_parents)
                if not any(f in affected_override_folders for f in chain):
                    continue
                new_route, now_ignored = resolve_folder_routing(
                    str(rec["path"]),
                    str(rec["name"]),
                    default_routing=self.policy.routing,
                    folder_chain=chain,
                    folder_overrides=folder_overrides,
                    folder_segments=folder_segments,
                )
                if now_ignored and rec.get("status") != "SKIPPED":
                    prev_mirror = self._local_path_for_record(rec)
                    if prev_mirror is not None and prev_mirror.exists():
                        prev_mirror.unlink(missing_ok=True)
                    rec["status"] = "SKIPPED"
                    rec["mirror_path"] = None
                    rec["_mirror_rel"] = None
                    rec["route"] = new_route
                    if fid not in changed_ids and fid not in added_ids:
                        changed_ids.append(fid)
                elif (
                    not now_ignored
                    and rec.get("status") == "SKIPPED"
                    and not is_skipped_extension(str(rec["name"]), self.policy.skip_extensions)
                ):
                    if rec.get("path"):
                        used_workspace_paths.discard(str(rec["path"]).lower())
                    if rec.get("_mirror_rel"):
                        used_mirror_paths.discard(str(rec["_mirror_rel"]).lower())
                    rebuilt_meta = DriveFileMetadata(
                        id=str(rec["id"]),
                        name=str(rec["name"]),
                        mime_type=str(rec["mime"]),
                        parents=[str(p) for p in (rec.get("_parents") or [])],
                        size=rec.get("size"),
                        md5_checksum=rec.get("md5"),
                        modified_time=str(rec.get("modified_time", "")),
                        head_revision_id=rec.get("head_revision_id"),
                    )
                    parent_segs = folder_segments.get(str(rec_folder_id), [])
                    records_by_id[fid] = self._sync_file_content(
                        item=rebuilt_meta,
                        content_file_id=str(rec.get("_content_file_id") or fid),
                        parent_segments=parent_segs,
                        used_workspace_paths=used_workspace_paths,
                        used_mirror_paths=used_mirror_paths,
                        existing_record=rec,
                        folder_id=str(rec_folder_id),
                        folder_chain=chain,
                        folder_overrides=folder_overrides,
                        folder_segments=folder_segments,
                    )
                    if fid not in changed_ids and fid not in added_ids:
                        changed_ids.append(fid)
                elif rec.get("route") != new_route:
                    rec["route"] = new_route
                    if fid not in changed_ids and fid not in added_ids:
                        changed_ids.append(fid)

        return self._build_and_commit_manifest(
            mode="incremental",
            start_page_token=new_start_token,
            folder_ids=folder_ids,
            folder_segments=folder_segments,
            folder_parents=folder_parents,
            folder_overrides=folder_overrides,
            override_files_by_id=override_files_by_id,
            records_by_id=records_by_id,
            added_ids=added_ids,
            changed_ids=changed_ids,
            removed_ids=removed_ids,
            warnings=warnings,
        )

    def sync(self, *, full: bool = False) -> SyncResult:
        """Run a full crawl or incremental sync depending on state and `full` flag."""
        self.mirror_dir.mkdir(parents=True, exist_ok=True)
        state = None if full else self._load_state()
        if state is None:
            return self._run_full_crawl()
        return self._run_incremental(state)


def sync_drive(
    client: DriveClient,
    root_folder_id: str,
    *,
    mirror_dir: Path,
    root_dir: Path | None = None,
    policy: DrivePolicyConfig | None = None,
    tree_output_path: Path | None = None,
    on_event: EventCallback | None = None,
    event_bus: DriveEventBus | None = None,
    full: bool = False,
) -> SyncResult:
    """Convenience entrypoint for a single Drive sync run."""
    syncer = DriveSync(
        client=client,
        root_folder_id=root_folder_id,
        mirror_dir=mirror_dir,
        root_dir=root_dir,
        policy=policy,
        tree_output_path=tree_output_path,
        on_event=on_event,
        event_bus=event_bus,
    )
    return syncer.sync(full=full)
