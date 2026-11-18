from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session, select

from backend.app.config import ROOT_DIR, Settings, get_settings
from backend.app.models.entities import (
    BlockerRecord,
    DriveFile,
    DriveState,
    Template,
    TemplateVersion,
)
from backend.app.services.admin import AdminError, record_audit
from backend.app.services.queue import get_queue_depth
from backend.pipeline.captions.template_loader import (
    ensure_starter_templates,
    validate_template_dict,
)
from backend.pipeline.sample_generator import verify_toolchain
from drive.client import (
    DriveAuthError,
    DriveClient,
    DriveError,
    DriveNotFoundError,
    MockDriveClient,
    ServiceAccountDriveClient,
)
from drive.routing import VALID_ROUTES
from drive.sync import sync_drive
from drive.tree import render_tree_md
from hitl.models import Blocker, BlockerStatus, detect_leaked_secret
from hitl.raise_blocker import resolve_blocker as hitl_resolve_blocker

APP_VERSION = "2.0.0"

_ROUTE_ALIASES: dict[str, str] = {
    "source": "source",
    "sources": "source",
    "template": "template",
    "templates": "template",
    "font": "font",
    "fonts": "font",
    "brandkit": "brandkit",
    "brandkits": "brandkit",
    "brand_kit": "brandkit",
    "doc": "doc",
    "docs": "doc",
    "reference": "reference",
    "references": "reference",
    "export": "export",
    "exports": "export",
    "unsorted": "unsorted",
}

