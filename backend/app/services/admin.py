from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import struct
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pysubs2
import yaml
from sqlmodel import Session, col, select

from backend.app.config import get_settings
from backend.app.models.entities import (
    AuditLog,
    Clip,
    Font,
    Job,
    Preset,
    PromptVersion,
    Setting,
    Template,
    TemplateVersion,
)
from backend.app.services.storage import get_storage
from backend.pipeline.captions.ass_builder import build_ass_subtitles
from backend.pipeline.captions.template_loader import (
    ensure_starter_templates,
    validate_template_dict,
)
from backend.pipeline.highlights.ranker import (
    build_candidate_windows,
    compute_signal_features,
    score_candidate_llm_or_heuristic,
)

ADMIN_SESSION_COOKIE = "clipper_admin_session"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SECRET_FIELD_NAMES = {
    "gemini_api_key",
    "admin_password_hash",
    "notify_smtp_app_password",
    "s3_secret_key",
    "s3_access_key",
    "hf_token",
    "cookies_youtube",
    "cookies_instagram",
    "cookies_facebook",
}
_UPDATABLE_SETTING_KEYS = {
    "llm_provider",
    "gemini_model",
    "ollama_base_url",
    "ollama_host",
    "ollama_model",
    "asr_engine",
    "asr_model_size",
    "asr_device",
    "asr_compute_type",
    "max_video_hours",
    "storage_backend",
    "queue_mode",
    "drive_enabled",
    "drive_poll_minutes",
    "notify_enabled",
    "notify_smtp_host",
    "notify_smtp_port",
    "notify_smtp_user",
    "owner_email",
}

_auth_lock = threading.RLock()
_failed_logins: dict[str, list[float]] = {}
_ephemeral_session_secret: bytes | None = None
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_S = 60.0


