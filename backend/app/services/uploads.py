from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.config import get_settings

_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_upload_lock = threading.RLock()


class UploadError(RuntimeError):
    """Error raised by chunked upload operations."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sessions_root() -> Path:
    root = get_settings().data_dir / "uploads" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _uploads_root() -> Path:
    root = get_settings().data_dir / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_upload_id(upload_id: str) -> str:
    clean = (upload_id or "").strip()
    if not _UPLOAD_ID_RE.match(clean):
        raise UploadError(400, "VALIDATION_ERROR", f"Invalid upload id: {upload_id!r}")
    return clean


def _sanitize_filename(filename: str) -> str:
    name = Path((filename or "").strip()).name
    if not name or name in (".", ".."):
        raise UploadError(422, "VALIDATION_ERROR", "Invalid filename")
    return name


def _session_dir(upload_id: str) -> Path:
    clean_id = _validate_upload_id(upload_id)
    return _sessions_root() / clean_id


def _session_meta_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / "session.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _load_session(upload_id: str) -> dict[str, Any]:
    meta_path = _session_meta_path(upload_id)
    if not meta_path.exists():
        raise UploadError(404, "VALIDATION_ERROR", f"Upload session {upload_id!r} not found")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _public_session(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": meta["id"],
        "filename": meta["filename"],
        "size_bytes": int(meta["size_bytes"]),
        "received_chunks": int(meta.get("received_chunks", 0)),
        "status": meta["status"],
        "upload_id": meta.get("upload_id"),
    }
    if meta.get("mime_type") is not None:
        out["mime_type"] = meta["mime_type"]
    if meta.get("upload_path") is not None:
        out["upload_path"] = meta["upload_path"]
    if meta.get("sha256") is not None:
        out["sha256"] = meta["sha256"]
    return out


def init_upload_session(
    *,
    filename: str,
    size_bytes: int,
    mime_type: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    safe_name = _sanitize_filename(filename)
    if int(size_bytes) < 1:
        raise UploadError(422, "VALIDATION_ERROR", "size_bytes must be >= 1")

    upload_id = f"upl_{uuid.uuid4().hex[:12]}"
    sdir = _session_dir(upload_id)
    (sdir / "chunks").mkdir(parents=True, exist_ok=True)
    meta = {
        "id": upload_id,
        "filename": safe_name,
        "size_bytes": int(size_bytes),
        "mime_type": mime_type,
        "expected_sha256": expected_sha256.strip().lower() if expected_sha256 else None,
        "received_indices": [],
        "received_chunks": 0,
        "status": "INIT",
        "upload_id": None,
        "upload_path": None,
        "sha256": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    with _upload_lock:
        _atomic_write_json(_session_meta_path(upload_id), meta)
    return _public_session(meta)


def get_upload_session(upload_id: str) -> dict[str, Any]:
    with _upload_lock:
        return _public_session(_load_session(upload_id))


def store_upload_chunk(upload_id: str, chunk_index: int, data: bytes) -> dict[str, Any]:
    if chunk_index < 0:
        raise UploadError(422, "VALIDATION_ERROR", "Chunk index n must be >= 0")

    with _upload_lock:
        meta = _load_session(upload_id)
        if meta["status"] == "COMPLETED":
            raise UploadError(409, "VALIDATION_ERROR", f"Upload {upload_id} is already completed")

        chunks_dir = _session_dir(upload_id) / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = chunks_dir / str(chunk_index)
        _atomic_write_bytes(chunk_path, data)

        indices = set(int(i) for i in meta.get("received_indices", []))
        indices.add(int(chunk_index))
        meta["received_indices"] = sorted(indices)
        meta["received_chunks"] = len(indices)
        meta["status"] = "UPLOADING"
        meta["updated_at"] = _now_iso()
        _atomic_write_json(_session_meta_path(upload_id), meta)
        return _public_session(meta)


def complete_upload_session(
    upload_id: str,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    with _upload_lock:
        meta = _load_session(upload_id)
        if meta["status"] == "COMPLETED" and meta.get("upload_path"):
            existing_path = Path(meta["upload_path"])
            if existing_path.exists():
                return _public_session(meta)

        chunks_dir = _session_dir(upload_id) / "chunks"
        if not chunks_dir.exists():
            raise UploadError(400, "VALIDATION_ERROR", f"No chunks uploaded for {upload_id}")

        chunk_files = [
            p for p in chunks_dir.iterdir() if p.is_file() and p.name.isdigit()
        ]
        if not chunk_files:
            raise UploadError(400, "VALIDATION_ERROR", f"No chunks uploaded for {upload_id}")

        chunk_files.sort(key=lambda p: int(p.name))
        indices = [int(p.name) for p in chunk_files]
        if indices != list(range(len(indices))):
            raise UploadError(
                400,
                "VALIDATION_ERROR",
                f"Missing chunk(s); expected contiguous 0..{max(indices)}, got {indices}",
            )

        uploads_root = _uploads_root()
        final_path = uploads_root / f"{upload_id}_{meta['filename']}"
        fd, tmp_name = tempfile.mkstemp(prefix=f".tmp_{upload_id}_", dir=str(uploads_root))
        tmp_path = Path(tmp_name)

        hasher = hashlib.sha256()
        total_bytes = 0
        try:
            with os.fdopen(fd, "wb") as out_f:
                for chunk_file in chunk_files:
                    with chunk_file.open("rb") as in_f:
                        while True:
                            buf = in_f.read(65536)
                            if not buf:
                                break
                            out_f.write(buf)
                            hasher.update(buf)
                            total_bytes += len(buf)
                out_f.flush()
                os.fsync(out_f.fileno())

            expected_size = int(meta["size_bytes"])
            if total_bytes != expected_size:
                raise UploadError(
                    400,
                    "VALIDATION_ERROR",
                    f"Upload size mismatch: expected {expected_size} bytes, got {total_bytes}",
                )

            actual_sha256 = hasher.hexdigest()
            check_sha = (
                expected_sha256.strip().lower()
                if expected_sha256 and expected_sha256.strip()
                else meta.get("expected_sha256")
            )
            if check_sha and actual_sha256 != check_sha:
                raise UploadError(
                    400,
                    "VALIDATION_ERROR",
                    f"SHA-256 mismatch: expected {check_sha}, got {actual_sha256}",
                )

            os.replace(tmp_path, final_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        shutil.rmtree(chunks_dir, ignore_errors=True)
        meta["status"] = "COMPLETED"
        meta["upload_id"] = upload_id
        meta["upload_path"] = str(final_path)
        meta["sha256"] = actual_sha256
        meta["received_chunks"] = len(chunk_files)
        meta["updated_at"] = _now_iso()
        _atomic_write_json(_session_meta_path(upload_id), meta)
        return _public_session(meta)


def resolve_upload_path(upload_ref: str) -> Path | None:
    """Resolve an upload session ID or existing local upload path to a Path on disk."""
    raw = (upload_ref or "").strip()
    if not raw:
        return None
    if _UPLOAD_ID_RE.match(raw):
        meta_path = _session_meta_path(raw)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("status") == "COMPLETED" and meta.get("upload_path"):
                p = Path(meta["upload_path"])
                if p.exists():
                    return p
    cand = Path(raw)
    if cand.exists() and cand.is_file():
        return cand
    return None