_SENSITIVE_PAYLOAD_KEYS = {
    "gemini_api_key",
    "admin_password_hash",
    "notify_smtp_app_password",
    "s3_secret_key",
    "s3_access_key",
    "hf_token",
    "password",
    "secret",
    "token",
    "private_key",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _detect_ffmpeg_info(tc: dict[str, Any] | None = None) -> dict[str, Any]:
    toolchain = dict(tc) if tc is not None else verify_toolchain()
    version_str = "unavailable"
    harfbuzz = False
    fribidi = bool(toolchain.get("fribidi_enabled", False))
    libass = bool(toolchain.get("libass_enabled", False))
    available = bool(toolchain.get("ffmpeg_available", False))

    try:
        proc = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        out = proc.stdout or ""
        if proc.returncode == 0 and out:
            available = True
            first_line = out.splitlines()[0].strip()
            parts = first_line.split()
            version_str = parts[2] if len(parts) >= 3 else first_line
            libass = libass or ("--enable-libass" in out)
            fribidi = fribidi or ("--enable-libfribidi" in out)
            harfbuzz = "--enable-libharfbuzz" in out or "--enable-harfbuzz" in out or libass
    except Exception:
        pass

    return {
        "available": available,
        "version": version_str,
        "libass": libass,
        "harfbuzz": harfbuzz,
        "fribidi": fribidi,
        "indic_render_ok": bool(toolchain.get("indic_render_ok", False)),
    }


def _detect_gpu_info() -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            first = proc.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            name = parts[0] if parts else "NVIDIA GPU"
            vram_gb: float | None = None
            if len(parts) >= 2:
                try:
                    vram_gb = round(float(parts[1]) / 1024.0, 2)
                except ValueError:
                    vram_gb = None
            return {"name": name, "vram_gb": vram_gb, "cuda": True}
    except Exception:
        pass

    return {"name": None, "vram_gb": None, "cuda": False}


def _detect_ytdlp_version() -> dict[str, Any]:
    try:
        import importlib.metadata

        ver = importlib.metadata.version("yt-dlp")
        return {"version": ver, "available": True}
    except Exception:
        pass

    try:
        proc = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return {"version": proc.stdout.strip().splitlines()[0], "available": True}
    except Exception:
        pass

    return {"version": "unavailable", "available": False}


def _detect_disk_info(settings: Settings) -> dict[str, Any]:
    target = settings.data_dir if settings.data_dir.exists() else ROOT_DIR
    try:
        usage = shutil.disk_usage(target)
        free_gb = round(usage.free / (1024.0**3), 2)
        total_gb = round(usage.total / (1024.0**3), 2)
        return {"free_gb": free_gb, "total_gb": total_gb}
    except Exception:
        return {"free_gb": 0.0, "total_gb": 0.0}


def _detect_models_info(settings: Settings) -> dict[str, Any]:
    asr_models: list[str] = []
    if settings.asr_model_size:
        asr_models.append(settings.asr_model_size)
    asr_cfg = settings.load_yaml_config("asr")
    models_map = asr_cfg.get("models") if isinstance(asr_cfg, dict) else None
    if isinstance(models_map, dict):
        for val in models_map.values():
            s = str(val).strip()
            if s and s not in asr_models:
                asr_models.append(s)
    if not asr_models:
        asr_models = ["small"]

    provider = (settings.llm_provider or "auto").strip().lower()
    gemini_ok = bool(settings.gemini_api_key and settings.gemini_api_key.strip())
    if provider == "gemini":
        llm_ok = gemini_ok
    elif provider in ("ollama", "auto", "heuristic"):
        llm_ok = True
    else:
        llm_ok = True

    return {
        "asr": asr_models,
        "asr_engine": settings.asr_engine,
        "llm": {
            "provider": settings.llm_provider,
            "ok": llm_ok,
        },
    }


def _count_templates(session: Session | None, settings: Settings) -> dict[str, int]:
    valid_count = 0
    invalid_count = 0
    seen_ids: set[str] = set()

    if session is not None:
        try:
            tpls = list(session.exec(select(Template)).all())
            for tpl in tpls:
                seen_ids.add(tpl.id)
                if tpl.status == "invalid":
                    invalid_count += 1
                    continue
                ver = session.exec(
                    select(TemplateVersion)
                    .where(TemplateVersion.template_id == tpl.id)
                    .where(TemplateVersion.version == tpl.latest_version)
                ).first()
                if ver is not None and not ver.valid:
                    invalid_count += 1
                else:
                    valid_count += 1
        except Exception:
            pass

    tpl_root = settings.assets_dir / "templates"
    has_subdirs = tpl_root.exists() and any(p.is_dir() for p in tpl_root.iterdir())
    if not has_subdirs and valid_count == 0:
        try:
            ensure_starter_templates()
        except Exception:
            pass

    if tpl_root.exists():
        for folder in sorted(tpl_root.iterdir()):
            if not folder.is_dir():
                continue
            tfile = folder / "template.json"
            if not tfile.exists():
                continue
            try:
                raw = json.loads(tfile.read_text(encoding="utf-8"))
                tid = str(
                    (raw.get("meta") or {}).get("id")
                    if isinstance(raw.get("meta"), dict)
                    else raw.get("id") or folder.name
                )
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                ok, _ = validate_template_dict(raw)
                if ok:
                    valid_count += 1
                else:
                    invalid_count += 1
            except Exception:
                invalid_count += 1

    return {
        "valid": valid_count,
        "invalid": invalid_count,
        "total": valid_count + invalid_count,
    }


def _resolve_mirror_dir(settings: Settings) -> Path:
    env_mirror = os.environ.get("DRIVE_MIRROR_DIR", "").strip()
    if env_mirror:
        return Path(env_mirror)
    root_mirror = ROOT_DIR / "drive_mirror"
    if root_mirror.exists():
        return root_mirror
    return settings.data_dir / "drive_mirror"


def _resolve_drive_tree_json_path(settings: Settings) -> Path:
    return settings.data_dir / "drive_tree.json"


def _resolve_drive_tree_md_path(settings: Settings) -> Path:
    docs_tree = ROOT_DIR / "docs" / "drive" / "TREE.md"
    if docs_tree.parent.exists() and os.access(docs_tree.parent, os.W_OK):
        return docs_tree
    return settings.data_dir / "drive_tree.md"


def _empty_drive_manifest(root_folder_id: str | None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "root_folder_id": root_folder_id or "",
        "root_name": "Clipper",
        "generated_at": _now_iso(),
        "start_page_token": "0",
        "stats": {
            "files": 0,
            "folders": 0,
            "bytes": 0,
            "unsorted": 0,
            "errors": 0,
        },
        "files": [],
    }


def _load_drive_manifest(
    session: Session | None,
    settings: Settings,
) -> dict[str, Any]:
    mirror_dir = _resolve_mirror_dir(settings)
    manifest_candidates = [
        mirror_dir / "manifest.json",
        settings.data_dir / "drive_mirror" / "manifest.json",
        ROOT_DIR / "drive_mirror" / "manifest.json",
    ]
    for mpath in manifest_candidates:
        if mpath.exists():
            try:
                loaded = json.loads(mpath.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("files"), list):
                    return loaded
            except Exception:
                pass

    tree_json_path = _resolve_drive_tree_json_path(settings)
    if tree_json_path.exists():
        try:
            cached = json.loads(tree_json_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                if isinstance(cached.get("manifest"), dict):
                    return dict(cached["manifest"])
                if isinstance(cached.get("files"), list):
                    return cached
        except Exception:
            pass

    if session is not None:
        try:
            dfiles = list(session.exec(select(DriveFile)).all())
            dstate = session.get(DriveState, "singleton")
            if dfiles or dstate is not None:
                files_payload: list[dict[str, Any]] = []
                for df in sorted(dfiles, key=lambda x: (x.path_in_mirror or x.name, x.id)):
                    files_payload.append(
                        {
                            "id": df.id,
                            "name": df.name,
                            "mime": df.mime,
                            "path": df.path_in_mirror or df.name,
                            "mirror_path": df.path_in_mirror or None,
                            "size": df.size,
                            "md5": df.md5,
                            "modified_time": df.modified_time or df.seen_at,
                            "route": df.route if df.route in VALID_ROUTES else "unsorted",
                            "status": df.status,
                        }
                    )
                active = [f for f in files_payload if f.get("status") != "REMOVED"]
                return {
                    "schema_version": 1,
                    "root_folder_id": (
                        (dstate.root_folder_id if dstate else None)
                        or settings.drive_root_folder_id
                        or ""
                    ),
                    "root_name": "Clipper",
                    "generated_at": (
                        (dstate.last_sync_at if dstate and dstate.last_sync_at else None)
                        or _now_iso()
                    ),
                    "start_page_token": (
                        (dstate.start_page_token if dstate and dstate.start_page_token else None)
                        or "1"
                    ),
                    "stats": {
                        "files": len(active),
                        "folders": 0,
                        "bytes": sum(int(f.get("size") or 0) for f in active),
                        "unsorted": sum(1 for f in active if f.get("route") == "unsorted"),
                        "errors": sum(1 for f in active if f.get("status") == "ERROR"),
                    },
                    "files": files_payload,
                }
        except Exception:
            pass

    return _empty_drive_manifest(settings.drive_root_folder_id or None)


def _persist_drive_manifest_to_db(
    session: Session | None,
    manifest: dict[str, Any],
    *,
    status: str = "CONNECTED",
    last_error: str | None = None,
) -> None:
    if session is None:
        return
    dstate = session.get(DriveState, "singleton")
    if dstate is None:
        dstate = DriveState(id="singleton")
    dstate.root_folder_id = str(manifest.get("root_folder_id") or "") or None
    dstate.start_page_token = str(manifest.get("start_page_token") or "") or None
    dstate.last_sync_at = str(manifest.get("generated_at") or _now_iso())
    dstate.last_error = last_error
    dstate.status = status
    session.add(dstate)

    files = manifest.get("files")
    if isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict) or entry.get("is_folder"):
                continue
            fid = str(entry.get("id") or "").strip()
            if not fid:
                continue
            df = session.get(DriveFile, fid)
            if df is None:
                df = DriveFile(id=fid)
            df.name = str(entry.get("name") or "")
            df.mime = str(entry.get("mime") or "application/octet-stream")
            df.size = int(entry.get("size") or 0)
            df.md5 = str(entry.get("md5")) if entry.get("md5") else None
            df.modified_time = str(entry.get("modified_time") or "") or None
            df.path_in_mirror = str(entry.get("mirror_path") or entry.get("path") or df.name)
            route_val = str(entry.get("route") or "unsorted")
            df.route = route_val if route_val in VALID_ROUTES else "unsorted"
            df.status = str(entry.get("status") or "SYNCED")
            df.seen_at = _now_iso()
            session.add(df)
    session.commit()


def get_drive_status_service(
    session: Session | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    mirror_dir = _resolve_mirror_dir(cfg)
    manifest = _load_drive_manifest(session, cfg)
    manifest_files = manifest.get("files")
    raw_files: list[Any] = manifest_files if isinstance(manifest_files, list) else []
    active_files = [
        f
        for f in raw_files
        if isinstance(f, dict) and not f.get("is_folder") and f.get("status") != "REMOVED"
    ]

    counts_by_route: dict[str, int] = {r: 0 for r in VALID_ROUTES}
    for entry in active_files:
        r = str(entry.get("route") or "unsorted")
        counts_by_route[r if r in counts_by_route else "unsorted"] += 1

    dstate = session.get(DriveState, "singleton") if session is not None else None
    folder_id = (
        (dstate.root_folder_id if dstate and dstate.root_folder_id else None)
        or (cfg.drive_root_folder_id.strip() or None)
        or (str(manifest.get("root_folder_id") or "").strip() or None)
    )
    sa_exists = Path(cfg.drive_sa_file).exists()
    configured = bool(cfg.drive_enabled or folder_id or active_files)

    last_sync_at: str | None = None
    if dstate and dstate.last_sync_at:
        last_sync_at = dstate.last_sync_at
    elif active_files or (mirror_dir / "manifest.json").exists():
        last_sync_at = str(manifest.get("generated_at") or _now_iso())

    last_error = dstate.last_error if dstate else None

    if dstate and dstate.status and dstate.status != "DISABLED":
        status = dstate.status
    elif cfg.drive_enabled:
        if not folder_id:
            status = "NOT_FOUND"
        elif not sa_exists and not active_files and not (mirror_dir / "manifest.json").exists():
            status = "AUTH_ERROR"
        elif any(f.get("status") == "STALE" for f in active_files):
            status = "STALE"
        else:
            status = "CONNECTED"
    elif active_files or (mirror_dir / "manifest.json").exists():
        status = "CONNECTED"
    else:
        status = "DISABLED"

    return {
        "enabled": bool(cfg.drive_enabled),
        "configured": configured,
        "status": status,
        "root_folder_id": folder_id,
        "folder_id": folder_id,
        "mirror_dir": str(mirror_dir),
        "last_sync_at": last_sync_at,
        "last_error": last_error,
        "file_count": len(active_files),
        "counts_by_route": counts_by_route,
        "poll_minutes": cfg.drive_poll_minutes,
    }


def get_drive_tree_service(
    session: Session | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    tree_json_path = _resolve_drive_tree_json_path(cfg)

    if tree_json_path.exists():
        try:
            loaded = json.loads(tree_json_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                if "tree_md" in loaded and isinstance(loaded.get("manifest"), dict):
                    manifest = loaded["manifest"]
                    return {
                        "tree_md": str(loaded["tree_md"]),
                        "manifest": manifest,
                        "root_folder_id": manifest.get("root_folder_id") or cfg.drive_root_folder_id or None,
                        "generated_at": manifest.get("generated_at"),
                        "files": manifest.get("files", []),
                        "stats": manifest.get("stats", {}),
                    }
                if isinstance(loaded.get("files"), list):
                    tree_md = render_tree_md(loaded)
                    return {
                        "tree_md": tree_md,
                        "manifest": loaded,
                        "root_folder_id": loaded.get("root_folder_id") or cfg.drive_root_folder_id or None,
                        "generated_at": loaded.get("generated_at"),
                        "files": loaded.get("files", []),
                        "stats": loaded.get("stats", {}),
                    }
        except Exception:
            pass

    manifest = _load_drive_manifest(session, cfg)
    tree_md = render_tree_md(manifest)
    payload = {
        "tree_md": tree_md,
        "manifest": manifest,
        "root_folder_id": manifest.get("root_folder_id") or cfg.drive_root_folder_id or None,
        "generated_at": manifest.get("generated_at"),
        "files": manifest.get("files", []),
        "stats": manifest.get("stats", {}),
    }
    try:
        _atomic_write_json(tree_json_path, payload)
    except OSError:
        pass
    return payload


def trigger_drive_sync_service(
    session: Session | None = None,
    *,
    full: bool = False,
    dry_run: bool = False,
    client: DriveClient | None = None,
    settings: Settings | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    mirror_dir = _resolve_mirror_dir(cfg)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    root_folder_id = (cfg.drive_root_folder_id or "").strip() or "local_workspace_root"

    if dry_run:
        status_payload = get_drive_status_service(session, settings=cfg)
        status_payload.update(
            {
                "dry_run": True,
                "mode": "full" if full else "incremental",
                "added": 0,
                "changed": 0,
                "removed": 0,
                "errors": 0,
            }
        )
        return status_payload

    drive_client: DriveClient | None = client
    if drive_client is None:
        if cfg.drive_enabled and Path(cfg.drive_sa_file).exists() and cfg.drive_root_folder_id.strip():
            try:
                drive_client = ServiceAccountDriveClient(
                    sa_file=Path(cfg.drive_sa_file),
                )
            except DriveAuthError as exc:
                if session is not None:
                    _persist_drive_manifest_to_db(
                        session,
                        _load_drive_manifest(session, cfg),
                        status="AUTH_ERROR",
                        last_error=str(exc),
                    )
                raise AdminError(401, "DRIVE_AUTH", str(exc)) from exc
            except DriveError as exc:
                raise AdminError(502, "DRIVE_AUTH", str(exc)) from exc

    added_count = 0
    changed_count = 0
    removed_count = 0
    error_count = 0
    mode = "full" if full else "incremental"

    if drive_client is not None:
        try:
            res = sync_drive(
                client=drive_client,
                root_folder_id=root_folder_id,
                mirror_dir=mirror_dir,
                root_dir=ROOT_DIR,
                tree_output_path=_resolve_drive_tree_md_path(cfg),
                full=full,
            )
            manifest = res.manifest
            mode = res.mode
            added_count = len(res.added_ids)
            changed_count = len(res.changed_ids)
            removed_count = len(res.removed_ids)
            error_count = len(res.error_ids)
        except DriveAuthError as exc:
            if session is not None:
                _persist_drive_manifest_to_db(
                    session,
                    _load_drive_manifest(session, cfg),
                    status="AUTH_ERROR",
                    last_error=str(exc),
                )
            raise AdminError(401, "DRIVE_AUTH", str(exc)) from exc
        except DriveNotFoundError as exc:
            if session is not None:
                _persist_drive_manifest_to_db(
                    session,
                    _load_drive_manifest(session, cfg),
                    status="NOT_FOUND",
                    last_error=str(exc),
                )
            raise AdminError(404, "DRIVE_NOT_FOUND", str(exc)) from exc
    else:
        manifest = _load_drive_manifest(session, cfg)
        manifest["generated_at"] = _now_iso()
        if not manifest.get("root_folder_id"):
            manifest["root_folder_id"] = root_folder_id
        _atomic_write_json(mirror_dir / "manifest.json", manifest)

    tree_md = render_tree_md(manifest)
    tree_payload = {
        "tree_md": tree_md,
        "manifest": manifest,
        "root_folder_id": manifest.get("root_folder_id") or root_folder_id,
        "generated_at": manifest.get("generated_at"),
        "files": manifest.get("files", []),
        "stats": manifest.get("stats", {}),
    }
    _atomic_write_json(_resolve_drive_tree_json_path(cfg), tree_payload)

    sync_status = "CONNECTED" if (cfg.drive_enabled or manifest.get("files") or drive_client is not None) else "DISABLED"
    _persist_drive_manifest_to_db(session, manifest, status=sync_status, last_error=None)

    if session is not None and actor:
        record_audit(
            session,
            actor=actor,
            action="drive.sync",
            target=root_folder_id,
            diff={"mode": mode, "added": added_count, "changed": changed_count, "removed": removed_count},
        )
        session.commit()

    out = get_drive_status_service(session, settings=cfg)
    out.update(
        {
            "dry_run": False,
            "mode": mode,
            "added": added_count,
            "changed": changed_count,
            "removed": removed_count,
            "errors": error_count,
        }
    )
    return out


def list_drive_files_service(
    session: Session | None = None,
    *,
    route: str | None = None,
    category: str | None = None,
    subfolder: str | None = None,
    include_removed: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    raw_filter = (route or category or subfolder or "").strip().lower()
    norm_route: str | None = None
    if raw_filter:
        norm_route = _ROUTE_ALIASES.get(raw_filter)
        if norm_route is None:
            raise AdminError(
                422,
                "VALIDATION_ERROR",
                f"Invalid Drive route/category filter: {raw_filter!r}; expected one of {sorted(_ROUTE_ALIASES)}",
            )

    manifest = _load_drive_manifest(session, cfg)
    manifest_files = manifest.get("files")
    raw_files: list[Any] = manifest_files if isinstance(manifest_files, list) else []
    items: list[dict[str, Any]] = []
    for entry in raw_files:
        if not isinstance(entry, dict) or entry.get("is_folder"):
            continue
        if not include_removed and entry.get("status") == "REMOVED":
            continue
        entry_route = str(entry.get("route") or "unsorted")
        if norm_route is not None and entry_route != norm_route:
            continue
        items.append(dict(entry))

    return {"items": items, "total": len(items), "route": norm_route}


def pick_drive_file_service(
    session: Session | None,
    *,
    file_id: str,
    create_job: bool = False,
    language: str = "auto",
    aspect: str = "9:16",
    preset_id: str | None = None,
    auto_mode: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    clean_id = (file_id or "").strip()
    if not clean_id:
        raise AdminError(422, "VALIDATION_ERROR", "file_id is required")

    mirror_dir = _resolve_mirror_dir(cfg)
    manifest = _load_drive_manifest(session, cfg)
    manifest_files = manifest.get("files")
    raw_files: list[Any] = manifest_files if isinstance(manifest_files, list) else []

    matched: dict[str, Any] | None = None
    for entry in raw_files:
        if isinstance(entry, dict) and str(entry.get("id") or "") == clean_id:
            matched = entry
            break

    if matched is None and session is not None:
        df = session.get(DriveFile, clean_id)
        if df is not None:
            matched = {
                "id": df.id,
                "name": df.name,
                "mime": df.mime,
                "path": df.path_in_mirror or df.name,
                "mirror_path": df.path_in_mirror,
                "size": df.size,
                "md5": df.md5,
                "route": df.route,
                "status": df.status,
            }

    if matched is None:
        raise AdminError(404, "DRIVE_NOT_FOUND", f"Drive file {clean_id!r} not found in mirror")

    if matched.get("status") == "REMOVED":
        raise AdminError(404, "DRIVE_NOT_FOUND", f"Drive file {clean_id!r} was removed")

    mirror_rel = str(matched.get("mirror_path") or matched.get("path") or matched.get("name") or "")
    candidate_paths = [
        ROOT_DIR / mirror_rel if mirror_rel else None,
        mirror_dir / mirror_rel if mirror_rel else None,
        mirror_dir / str(matched.get("path") or "") if matched.get("path") else None,
    ]
    local_path: Path | None = None
    for cp in candidate_paths:
        if cp is not None and cp.exists() and cp.is_file():
            local_path = cp
            break

    source_ref = str(local_path) if local_path is not None else f"drive://{clean_id}"
    result: dict[str, Any] = {
        "drive_file_id": clean_id,
        "file_id": clean_id,
        "source_ref": source_ref,
        "source_kind": "drive",
        "name": str(matched.get("name") or clean_id),
        "mime": str(matched.get("mime") or "video/mp4"),
        "route": str(matched.get("route") or "source"),
        "size": matched.get("size"),
        "local_path": str(local_path) if local_path is not None else None,
    }

    if create_job and session is not None:
        from backend.app.models.entities import Job, JobStatus

        job = Job(
            source_kind="drive",
            source_url=source_ref if "://" in source_ref else f"drive://{clean_id}",
            source_ref=source_ref,
            platform="drive",
            title=str(matched.get("name") or "Drive Video"),
            language=language,
            aspect=aspect,
            auto_mode=auto_mode,
            preset_id=preset_id,
            status=JobStatus.QUEUED.value,
            stage="QUEUED",
            progress_pct=5,
            message="Job queued from Drive source",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        result["job_id"] = job.id
        result["job_status"] = job.status

    return result


# ---------------------------------------------------------------------------
# HITL Blockers discovery & resolution (.clipper-state/blockers + .agent/blockers)
# ---------------------------------------------------------------------------


def _candidate_state_dirs(root_dir: Path | None = None) -> list[Path]:
    env_root = os.environ.get("CLIPPER_STATE_ROOT", "").strip()
    if root_dir is not None:
        roots = [root_dir]
    elif env_root:
        roots = [Path(env_root)]
    else:
        roots = [ROOT_DIR]

    dirs: list[Path] = []
    seen: set[Path] = set()
    for r in roots:
        for sub in (".clipper-state", ".agent"):
            d = (r / sub).resolve()
            if d not in seen:
                seen.add(d)
                dirs.append(r / sub)
    return dirs


def _redact_payload_dict(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for k, v in payload.items():
        key_lower = str(k).lower()
        if key_lower in _SENSITIVE_PAYLOAD_KEYS:
            cleaned[k] = "[REDACTED]"
        elif isinstance(v, str) and detect_leaked_secret(v):
            cleaned[k] = "[REDACTED]"
        elif isinstance(v, dict):
            cleaned[k] = _redact_payload_dict(v)
        elif isinstance(v, list):
            new_list: list[Any] = []
            for item in v:
                if isinstance(item, dict):
                    new_list.append(_redact_payload_dict(item))
                elif isinstance(item, str) and detect_leaked_secret(item):
                    new_list.append("[REDACTED]")
                else:
                    new_list.append(item)
            cleaned[k] = new_list
        else:
            cleaned[k] = v
    return cleaned


def _format_blocker_schema_dict(blocker: Blocker, *, resolved_by: str | None = None) -> dict[str, Any]:
    raw_status = blocker.status.value if isinstance(blocker.status, BlockerStatus) else str(blocker.status)
    schema_status = "RESOLVED" if raw_status == "RESOLVED" else "OPEN"
    step_str = str(blocker.step) if blocker.step is not None else None

    payload: dict[str, Any] = {
        "needed": [item.model_dump(mode="json") for item in blocker.needed],
        "why": blocker.why,
        "alternatives": list(blocker.alternatives),
        "unblocked_meanwhile": list(blocker.unblocked_meanwhile),
        "raw_status": raw_status,
        "resolution_note": blocker.resolution_note,
        "resolved_by": resolved_by or ("web" if schema_status == "RESOLVED" else None),
        "reminders_sent": blocker.reminders_sent,
        "correlation_token": blocker.correlation_token,
    }
    safe_payload = _redact_payload_dict(payload)

    return {
        "id": blocker.id,
        "type": blocker.type.value,
        "status": schema_status,
        "task_id": blocker.task_id,
        "step": step_str,
        "title": "[REDACTED]" if detect_leaked_secret(blocker.title) else blocker.title,
        "payload": safe_payload,
        "needed": safe_payload["needed"],
        "why": safe_payload["why"],
        "alternatives": safe_payload["alternatives"],
        "resolution_note": safe_payload["resolution_note"],
        "resolved_by": safe_payload["resolved_by"],
        "created_at": blocker.created_at,
        "resolved_at": blocker.resolved_at,
    }


def list_all_blockers_service(
    session: Session | None = None,
    *,
    root_dir: Path | None = None,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}

    for sdir in _candidate_state_dirs(root_dir):
        bdir = sdir / "blockers"
        if not bdir.exists():
            continue
        for p in sorted(bdir.glob("BLK-*.yaml")):
            try:
                raw = yaml.safe_load(p.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    continue
                resolved_by = str(raw.get("resolved_by")) if raw.get("resolved_by") else None
                b = Blocker.model_validate(raw)
                formatted = _format_blocker_schema_dict(b, resolved_by=resolved_by)
                existing = by_id.get(b.id)
                if existing is None or (
                    existing["status"] == "RESOLVED" and formatted["status"] == "OPEN"
                ):
                    by_id[b.id] = formatted
            except Exception:
                continue

    if session is not None:
        try:
            db_rows = list(session.exec(select(BlockerRecord)).all())
            for row in db_rows:
                if row.id not in by_id:
                    safe_payload = _redact_payload_dict(dict(row.payload_json or {}))
                    by_id[row.id] = {
                        "id": row.id,
                        "type": row.type,
                        "status": "RESOLVED" if row.status == "RESOLVED" else "OPEN",
                        "task_id": row.task_id,
                        "step": str(row.step) if row.step is not None else None,
                        "title": row.title,
                        "payload": safe_payload,
                        "needed": safe_payload.get("needed", []),
                        "why": safe_payload.get("why", ""),
                        "alternatives": safe_payload.get("alternatives", []),
                        "resolution_note": safe_payload.get("resolution_note"),
                        "resolved_by": safe_payload.get("resolved_by"),
                        "created_at": row.created_at,
                        "resolved_at": row.resolved_at,
                    }
        except Exception:
            pass

    items = [by_id[k] for k in sorted(by_id.keys())]
    if status_filter:
        norm_st = status_filter.strip().upper()
        items = [it for it in items if it["status"] == norm_st]
    return items


def resolve_blocker_service(
    session: Session | None,
    blocker_id: str,
    *,
    note: str = "Resolved via Web UI",
    resolved_by: str = "web",
    root_dir: Path | None = None,
    actor: str = "admin",
) -> dict[str, Any]:
    clean_id = (blocker_id or "").strip()
    if not clean_id:
        raise AdminError(422, "VALIDATION_ERROR", "blocker_id is required")

    clean_note = (note or "Resolved via Web UI").strip()
    if detect_leaked_secret(clean_note):
        raise AdminError(
            422,
            "VALIDATION_ERROR",
            "Secret leak detected in resolution note; never send raw secret values in resolution notes.",
        )

    resolved_obj: Blocker | None = None

    for sdir in _candidate_state_dirs(root_dir):
        bfile = sdir / "blockers" / f"{clean_id}.yaml"
        if not bfile.exists():
            continue
        try:
            b = hitl_resolve_blocker(
                clean_id,
                note=clean_note,
                root_dir=sdir.parent,
                state_dir=sdir,
                validate=False,
            )
            # Persist resolved_by in YAML
            raw_data = yaml.safe_load(bfile.read_text(encoding="utf-8")) or {}
            if isinstance(raw_data, dict):
                raw_data["resolved_by"] = resolved_by
                raw_data["resolution_note"] = clean_note
                bfile.write_text(yaml.safe_dump(raw_data, sort_keys=False), encoding="utf-8")
            resolved_obj = b
        except ValueError as exc:
            raise AdminError(422, "VALIDATION_ERROR", str(exc)) from exc

    if resolved_obj is None and session is not None:
        row = session.get(BlockerRecord, clean_id)
        if row is not None:
            row.status = "RESOLVED"
            row.resolved_at = _now_iso()
            payload = dict(row.payload_json or {})
            payload["resolution_note"] = clean_note
            payload["resolved_by"] = resolved_by
            row.payload_json = _redact_payload_dict(payload)
            session.add(row)
            session.commit()
            record_audit(
                session,
                actor=actor,
                action="blocker.resolve",
                target=clean_id,
                diff={"status": "RESOLVED", "resolved_by": resolved_by},
            )
            session.commit()
            return {
                "id": row.id,
                "type": row.type,
                "status": "RESOLVED",
                "task_id": row.task_id,
                "step": str(row.step) if row.step is not None else None,
                "title": row.title,
                "payload": row.payload_json,
                "resolution_note": clean_note,
                "resolved_by": resolved_by,
                "created_at": row.created_at,
                "resolved_at": row.resolved_at,
            }

    if resolved_obj is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Blocker {clean_id!r} not found")

    formatted = _format_blocker_schema_dict(resolved_obj, resolved_by=resolved_by)

    if session is not None:
        row = session.get(BlockerRecord, clean_id)
        if row is None:
            row = BlockerRecord(
                id=clean_id,
                type=formatted["type"],
                status="RESOLVED",
                task_id=formatted["task_id"],
                step=formatted["step"],
                title=formatted["title"],
                payload_json=formatted["payload"],
                created_at=formatted["created_at"],
                resolved_at=formatted["resolved_at"],
            )
        else:
            row.status = "RESOLVED"
            row.resolved_at = formatted["resolved_at"]
            row.payload_json = formatted["payload"]
        session.add(row)
        record_audit(
            session,
            actor=actor,
            action="blocker.resolve",
            target=clean_id,
            diff={"status": "RESOLVED", "resolved_by": resolved_by},
        )
        session.commit()

    return formatted


# ---------------------------------------------------------------------------
# Unified /api/v1/health payload (Spec v2 + OpenAPI + Legacy E2E compatible)
# ---------------------------------------------------------------------------


def get_health_service(
    session: Session | None = None,
    *,
    settings: Settings | None = None,
    root_dir: Path | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    tc = verify_toolchain()
    ffmpeg_info = _detect_ffmpeg_info(tc)
    gpu_info = _detect_gpu_info()
    models_info = _detect_models_info(cfg)
    disk_info = _detect_disk_info(cfg)
    ytdlp_info = _detect_ytdlp_version()
    drive_info = get_drive_status_service(session, settings=cfg)
    templates_info = _count_templates(session, cfg)
    blockers = list_all_blockers_service(session, root_dir=root_dir)
    open_blockers = [b for b in blockers if b.get("status") == "OPEN"]
    resolved_blockers = [b for b in blockers if b.get("status") == "RESOLVED"]

    try:
        queue_depth = get_queue_depth()
    except Exception:
        queue_depth = 0

    queue_info = {
        "depth": queue_depth,
        "mode": cfg.queue_mode,
        "ok": True,
    }
    blockers_summary = {
        "open": len(open_blockers),
        "resolved": len(resolved_blockers),
        "total": len(blockers),
    }

    ffmpeg_ok = bool(ffmpeg_info.get("available") and ffmpeg_info.get("libass"))
    status = "ok" if ffmpeg_ok else "degraded"

    cookies_configured = {
        "youtube": bool(cfg.cookies_youtube and Path(cfg.cookies_youtube).exists())
        or (cfg.secrets_dir / "youtube_cookies.txt").exists(),
        "instagram": bool(cfg.cookies_instagram and Path(cfg.cookies_instagram).exists())
        or (cfg.secrets_dir / "instagram_cookies.txt").exists(),
        "facebook": bool(cfg.cookies_facebook and Path(cfg.cookies_facebook).exists())
        or (cfg.secrets_dir / "facebook_cookies.txt").exists(),
    }

    subsystems = {
        "ffmpeg": ffmpeg_info,
        "gpu": gpu_info,
        "models": models_info,
        "drive": {
            "status": drive_info["status"],
            "enabled": drive_info["enabled"],
            "configured": drive_info["configured"],
            "file_count": drive_info["file_count"],
            "last_sync_at": drive_info["last_sync_at"],
        },
        "queue": queue_info,
        "templates": templates_info,
        "blockers": blockers_summary,
    }

    return {
        "ok": ffmpeg_ok,
        "app": "CLIPPER",
        "status": status,
        "version": APP_VERSION,
        "subsystems": subsystems,
        "ffmpeg": ffmpeg_info,
        "gpu": gpu_info,
        "models": models_info,
        "disk": disk_info,
        "ytdlp": ytdlp_info,
        "queue": queue_info,
        "drive": {
            "status": drive_info["status"],
            "enabled": drive_info["enabled"],
            "configured": drive_info["configured"],
            "last_sync_at": drive_info["last_sync_at"],
        },
        "templates": templates_info,
        "blockers": blockers_summary,
        "open_blockers": len(open_blockers),
        "cookies_configured": cookies_configured,
        "toolchain": tc,
        "llm_provider": cfg.llm_provider,
        "gemini_configured": bool(cfg.gemini_api_key.strip()),
        "smtp_configured": bool(cfg.notify_smtp_user and cfg.notify_smtp_app_password),
    }


__all__ = [
    "APP_VERSION",
    "MockDriveClient",
    "get_drive_status_service",
    "get_drive_tree_service",
    "get_health_service",
    "list_all_blockers_service",
    "list_drive_files_service",
    "pick_drive_file_service",
    "resolve_blocker_service",
    "trigger_drive_sync_service",
]