class AdminError(RuntimeError):
    """Structured error raised by admin service operations."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable
        super().__init__(message)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _validate_slug(value: str, label: str = "id") -> str:
    clean = (value or "").strip()
    if not clean or ".." in clean or "/" in clean or "\\" in clean or not _SLUG_RE.match(clean):
        raise AdminError(422, "VALIDATION_ERROR", f"Invalid {label}: {value!r}")
    return clean


# ---------------------------------------------------------------------------
# Auth, Password Verification, Rate Limiting & Session Cookies
# ---------------------------------------------------------------------------


def hash_admin_password(password: str) -> str:
    """Hash an admin password using argon2 (if installed) or scrypt."""
    try:
        from argon2 import PasswordHasher  # type: ignore[import-not-found]

        return PasswordHasher().hash(password)
    except ImportError:
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
        return f"scrypt${salt.hex()}${derived.hex()}"


def verify_admin_password(password: str, stored_hash: str) -> bool:
    """Verify password against argon2, scrypt, pbkdf2_sha256, or sha256 hashes."""
    raw_hash = (stored_hash or "").strip()
    if not raw_hash or not password:
        return False

    if raw_hash.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher  # type: ignore[import-not-found]

            return bool(PasswordHasher().verify(raw_hash, password))
        except Exception:
            return False

    if raw_hash.startswith("scrypt$"):
        parts = raw_hash.split("$")
        if len(parts) != 3:
            return False
        try:
            salt = bytes.fromhex(parts[1])
            expected = bytes.fromhex(parts[2])
            derived = hashlib.scrypt(
                password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=len(expected)
            )
            return hmac.compare_digest(derived, expected)
        except Exception:
            return False

    if raw_hash.startswith("pbkdf2_sha256$"):
        parts = raw_hash.split("$")
        if len(parts) != 4:
            return False
        try:
            iters = int(parts[1])
            salt = parts[2].encode("utf-8")
            expected = base64.b64decode(parts[3].encode("ascii"))
            derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
            return hmac.compare_digest(derived, expected)
        except Exception:
            return False

    if raw_hash.startswith("sha256$"):
        expected_hex = raw_hash.split("$", 1)[1].strip().lower()
        actual_hex = hashlib.sha256(password.encode("utf-8")).hexdigest().lower()
        return hmac.compare_digest(actual_hex, expected_hex)

    return False


def _get_session_signing_key() -> bytes:
    global _ephemeral_session_secret
    settings = get_settings()
    secret_file = settings.data_dir / ".admin_session_secret"
    with _auth_lock:
        if secret_file.exists():
            raw = secret_file.read_bytes()
            if len(raw) >= 16:
                return hashlib.sha256(raw + settings.admin_password_hash.encode("utf-8")).digest()
        try:
            generated = secrets.token_bytes(32)
            _atomic_write_bytes(secret_file, generated)
            return hashlib.sha256(
                generated + settings.admin_password_hash.encode("utf-8")
            ).digest()
        except OSError:
            if _ephemeral_session_secret is None:
                _ephemeral_session_secret = secrets.token_bytes(32)
            return hashlib.sha256(
                _ephemeral_session_secret + settings.admin_password_hash.encode("utf-8")
            ).digest()


def issue_admin_session_token(*, ttl_seconds: int = 86400) -> str:
    now = int(time.time())
    payload = {"sub": "admin", "iat": now, "exp": now + max(60, int(ttl_seconds))}
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    sig = hmac.new(_get_session_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii")
    return f"{payload_b64}.{sig_b64}"


def verify_admin_session_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(
            _get_session_signing_key(), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        actual_sig = base64.urlsafe_b64decode(sig_b64.encode("ascii"))
        if not hmac.compare_digest(actual_sig, expected_sig):
            return False
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8"))
        return payload.get("sub") == "admin" and int(payload.get("exp", 0)) > int(time.time())
    except Exception:
        return False


def reset_login_rate_limits() -> None:
    with _auth_lock:
        _failed_logins.clear()


def check_and_authenticate_admin(password: str, client_ip: str) -> str:
    settings = get_settings()
    if not settings.admin_enabled:
        raise AdminError(403, "VALIDATION_ERROR", "Admin panel is disabled")

    now = time.monotonic()
    with _auth_lock:
        attempts = [
            ts for ts in _failed_logins.get(client_ip, []) if (now - ts) < LOGIN_RATE_LIMIT_WINDOW_S
        ]
        _failed_logins[client_ip] = attempts
        if len(attempts) >= LOGIN_RATE_LIMIT_MAX_ATTEMPTS:
            raise AdminError(
                429,
                "RATE_LIMITED",
                "Too many failed login attempts. Try again later.",
                retryable=True,
            )

    if not verify_admin_password(password, settings.admin_password_hash):
        with _auth_lock:
            attempts = [
                ts
                for ts in _failed_logins.get(client_ip, [])
                if (now - ts) < LOGIN_RATE_LIMIT_WINDOW_S
            ]
            attempts.append(now)
            _failed_logins[client_ip] = attempts
        raise AdminError(401, "VALIDATION_ERROR", "Invalid admin password")

    with _auth_lock:
        _failed_logins.pop(client_ip, None)

    return issue_admin_session_token()


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


def _redact_secrets_value(obj: Any, key_hint: str = "") -> Any:
    low = key_hint.lower()
    if any(tok in low for tok in ("secret", "password", "token", "api_key", "cookie")):
        return "***REDACTED***"
    if isinstance(obj, dict):
        return {k: _redact_secrets_value(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_secrets_value(v) for v in obj]
    return obj


def record_audit(
    session: Session,
    *,
    action: str,
    target: str,
    diff: dict[str, Any] | None = None,
    actor: str = "admin",
) -> AuditLog:
    entry = AuditLog(
        actor=actor,
        action=action,
        target=target,
        diff_json=_redact_secrets_value(diff or {}),
    )
    session.add(entry)
    return entry


def list_audit_logs(
    session: Session,
    *,
    limit: int = 20,
    cursor: str | None = None,
    action: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    filters = []
    if action:
        filters.append(col(AuditLog.action) == action)
    if target:
        filters.append(col(AuditLog.target) == target)

    all_matching = list(
        session.exec(
            select(AuditLog)
            .where(*filters)
            .order_by(col(AuditLog.created_at).desc(), col(AuditLog.id).desc())
        ).all()
    )
    total = len(all_matching)

    start_idx = 0
    if cursor:
        try:
            start_idx = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
            if start_idx < 0:
                raise ValueError
        except Exception as exc:
            raise AdminError(400, "VALIDATION_ERROR", "Invalid audit cursor") from exc

    page = all_matching[start_idx : start_idx + limit]
    next_cursor = (
        base64.urlsafe_b64encode(str(start_idx + limit).encode("ascii")).decode("ascii")
        if (start_idx + limit) < total
        else None
    )
    return {
        "items": [
            {
                "id": row.id,
                "actor": row.actor,
                "action": row.action,
                "target": row.target,
                "diff": row.diff_json or {},
                "created_at": row.created_at,
            }
            for row in page
        ],
        "next_cursor": next_cursor,
        "total": total,
    }


# ---------------------------------------------------------------------------
# Templates, Versions, Preview & Presets
# ---------------------------------------------------------------------------


def _admin_templates_root() -> Path:
    root = get_settings().data_dir / "admin_templates"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_caption_v2_schema() -> dict[str, Any] | None:
    schema_path = (
        Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "caption_template.schema.json"
    )
    if schema_path.exists():
        return json.loads(schema_path.read_text(encoding="utf-8"))
    return None


def validate_any_template_payload(
    data: dict[str, Any], *, kind: str = "caption"
) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "Template payload must be a JSON object"
    if kind != "caption":
        tid = data.get("id") or (data.get("meta") or {}).get("id")
        name = data.get("name") or (data.get("meta") or {}).get("name")
        if not tid or not name:
            return False, "Template requires 'id' and 'name'"
        return True, ""

    if data.get("schema_version") == 2:
        v2_schema = _load_caption_v2_schema()
        if v2_schema is not None:
            try:
                jsonschema.validate(instance=data, schema=v2_schema)
                return True, ""
            except jsonschema.ValidationError as exc:
                return False, exc.message

    return validate_template_dict(data)


def _extract_template_meta(data: dict[str, Any], fallback_id: str = "") -> tuple[str, str, int]:
    raw_meta = data.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    tid = str(meta.get("id") or data.get("id") or fallback_id).strip()
    name = str(meta.get("name") or data.get("name") or tid).strip()
    ver_raw = meta.get("version") or data.get("version") or 1
    try:
        ver = max(1, int(ver_raw))
    except (TypeError, ValueError):
        ver = 1
    return tid, name, ver


def _latest_template_version(session: Session, template_id: str) -> TemplateVersion | None:
    return session.exec(
        select(TemplateVersion)
        .where(TemplateVersion.template_id == template_id)
        .order_by(col(TemplateVersion.version).desc())
    ).first()


def sync_builtin_templates_and_presets(session: Session) -> None:
    starters = ensure_starter_templates()
    settings = get_settings()
    tdir = settings.assets_dir / "templates"
    changed = False

    for tmpl in starters:
        tid = str(tmpl.get("id", "")).strip()
        if not tid:
            continue
        existing = session.get(Template, tid)
        tpath = tdir / tid / "template.json"
        if existing is None:
            valid = bool(tmpl.get("valid", True))
            err = str(tmpl.get("validation_error") or "")
            raw_bytes = tpath.read_bytes() if tpath.exists() else json.dumps(tmpl).encode("utf-8")
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            session.add(
                Template(
                    id=tid,
                    kind="caption",
                    name=str(tmpl.get("name") or tid),
                    status="published" if valid else "invalid",
                    latest_version=int(tmpl.get("version") or 1),
                    source="builtin",
                )
            )
            session.add(
                TemplateVersion(
                    template_id=tid,
                    version=int(tmpl.get("version") or 1),
                    path=str(tpath),
                    content_hash=content_hash,
                    valid=valid,
                    validation_json={"errors": [err] if err else []},
                    published_at=_now_iso() if valid else None,
                )
            )
            changed = True

    default_preset = session.get(Preset, "default")
    if default_preset is None:
        session.add(
            Preset(
                id="default",
                name="Default Vertical",
                caption_tpl="bold_pop_yellow@latest",
                layout_tpl="fill@latest",
                brandkit_tpl=None,
                overlay_tpls_json=[],
                publish_tpl="default@latest",
                render_profile="1080p_fast",
                is_default=True,
            )
        )
        changed = True

    if changed:
        session.commit()


def _serialize_template_summary(
    tpl: Template, latest_ver: TemplateVersion | None
) -> dict[str, Any]:
    val_json = (latest_ver.validation_json if latest_ver else None) or {}
    errors = list(val_json.get("errors") or [])
    valid = bool(latest_ver.valid) if latest_ver is not None else True
    return {
        "id": tpl.id,
        "kind": tpl.kind,
        "name": tpl.name,
        "status": tpl.status,
        "latest_version": tpl.latest_version,
        "source": tpl.source,
        "valid": valid,
        "validation_error": errors[0] if errors else None,
        "created_at": tpl.created_at,
    }


def list_templates_service(
    session: Session,
    *,
    kind: str | None = None,
    status: str | None = None,
    include_non_published: bool = False,
) -> list[dict[str, Any]]:
    sync_builtin_templates_and_presets(session)
    filters = []
    if kind:
        filters.append(col(Template.kind) == kind)
    if status:
        filters.append(col(Template.status) == status)
    elif not include_non_published:
        filters.append(col(Template.status) == "published")

    rows = list(
        session.exec(select(Template).where(*filters).order_by(col(Template.id))).all()
    )
    return [_serialize_template_summary(t, _latest_template_version(session, t.id)) for t in rows]


def get_template_detail_service(session: Session, template_id: str) -> dict[str, Any]:
    sync_builtin_templates_and_presets(session)
    tpl = session.get(Template, template_id)
    if tpl is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Template {template_id!r} not found")
    latest_ver = _latest_template_version(session, template_id)
    out = _serialize_template_summary(tpl, latest_ver)
    if latest_ver and latest_ver.path and Path(latest_ver.path).exists():
        try:
            out["definition"] = json.loads(Path(latest_ver.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["definition"] = None
    return out


def list_template_versions_service(session: Session, template_id: str) -> list[dict[str, Any]]:
    sync_builtin_templates_and_presets(session)
    tpl = session.get(Template, template_id)
    if tpl is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Template {template_id!r} not found")
    vers = list(
        session.exec(
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id)
            .order_by(col(TemplateVersion.version).desc())
        ).all()
    )
    return [
        {
            "id": v.id,
            "template_id": v.template_id,
            "version": v.version,
            "path": v.path,
            "content_hash": v.content_hash,
            "valid": v.valid,
            "validation": v.validation_json or {},
            "license_note": v.license_note,
            "published_at": v.published_at,
        }
        for v in vers
    ]


def _normalize_template_for_ass(data: dict[str, Any]) -> dict[str, Any]:
    """Convert either v1 or schema_version=2 caption template dict to v1 ASS builder format."""
    if data.get("schema_version") != 2:
        return data
    fonts_v2 = data.get("fonts", {})
    fonts_v1 = {
        sc: (
            fonts_v2[sc].get("family", "Sans")
            if isinstance(fonts_v2.get(sc), dict)
            else str(fonts_v2.get(sc, "Sans"))
        )
        for sc in ("latn", "deva", "beng")
    }
    text_v2 = data.get("text", {})
    stroke_v2 = data.get("stroke", {})
    layout_v2 = data.get("layout", {})
    anim_v2 = data.get("animation", {})
    anim_mode = anim_v2.get("mode", "pages")
    active_word = anim_v2.get("active_word", "highlight")
    anim_type = (
        "karaoke_fill"
        if anim_mode == "karaoke"
        else ("highlight_word" if active_word == "highlight" else "pop")
    )
    return {
        "id": (data.get("meta") or {}).get("id", "preview"),
        "name": (data.get("meta") or {}).get("name", "Preview"),
        "version": (data.get("meta") or {}).get("version", 1),
        "fonts": fonts_v1,
        "size_px": int(text_v2.get("size_px", 72)),
        "case": text_v2.get("case", "none"),
        "colors": data.get("colors", {"primary": "#FFFFFF", "active": "#FFE600", "outline": "#000000"}),
        "outline_px": float(stroke_v2.get("outline_px", 5)),
        "shadow_px": float(stroke_v2.get("shadow_px", 2)),
        "layout": {
            "position": layout_v2.get("anchor", "lower_third"),
            "y_percent": float(layout_v2.get("y_percent", 72)),
            "align": layout_v2.get("align", "center"),
            "max_words_per_line": int(layout_v2.get("max_words_per_line", 3)),
            "max_lines": int(layout_v2.get("max_lines", 2)),
        },
        "animation": {"type": anim_type},
    }


def render_template_preview_png(
    session: Session,
    template_id: str,
    *,
    language: str = "hinglish",
    sample_text: str | None = None,
) -> bytes:
    detail = get_template_detail_service(session, template_id)
    definition = detail.get("definition")
    if not isinstance(definition, dict):
        raise AdminError(400, "TEMPLATE_INVALID", f"Template {template_id!r} has no readable JSON")

    sample_map = {
        "en": "Shipping fast beats perfection every single time!",
        "hi": "सफलता एक दिन में नहीं मिलती दोस्तों!",
        "bn": "প্রথম তিন সেকেন্ডেই দর্শকের মনোযোগ ধরে রাখুন!",
        "hinglish": "Dosto aaj ka viral hook yahi hai!",
        "auto": "Dosto aaj सफलता aur ভাইরাল hook!",
    }
    text_str = (sample_text or "").strip() or sample_map.get(language, sample_map["hinglish"])
    tokens = [tok for tok in text_str.split() if tok] or ["CLIPPER", "Preview"]
    words = [
        {"w": tok, "s": idx * 0.4, "e": (idx + 1) * 0.4}
        for idx, tok in enumerate(tokens)
    ]

    norm_tpl = _normalize_template_for_ass(definition)
    with tempfile.TemporaryDirectory(prefix="clipper_tpl_preview_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        ass_path = tmp_path / "preview.ass"
        try:
            build_ass_subtitles(words, norm_tpl, ass_path, clip_start_offset=0.0, play_res=(540, 960))
        except Exception:
            subs = pysubs2.SSAFile()
            subs.info["PlayResX"] = "540"
            subs.info["PlayResY"] = "960"
            subs.events.append(pysubs2.SSAEvent(start=0, end=2000, text=text_str))
            subs.save(str(ass_path))

        escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
        out_png = tmp_path / "preview.png"
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=#0F172A:s=540x960:d=1",
            "-vf",
            f"ass='{escaped_ass}'",
            "-ss",
            "0.2",
            "-frames:v",
            "1",
            str(out_png),
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not out_png.exists():
            raise AdminError(
                500,
                "RENDER_FAILED",
                f"Preview render failed: {proc.stderr.decode('utf-8', errors='ignore')[:200]}",
            )
        return out_png.read_bytes()


def import_template_bytes(
    session: Session,
    *,
    filename: str,
    raw_bytes: bytes,
    kind: str = "caption",
    actor: str = "admin",
) -> dict[str, Any]:
    if not raw_bytes:
        raise AdminError(422, "VALIDATION_ERROR", "Uploaded template file is empty")

    companion_files: dict[str, bytes] = {}
    low_name = (filename or "").lower()
    if low_name.endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
        except zipfile.BadZipFile as exc:
            raise AdminError(400, "TEMPLATE_INVALID", "Invalid .cliptpl.zip archive") from exc
        with zf:
            tpl_member: str | None = None
            for info in zf.infolist():
                if info.is_dir():
                    continue
                norm_member = Path(info.filename).as_posix()
                if norm_member.startswith("/") or ".." in Path(norm_member).parts:
                    raise AdminError(400, "TEMPLATE_INVALID", f"Unsafe path in zip: {info.filename}")
                if Path(norm_member).name == "template.json" and tpl_member is None:
                    tpl_member = norm_member
                else:
                    companion_files[Path(norm_member).name] = zf.read(info.filename)
            if tpl_member is None:
                raise AdminError(400, "TEMPLATE_INVALID", "Archive missing template.json")
            try:
                tpl_data = json.loads(zf.read(tpl_member).decode("utf-8"))
            except Exception as exc:
                raise AdminError(400, "TEMPLATE_INVALID", f"Malformed template.json: {exc}") from exc
    else:
        try:
            tpl_data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise AdminError(400, "TEMPLATE_INVALID", f"Malformed template JSON: {exc}") from exc

    if not isinstance(tpl_data, dict):
        raise AdminError(400, "TEMPLATE_INVALID", "Template JSON must be an object")

    fallback_id = Path(filename or "custom_template").stem.split(".")[0]
    tid, name, _ = _extract_template_meta(tpl_data, fallback_id=fallback_id)
    tid = _validate_slug(tid, "template_id")

    valid, err = validate_any_template_payload(tpl_data, kind=kind)
    existing_tpl = session.get(Template, tid)
    if existing_tpl is None:
        next_ver = 1
        tpl = Template(
            id=tid,
            kind=kind,
            name=name or tid,
            status="draft" if valid else "invalid",
            latest_version=next_ver,
            source="import",
        )
        session.add(tpl)
    else:
        latest_v = _latest_template_version(session, tid)
        next_ver = (latest_v.version if latest_v else existing_tpl.latest_version) + 1
        existing_tpl.name = name or existing_tpl.name
        existing_tpl.kind = kind or existing_tpl.kind
        existing_tpl.latest_version = next_ver
        existing_tpl.status = "draft" if valid else "invalid"
        existing_tpl.source = "import"
        session.add(existing_tpl)
        tpl = existing_tpl

    ver_dir = _admin_templates_root() / tid / f"v{next_ver}"
    ver_dir.mkdir(parents=True, exist_ok=True)
    for cname, cbytes in companion_files.items():
        if cname:
            _atomic_write_bytes(ver_dir / cname, cbytes)

    tpl_json_path = ver_dir / "template.json"
    serialized_bytes = json.dumps(tpl_data, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(tpl_json_path, serialized_bytes)
    content_hash = hashlib.sha256(serialized_bytes).hexdigest()

    tv = TemplateVersion(
        template_id=tid,
        version=next_ver,
        path=str(tpl_json_path),
        content_hash=content_hash,
        valid=valid,
        validation_json={"errors": [err] if err else []},
        published_at=None,
    )
    session.add(tv)
    record_audit(
        session,
        action="template.import",
        target=f"template:{tid}@v{next_ver}",
        diff={"valid": valid, "kind": kind, "error": err or None},
        actor=actor,
    )
    session.commit()
    session.refresh(tpl)
    session.refresh(tv)
    return _serialize_template_summary(tpl, tv)


def publish_template_service(
    session: Session, template_id: str, *, actor: str = "admin"
) -> dict[str, Any]:
    sync_builtin_templates_and_presets(session)
    tpl = session.get(Template, template_id)
    if tpl is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Template {template_id!r} not found")
    tv = _latest_template_version(session, template_id)
    if tv is None:
        raise AdminError(400, "TEMPLATE_INVALID", f"Template {template_id!r} has no versions")

    if tv.path and Path(tv.path).exists():
        try:
            payload = json.loads(Path(tv.path).read_text(encoding="utf-8"))
        except Exception as exc:
            raise AdminError(400, "TEMPLATE_INVALID", f"Unreadable template JSON: {exc}") from exc
        valid, err = validate_any_template_payload(payload, kind=tpl.kind)
    else:
        valid = bool(tv.valid)
        err = ((tv.validation_json or {}).get("errors") or [""])[0]

    if not valid:
        raise AdminError(
            400,
            "TEMPLATE_INVALID",
            f"Cannot publish invalid template {template_id!r}: {err}",
        )

    # Published versions are immutable: if current version is already published, create a new immutable version
    if tv.published_at is not None and tpl.status == "published":
        new_ver = tv.version + 1
        new_tv = TemplateVersion(
            template_id=tpl.id,
            version=new_ver,
            path=tv.path,
            content_hash=tv.content_hash,
            valid=True,
            validation_json={"errors": []},
            license_note=tv.license_note,
            published_at=_now_iso(),
        )
        tpl.latest_version = new_ver
        session.add(new_tv)
        active_tv = new_tv
    else:
        tv.valid = True
        tv.validation_json = {"errors": []}
        tv.published_at = tv.published_at or _now_iso()
        session.add(tv)
        active_tv = tv

    tpl.status = "published"
    session.add(tpl)
    record_audit(
        session,
        action="template.publish",
        target=f"template:{tpl.id}@v{active_tv.version}",
        diff={"status": "published", "version": active_tv.version},
        actor=actor,
    )
    session.commit()
    session.refresh(tpl)
    session.refresh(active_tv)
    return _serialize_template_summary(tpl, active_tv)


def archive_template_service(
    session: Session, template_id: str, *, actor: str = "admin"
) -> dict[str, Any]:
    sync_builtin_templates_and_presets(session)
    tpl = session.get(Template, template_id)
    if tpl is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Template {template_id!r} not found")
    prev = tpl.status
    tpl.status = "archived"
    session.add(tpl)
    record_audit(
        session,
        action="template.archive",
        target=f"template:{tpl.id}",
        diff={"from_status": prev, "to_status": "archived"},
        actor=actor,
    )
    session.commit()
    session.refresh(tpl)
    return _serialize_template_summary(tpl, _latest_template_version(session, tpl.id))


def duplicate_template_service(
    session: Session,
    template_id: str,
    *,
    new_id: str | None = None,
    new_name: str | None = None,
    actor: str = "admin",
) -> dict[str, Any]:
    sync_builtin_templates_and_presets(session)
    src = session.get(Template, template_id)
    if src is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Template {template_id!r} not found")
    src_tv = _latest_template_version(session, template_id)
    if src_tv is None or not src_tv.path or not Path(src_tv.path).exists():
        raise AdminError(400, "TEMPLATE_INVALID", f"Template {template_id!r} has no source file")

    base_copy_id = _validate_slug(new_id, "new_id") if new_id else f"{template_id}_copy"
    copy_id = base_copy_id
    idx = 2
    while session.get(Template, copy_id) is not None:
        copy_id = f"{base_copy_id}_{idx}"
        idx += 1

    copy_name = (new_name or f"{src.name} (Copy)").strip()
    data = json.loads(Path(src_tv.path).read_text(encoding="utf-8"))
    if data.get("schema_version") == 2 and isinstance(data.get("meta"), dict):
        data["meta"]["id"] = copy_id
        data["meta"]["name"] = copy_name
        data["meta"]["version"] = 1
    else:
        data["id"] = copy_id
        data["name"] = copy_name
        data["version"] = 1

    ver_dir = _admin_templates_root() / copy_id / "v1"
    ver_dir.mkdir(parents=True, exist_ok=True)
    dst_path = ver_dir / "template.json"
    serialized = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(dst_path, serialized)
    valid, err = validate_any_template_payload(data, kind=src.kind)

    dup_tpl = Template(
        id=copy_id,
        kind=src.kind,
        name=copy_name,
        status="draft" if valid else "invalid",
        latest_version=1,
        source="admin",
    )
    dup_tv = TemplateVersion(
        template_id=copy_id,
        version=1,
        path=str(dst_path),
        content_hash=hashlib.sha256(serialized).hexdigest(),
        valid=valid,
        validation_json={"errors": [err] if err else []},
        published_at=None,
    )
    session.add(dup_tpl)
    session.add(dup_tv)
    record_audit(
        session,
        action="template.duplicate",
        target=f"template:{copy_id}",
        diff={"source_id": template_id, "new_id": copy_id},
        actor=actor,
    )
    session.commit()
    session.refresh(dup_tpl)
    session.refresh(dup_tv)
    return _serialize_template_summary(dup_tpl, dup_tv)


def export_template_zip_bytes(session: Session, template_id: str) -> tuple[str, bytes]:
    sync_builtin_templates_and_presets(session)
    tpl = session.get(Template, template_id)
    if tpl is None:
        raise AdminError(404, "VALIDATION_ERROR", f"Template {template_id!r} not found")
    tv = _latest_template_version(session, template_id)
    if tv is None or not tv.path or not Path(tv.path).exists():
        raise AdminError(404, "VALIDATION_ERROR", f"Template {template_id!r} file missing")

    src_path = Path(tv.path)
    src_dir = src_path.parent
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in sorted(src_dir.rglob("*")):
            if fpath.is_file():
                rel = fpath.relative_to(src_dir).as_posix()
                zf.write(fpath, rel)
        manifest = {
            "id": tpl.id,
            "kind": tpl.kind,
            "version": tv.version,
            "status": tpl.status,
            "content_hash": tv.content_hash,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    buf.seek(0)
    filename = f"{tpl.id}-v{tv.version}.cliptpl.zip"
    return filename, buf.getvalue()


def _serialize_preset(p: Preset) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "version": 1,
        "caption": p.caption_tpl,
        "layout": p.layout_tpl,
        "brand_kit": p.brandkit_tpl,
        "overlays": list(p.overlay_tpls_json or []),
        "publish": p.publish_tpl,
        "render_profile": p.render_profile,
        "is_default": p.is_default,
        "created_at": p.created_at,
    }


def list_presets_service(session: Session) -> list[dict[str, Any]]:
    sync_builtin_templates_and_presets(session)
    rows = list(session.exec(select(Preset).order_by(col(Preset.id))).all())
    return [_serialize_preset(p) for p in rows]


def save_preset_service(
    session: Session, payload: dict[str, Any], *, actor: str = "admin"
) -> dict[str, Any]:
    pid = _validate_slug(str(payload.get("id") or ""), "preset_id")
    name = str(payload.get("name") or pid).strip()
    caption = str(payload.get("caption") or "bold_pop_yellow@latest")
    layout = str(payload.get("layout") or "fill@latest")
    brand_kit = payload.get("brand_kit")
    overlays = list(payload.get("overlays") or [])
    publish = str(payload.get("publish") or "default@latest")
    render_profile = str(payload.get("render_profile") or "1080p_fast")
    is_default = bool(payload.get("is_default", False))

    if is_default:
        for other in session.exec(select(Preset).where(col(Preset.is_default).is_(True))).all():
            if other.id != pid:
                other.is_default = False
                session.add(other)

    existing = session.get(Preset, pid)
    if existing is None:
        preset = Preset(
            id=pid,
            name=name,
            caption_tpl=caption,
            layout_tpl=layout,
            brandkit_tpl=brand_kit,
            overlay_tpls_json=overlays,
            publish_tpl=publish,
            render_profile=render_profile,
            is_default=is_default,
        )
        session.add(preset)
        action = "preset.create"
    else:
        existing.name = name
        existing.caption_tpl = caption
        existing.layout_tpl = layout
        existing.brandkit_tpl = brand_kit
        existing.overlay_tpls_json = overlays
        existing.publish_tpl = publish
        existing.render_profile = render_profile
        existing.is_default = is_default
        session.add(existing)
        preset = existing
        action = "preset.update"

    record_audit(
        session,
        action=action,
        target=f"preset:{pid}",
        diff={"name": name, "caption": caption, "is_default": is_default},
        actor=actor,
    )
    session.commit()
    session.refresh(preset)
    return _serialize_preset(preset)


# ---------------------------------------------------------------------------
# Fonts & Brand Kits
# ---------------------------------------------------------------------------


def detect_font_script_coverage(font_bytes: bytes, filename: str) -> dict[str, bool]:
    """Inspect SFNT cmap table for latn/deva/beng glyph coverage with heuristic fallback."""
    sample_cps = {"latn": 0x0041, "deva": 0x0915, "beng": 0x0995}
    coverage = {"latn": False, "deva": False, "beng": False}

    try:
        if len(font_bytes) >= 12:
            sfnt_ver, num_tables = struct.unpack(">IH", font_bytes[:6])
            if sfnt_ver in (0x00010000, 0x4F54544F) and 0 < num_tables < 100:
                cmap_offset = None
                cmap_len = 0
                for idx in range(num_tables):
                    rec_off = 12 + idx * 16
                    if rec_off + 16 > len(font_bytes):
                        break
                    tag = font_bytes[rec_off : rec_off + 4]
                    off, length = struct.unpack(">II", font_bytes[rec_off + 8 : rec_off + 16])
                    if tag == b"cmap":
                        cmap_offset, cmap_len = off, length
                        break

                if cmap_offset is not None and cmap_offset + 4 <= len(font_bytes):
                    cmap_data = font_bytes[cmap_offset : cmap_offset + cmap_len]
                    _, num_sub = struct.unpack(">HH", cmap_data[:4])
                    mapped_cps: set[int] = set()
                    for sidx in range(num_sub):
                        srec = 4 + sidx * 8
                        if srec + 8 > len(cmap_data):
                            break
                        _, _, sub_off = struct.unpack(">HHI", cmap_data[srec : srec + 8])
                        if sub_off + 4 > len(cmap_data):
                            continue
                        fmt = struct.unpack(">H", cmap_data[sub_off : sub_off + 2])[0]
                        if fmt == 4 and sub_off + 14 <= len(cmap_data):
                            seg_count = struct.unpack(">H", cmap_data[sub_off + 6 : sub_off + 8])[0] // 2
                            end_off = sub_off + 14
                            start_off = end_off + 2 * seg_count + 2
                            if start_off + 2 * seg_count <= len(cmap_data):
                                for si in range(seg_count):
                                    sc = struct.unpack(
                                        ">H", cmap_data[start_off + 2 * si : start_off + 2 * si + 2]
                                    )[0]
                                    ec = struct.unpack(
                                        ">H", cmap_data[end_off + 2 * si : end_off + 2 * si + 2]
                                    )[0]
                                    for name_k, cp in sample_cps.items():
                                        if sc <= cp <= ec:
                                            mapped_cps.add(cp)
                        elif fmt == 12 and sub_off + 16 <= len(cmap_data):
                            num_groups = struct.unpack(">I", cmap_data[sub_off + 12 : sub_off + 16])[0]
                            grp_off = sub_off + 16
                            for gi in range(min(num_groups, 10000)):
                                go = grp_off + gi * 12
                                if go + 12 > len(cmap_data):
                                    break
                                sc, ec, _ = struct.unpack(">III", cmap_data[go : go + 12])
                                for cp in sample_cps.values():
                                    if sc <= cp <= ec:
                                        mapped_cps.add(cp)
                    if mapped_cps:
                        for name_k, cp in sample_cps.items():
                            coverage[name_k] = cp in mapped_cps
                        return coverage
    except Exception:
        pass

    low = filename.lower()
    coverage["latn"] = True
    if any(tok in low for tok in ("deva", "devanagari", "hindi")):
        coverage["deva"] = True
    if any(tok in low for tok in ("beng", "bengali", "bangla")):
        coverage["beng"] = True
    return coverage


def register_font_service(
    session: Session,
    *,
    filename: str,
    raw_bytes: bytes,
    license_str: str,
    family: str | None = None,
    style: str = "Regular",
    actor: str = "admin",
) -> dict[str, Any]:
    safe_name = Path((filename or "").strip()).name
    if not safe_name or Path(safe_name).suffix.lower() not in (".ttf", ".otf", ".woff2"):
        raise AdminError(422, "VALIDATION_ERROR", "Font file must be .ttf, .otf, or .woff2")
    clean_lic = (license_str or "").strip()
    if not clean_lic:
        raise AdminError(422, "VALIDATION_ERROR", "Font license is required")
    if not raw_bytes:
        raise AdminError(422, "VALIDATION_ERROR", "Uploaded font file is empty")

    fonts_dir = get_settings().data_dir / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    dest = fonts_dir / safe_name
    _atomic_write_bytes(dest, raw_bytes)

    scripts = detect_font_script_coverage(raw_bytes, safe_name)
    font_family = (family or Path(safe_name).stem).strip()
    license_ok = clean_lic.lower() not in ("unknown", "proprietary", "unlicensed")

    rec = Font(
        family=font_family,
        style=style or "Regular",
        path=str(dest),
        scripts_json=scripts,
        license=clean_lic,
        license_ok=license_ok,
        source="admin",
    )
    session.add(rec)
    record_audit(
        session,
        action="font.register",
        target=f"font:{font_family}",
        diff={"filename": safe_name, "license": clean_lic, "scripts": scripts},
        actor=actor,
    )
    session.commit()
    session.refresh(rec)
    return _serialize_font(rec)


def _serialize_font(f: Font) -> dict[str, Any]:
    return {
        "id": f.id,
        "family": f.family,
        "style": f.style,
        "path": f.path,
        "scripts": f.scripts_json or {},
        "license": f.license,
        "license_ok": f.license_ok,
        "source": f.source,
        "created_at": f.created_at,
    }


def list_fonts_service(session: Session) -> list[dict[str, Any]]:
    rows = list(session.exec(select(Font).order_by(col(Font.family))).all())
    return [_serialize_font(f) for f in rows]


def _brandkits_dir() -> Path:
    d = get_settings().data_dir / "brandkits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_brandkits_service(session: Session) -> list[dict[str, Any]]:
    rows = list(
        session.exec(
            select(Template).where(col(Template.kind) == "brand_kit").order_by(col(Template.id))
        ).all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        tv = _latest_template_version(session, row.id)
        if tv and tv.path and Path(tv.path).exists():
            try:
                out.append(json.loads(Path(tv.path).read_text(encoding="utf-8")))
                continue
            except (OSError, ValueError):
                pass
        out.append({"id": row.id, "name": row.name, "status": row.status})
    return out


def save_brandkit_service(
    session: Session, payload: dict[str, Any], *, actor: str = "admin"
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AdminError(422, "VALIDATION_ERROR", "Brand kit payload must be a JSON object")
    bk_id = _validate_slug(str(payload.get("id") or ""), "brandkit_id")
    name = str(payload.get("name") or bk_id).strip()
    record_data = {
        **payload,
        "id": bk_id,
        "name": name,
        "updated_at": _now_iso(),
    }
    bk_path = _brandkits_dir() / f"{bk_id}.json"
    serialized = json.dumps(record_data, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write_bytes(bk_path, serialized)
    content_hash = hashlib.sha256(serialized).hexdigest()

    existing = session.get(Template, bk_id)
    if existing is None:
        session.add(
            Template(
                id=bk_id,
                kind="brand_kit",
                name=name,
                status="published",
                latest_version=1,
                source="admin",
            )
        )
        ver = 1
        action = "brandkit.create"
    else:
        existing.name = name
        existing.kind = "brand_kit"
        existing.latest_version += 1
        session.add(existing)
        ver = existing.latest_version
        action = "brandkit.update"

    session.add(
        TemplateVersion(
            template_id=bk_id,
            version=ver,
            path=str(bk_path),
            content_hash=content_hash,
            valid=True,
            validation_json={"errors": []},
            published_at=_now_iso(),
        )
    )
    record_audit(
        session,
        action=action,
        target=f"brandkit:{bk_id}",
        diff={"name": name, "version": ver},
        actor=actor,
    )
    session.commit()
    return record_data


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def sync_builtin_prompts(session: Session) -> None:
    pdir = get_settings().config_dir / "prompts"
    if not pdir.exists():
        return
    changed = False
    for md in sorted(pdir.glob("*.md")):
        name = md.stem
        existing = session.exec(
            select(PromptVersion).where(col(PromptVersion.name) == name)
        ).first()
        if existing is None:
            raw = md.read_bytes()
            session.add(
                PromptVersion(
                    name=name,
                    version=1,
                    path=str(md),
                    content_hash=hashlib.sha256(raw).hexdigest(),
                    active=True,
                    created_by="system",
                )
            )
            changed = True
    if changed:
        session.commit()


def list_prompts_service(session: Session) -> list[dict[str, Any]]:
    sync_builtin_prompts(session)
    rows = list(
        session.exec(
            select(PromptVersion).order_by(col(PromptVersion.name), col(PromptVersion.version).desc())
        ).all()
    )
    by_name: dict[str, list[PromptVersion]] = {}
    for r in rows:
        by_name.setdefault(r.name, []).append(r)

    items: list[dict[str, Any]] = []
    for name, vers in sorted(by_name.items()):
        active = next((v for v in vers if v.active), vers[0])
        items.append(
            {
                "name": name,
                "active_version": active.version,
                "latest_version": vers[0].version,
                "content_hash": active.content_hash,
                "updated_at": active.created_at,
            }
        )
    return items


def get_prompt_detail_service(session: Session, name: str) -> dict[str, Any]:
    sync_builtin_prompts(session)
    clean_name = _validate_slug(name, "prompt_name")
    vers = list(
        session.exec(
            select(PromptVersion)
            .where(col(PromptVersion.name) == clean_name)
            .order_by(col(PromptVersion.version).desc())
        ).all()
    )
    if not vers:
        raise AdminError(404, "VALIDATION_ERROR", f"Prompt {clean_name!r} not found")
    active = next((v for v in vers if v.active), vers[0])
    content = ""
    if active.path and Path(active.path).exists():
        content = Path(active.path).read_text(encoding="utf-8")
    return {
        "name": clean_name,
        "active_version": active.version,
        "content": content,
        "versions": [
            {
                "id": v.id,
                "version": v.version,
                "active": v.active,
                "content_hash": v.content_hash,
                "created_by": v.created_by,
                "created_at": v.created_at,
            }
            for v in vers
        ],
    }


def save_prompt_version_service(
    session: Session, name: str, content: str, *, actor: str = "admin"
) -> dict[str, Any]:
    sync_builtin_prompts(session)
    clean_name = _validate_slug(name, "prompt_name")
    if not (content or "").strip():
        raise AdminError(422, "VALIDATION_ERROR", "Prompt content must not be empty")

    vers = list(
        session.exec(
            select(PromptVersion)
            .where(col(PromptVersion.name) == clean_name)
            .order_by(col(PromptVersion.version).desc())
        ).all()
    )
    next_ver = (vers[0].version + 1) if vers else 1
    for v in vers:
        if v.active:
            v.active = False
            session.add(v)

    dest = get_settings().data_dir / "prompts" / clean_name / f"v{next_ver}.md"
    _atomic_write_text(dest, content)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    pv = PromptVersion(
        name=clean_name,
        version=next_ver,
        path=str(dest),
        content_hash=content_hash,
        active=True,
        created_by=actor,
    )
    session.add(pv)
    record_audit(
        session,
        action="prompt.update",
        target=f"prompt:{clean_name}@v{next_ver}",
        diff={"version": next_ver, "content_hash": content_hash},
        actor=actor,
    )
    session.commit()
    return get_prompt_detail_service(session, clean_name)


def activate_prompt_version_service(
    session: Session, name: str, version: int, *, actor: str = "admin"
) -> dict[str, Any]:
    sync_builtin_prompts(session)
    clean_name = _validate_slug(name, "prompt_name")
    vers = list(
        session.exec(
            select(PromptVersion).where(col(PromptVersion.name) == clean_name)
        ).all()
    )
    if not vers:
        raise AdminError(404, "VALIDATION_ERROR", f"Prompt {clean_name!r} not found")
    target = next((v for v in vers if v.version == version), None)
    if target is None:
        raise AdminError(
            404, "VALIDATION_ERROR", f"Prompt {clean_name!r} version {version} not found"
        )
    for v in vers:
        v.active = v.version == version
        session.add(v)

    record_audit(
        session,
        action="prompt.activate",
        target=f"prompt:{clean_name}@v{version}",
        diff={"activated_version": version},
        actor=actor,
    )
    session.commit()
    return get_prompt_detail_service(session, clean_name)


def test_prompt_service(session: Session, name: str) -> dict[str, Any]:
    detail = get_prompt_detail_service(session, name)
    clean_name = detail["name"]
    content = detail["content"]
    errors: list[str] = []

    if clean_name == "highlight_map":
        sample_output: dict[str, Any] = {
            "moments": [
                {
                    "start_word_idx": 0,
                    "end_word_idx": 14,
                    "hook": 88,
                    "payoff": 84,
                    "standalone": 90,
                    "emotion": 76,
                    "shareability": 82,
                    "quotability": 79,
                    "brief_match": None,
                    "title": "Why 99% of Creators Fail in the First 3 Seconds",
                    "hook_line": "99% creators fail right at the start!",
                    "reason": "Strong opening hook with clear takeaway.",
                    "hashtags": ["#creators", "#shorts", "#growth"],
                }
            ]
        }
        schema = {
            "type": "object",
            "required": ["moments"],
            "properties": {
                "moments": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "start_word_idx",
                            "end_word_idx",
                            "hook",
                            "payoff",
                            "standalone",
                            "title",
                            "hashtags",
                        ],
                    },
                }
            },
        }
    elif clean_name == "publish_meta":
        sample_output = {
            "title": "Viral Hook Secret",
            "description": "How to keep viewers watching past 3 seconds.",
            "hashtags": ["#clips", "#viral", "#shorts"],
            "tags": ["clips", "viral", "shorts"],
        }
        schema = {
            "type": "object",
            "required": ["title", "description", "hashtags"],
        }
    else:
        sample_output = {"ok": True, "prompt": clean_name}
        schema = {"type": "object"}

    if not content.strip():
        errors.append("Prompt content is empty")
    try:
        jsonschema.validate(instance=sample_output, schema=schema)
    except jsonschema.ValidationError as exc:
        errors.append(exc.message)

    return {
        "name": clean_name,
        "version": detail["active_version"],
        "valid": len(errors) == 0,
        "errors": errors,
        "sample_output": sample_output,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def get_scoring_config_service(session: Session) -> dict[str, Any]:
    settings = get_settings()
    base = settings.load_spec_v2_yaml_config("highlights") or {}
    row = session.get(Setting, "scoring_config")
    merged = dict(base)
    if row and isinstance(row.value_json, dict):
        merged.update(row.value_json)

    clips = list(session.exec(select(Clip)).all())
    up_count = sum(1 for c in clips if c.feedback == "up")
    down_count = sum(1 for c in clips if c.feedback == "down")
    views = [c.actual_views for c in clips if c.actual_views is not None]
    return {
        "version": int(merged.get("version", 1)),
        "selection": merged.get("selection", {}),
        "weights": merged.get("weights", {}),
        "windows": merged.get("windows", {}),
        "genre_overrides": merged.get("genre_overrides", {}),
        "feedback_stats": {
            "up": up_count,
            "down": down_count,
            "total_rated": up_count + down_count,
            "avg_actual_views": round(sum(views) / len(views), 1) if views else None,
        },
    }


def update_scoring_config_service(
    session: Session, payload: dict[str, Any], *, actor: str = "admin"
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AdminError(422, "VALIDATION_ERROR", "Scoring payload must be a JSON object")

    if "weights" in payload and not isinstance(payload["weights"], dict):
        raise AdminError(422, "VALIDATION_ERROR", "weights must be an object")
    if "selection" in payload and not isinstance(payload["selection"], dict):
        raise AdminError(422, "VALIDATION_ERROR", "selection must be an object")

    current = get_scoring_config_service(session)
    updated: dict[str, Any] = {
        "version": int(current.get("version", 1)) + 1,
        "selection": payload.get("selection", current["selection"]),
        "weights": payload.get("weights", current["weights"]),
        "windows": payload.get("windows", current["windows"]),
        "genre_overrides": payload.get("genre_overrides", current["genre_overrides"]),
    }

    rev_dir = get_settings().data_dir / "config_revisions"
    rev_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(rev_dir / f"scoring_v{updated['version']}_{int(time.time())}.json", updated)

    row = session.get(Setting, "scoring_config")
    if row is None:
        row = Setting(key="scoring_config", value_json=updated)
    else:
        row.value_json = updated
        row.updated_at = _now_iso()
    session.add(row)
    record_audit(
        session,
        action="scoring.update",
        target="config:scoring",
        diff={"version": updated["version"], "weights": updated["weights"]},
        actor=actor,
    )
    session.commit()
    return get_scoring_config_service(session)


def dry_run_scoring_service(
    session: Session,
    *,
    job_id: str,
    weights_override: dict[str, float] | None = None,
) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if job is None or job.deleted_at is not None:
        raise AdminError(404, "VALIDATION_ERROR", f"Job {job_id!r} not found")

    tp = get_storage().job_dir(job_id) / "asr" / "transcript.json"
    if not tp.exists():
        raise AdminError(404, "VALIDATION_ERROR", f"Transcript missing for job {job_id!r}")
    transcript = json.loads(tp.read_text(encoding="utf-8"))

    current_clips = list(
        session.exec(select(Clip).where(Clip.job_id == job_id).order_by(col(Clip.rank))).all()
    )
    current_picks = [
        {"clip_id": c.id, "rank": c.rank, "start": c.start, "end": c.end, "score": c.score, "title": c.title}
        for c in current_clips
    ]

    cfg = get_scoring_config_service(session)
    default_weights = dict((cfg.get("weights") or {}).get("default") or {})
    used_weights = {**default_weights, **(weights_override or {})}
    llm_w = float(used_weights.get("llm", 0.55))
    energy_w = float(used_weights.get("energy", 0.10))
    rate_w = float(used_weights.get("speech_rate", 0.07))
    react_w = float(used_weights.get("reaction", 0.08))
    scene_w = float(used_weights.get("scene", 0.05))
    heat_w = float(used_weights.get("heatmap", 0.10))
    conf_w = float(used_weights.get("asr_conf", 0.05))
    w_sum = max(1e-6, llm_w + energy_w + rate_w + react_w + scene_w + heat_w + conf_w)

    windows_cfg = cfg.get("windows") or {}
    cands = build_candidate_windows(
        transcript,
        min_sec=float(windows_cfg.get("min_len_s", 12.0)),
        max_sec=float(windows_cfg.get("max_len_s", 60.0)),
    )
    lang = transcript.get("language", job.language or "en")
    proposed: list[dict[str, Any]] = []
    for cand in cands:
        feats = compute_signal_features(cand, median_wps=2.3)
        llm_res = score_candidate_llm_or_heuristic(cand, lang)
        fused = (
            llm_w * float(llm_res["llm_score"])
            + energy_w * feats["energy"]
            + rate_w * feats["speech_rate"]
            + react_w * feats["reaction"]
            + scene_w * feats["scene_dynamics"]
            + heat_w * feats["heatmap"]
            + conf_w * feats["asr_confidence"]
        ) / w_sum
        proposed.append(
            {
                "start": cand["start"],
                "end": cand["end"],
                "score": round(fused, 4),
                "title": llm_res["title"],
            }
        )
    proposed.sort(key=lambda item: item["score"], reverse=True)
    for idx, item in enumerate(proposed, start=1):
        item["rank"] = idx

    return {
        "job_id": job_id,
        "weights_used": used_weights,
        "current_picks": current_picks,
        "proposed_picks": proposed[: max(1, int(job.max_clips or 8))],
    }


# ---------------------------------------------------------------------------
# Settings (Strict Secret Masking)
# ---------------------------------------------------------------------------


def get_masked_settings_service(session: Session) -> dict[str, Any]:
    s = get_settings()
    row = session.get(Setting, "app_settings")
    overrides = dict(row.value_json) if row and isinstance(row.value_json, dict) else {}

    non_secrets = {
        "app_env": s.app_env,
        "llm_provider": overrides.get("llm_provider", s.llm_provider),
        "gemini_model": overrides.get("gemini_model", s.gemini_model),
        "ollama_base_url": overrides.get("ollama_base_url", s.ollama_base_url),
        "ollama_host": overrides.get("ollama_host", s.ollama_host),
        "ollama_model": overrides.get("ollama_model", s.ollama_model),
        "asr_engine": overrides.get("asr_engine", s.asr_engine),
        "asr_model_size": overrides.get("asr_model_size", s.asr_model_size),
        "asr_device": overrides.get("asr_device", s.asr_device),
        "asr_compute_type": overrides.get("asr_compute_type", s.asr_compute_type),
        "max_video_hours": overrides.get("max_video_hours", s.max_video_hours),
        "storage_backend": overrides.get("storage_backend", s.storage_backend),
        "queue_mode": overrides.get("queue_mode", s.queue_mode),
        "drive_enabled": overrides.get("drive_enabled", s.drive_enabled),
        "drive_poll_minutes": overrides.get("drive_poll_minutes", s.drive_poll_minutes),
        "notify_enabled": overrides.get("notify_enabled", s.notify_enabled),
        "notify_smtp_host": overrides.get("notify_smtp_host", s.notify_smtp_host),
        "notify_smtp_port": overrides.get("notify_smtp_port", s.notify_smtp_port),
        "notify_smtp_user": overrides.get("notify_smtp_user", s.notify_smtp_user),
        "owner_email": overrides.get("owner_email", s.owner_email),
        "admin_enabled": s.admin_enabled,
    }
    secrets_status = {
        key: {"set": bool(str(getattr(s, key, "") or "").strip())}
        for key in sorted(_SECRET_FIELD_NAMES)
    }
    return {**non_secrets, "secrets": secrets_status}


def update_settings_service(
    session: Session, payload: dict[str, Any], *, actor: str = "admin"
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AdminError(422, "VALIDATION_ERROR", "Settings payload must be a JSON object")

    forbidden = [k for k in payload if k in _SECRET_FIELD_NAMES or "password" in k.lower() or "secret" in k.lower() or "token" in k.lower() or "api_key" in k.lower()]
    if forbidden:
        raise AdminError(
            422,
            "VALIDATION_ERROR",
            f"Secret fields cannot be updated or returned via /admin/settings: {forbidden}",
        )

    unknown = [k for k in payload if k not in _UPDATABLE_SETTING_KEYS]
    if unknown:
        raise AdminError(422, "VALIDATION_ERROR", f"Unsupported or read-only setting keys: {unknown}")

    row = session.get(Setting, "app_settings")
    current = dict(row.value_json) if row and isinstance(row.value_json, dict) else {}
    diff = {k: {"old": current.get(k), "new": v} for k, v in payload.items()}
    current.update(payload)

    rev_dir = get_settings().data_dir / "config_revisions"
    rev_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(rev_dir / f"settings_{int(time.time())}.json", current)

    if row is None:
        row = Setting(key="app_settings", value_json=current)
    else:
        row.value_json = current
        row.updated_at = _now_iso()
    session.add(row)
    record_audit(
        session,
        action="settings.update",
        target="settings:app",
        diff=diff,
        actor=actor,
    )
    session.commit()
    return get_masked_settings_service(session)


# ---------------------------------------------------------------------------
# Admin Jobs View
# ---------------------------------------------------------------------------


def compute_job_disk_and_artifacts(job_id: str) -> tuple[int, list[dict[str, Any]]]:
    job_dir = get_storage().job_dir(job_id)
    total_bytes = 0
    artifacts: list[dict[str, Any]] = []
    if job_dir.exists():
        for p in sorted(job_dir.rglob("*")):
            if p.is_file() and not p.name.startswith(".tmp_"):
                sz = int(p.stat().st_size)
                total_bytes += sz
                artifacts.append(
                    {
                        "rel_path": p.relative_to(job_dir).as_posix(),
                        "size_bytes": sz,
                    }
                )
    return total_bytes, artifacts


# ---------------------------------------------------------------------------
# YAML helper (re-exported for config inspection)
# ---------------------------------------------------------------------------


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
