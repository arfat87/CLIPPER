from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backend.app.config import ROOT_DIR, get_settings

logger = logging.getLogger(__name__)

CookieStatus = Literal["missing", "invalid", "valid"]

COOKIE_PLATFORMS: frozenset[str] = frozenset({"instagram", "facebook", "youtube"})

_NETSCAPE_HEADER_RE = re.compile(r"^#\s*(?:Netscape\s+)?HTTP\s+Cookie\s+File\b", re.IGNORECASE)
_EXPIRY_RE = re.compile(r"^-?\d+$")

_COOKIE_HEADER_RE = re.compile(
    r"(?im)\b(Cookie|Set-Cookie|Authorization)\s*:\s*[^\r\n]+"
)
_COOKIE_KV_RE = re.compile(
    r"(?i)\b(sessionid|csrftoken|ds_user_id|mid|ig_did|rur|c_user|xs|datr|sb|fr|wd)=([^;\s,'\"]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")


@dataclass(frozen=True)
class CookieValidationResult:
    """Result of resolving and validating a Netscape cookies.txt file."""

    platform: str
    status: CookieStatus
    path: Path | None = None
    cookie_count: int = 0
    has_header: bool = False
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status == "valid" and self.path is not None

    @property
    def valid_path(self) -> Path | None:
        return self.path if self.is_valid else None


def redact_cookie_secrets(text: str) -> str:
    """Redact common cookie/auth header values from untrusted error strings."""
    if not text:
        return ""
    cleaned = _COOKIE_HEADER_RE.sub(r"\1: [REDACTED]", text)
    cleaned = _COOKIE_KV_RE.sub(r"\1=[REDACTED]", cleaned)
    cleaned = _BEARER_RE.sub("Bearer [REDACTED]", cleaned)
    return cleaned


def _validate_netscape_line(line: str) -> bool:
    candidate = line
    if candidate.startswith("#HttpOnly_"):
        candidate = candidate[len("#HttpOnly_") :]
    elif candidate.lower().startswith("#httponly_"):
        candidate = candidate[len("#httponly_") :]

    parts = candidate.split("\t")
    if len(parts) != 7:
        return False

    domain, include_subdomains, cookie_path, secure, expiry, name, _value = parts
    if not domain.strip() or any(ch.isspace() for ch in domain.strip()):
        return False
    if include_subdomains.strip().upper() not in {"TRUE", "FALSE"}:
        return False
    if not cookie_path.strip():
        return False
    if secure.strip().upper() not in {"TRUE", "FALSE"}:
        return False
    if not _EXPIRY_RE.fullmatch(expiry.strip()):
        return False
    if not name.strip():
        return False
    return True


def validate_netscape_cookie_file(
    cookie_path: Path | str | None,
    *,
    platform: str = "unknown",
) -> CookieValidationResult:
    """Validate Netscape cookies.txt format without exposing file contents or cookie values."""
    if cookie_path is None:
        return CookieValidationResult(
            platform=platform,
            status="missing",
            path=None,
            reason="Cookie file path is not configured.",
        )

    candidate = Path(cookie_path)
    if not candidate.exists() or not candidate.is_file():
        return CookieValidationResult(
            platform=platform,
            status="missing",
            path=None,
            reason="Cookie file does not exist.",
        )

    try:
        raw_bytes = candidate.read_bytes()
    except OSError:
        return CookieValidationResult(
            platform=platform,
            status="invalid",
            path=candidate,
            reason="Cookie file could not be read.",
        )

    if not raw_bytes or not raw_bytes.strip():
        return CookieValidationResult(
            platform=platform,
            status="invalid",
            path=candidate,
            reason="Cookie file is empty.",
        )

    if b"\x00" in raw_bytes:
        return CookieValidationResult(
            platform=platform,
            status="invalid",
            path=candidate,
            reason="Cookie file contains binary bytes.",
        )

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return CookieValidationResult(
            platform=platform,
            status="invalid",
            path=candidate,
            reason="Cookie file is not valid UTF-8 text.",
        )

    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return CookieValidationResult(
            platform=platform,
            status="invalid",
            path=candidate,
            reason="Cookie file appears to be JSON rather than Netscape cookies.txt format.",
        )
    if stripped.startswith("<"):
        return CookieValidationResult(
            platform=platform,
            status="invalid",
            path=candidate,
            reason="Cookie file appears to be HTML/XML rather than Netscape cookies.txt format.",
        )

    has_header = False
    cookie_count = 0

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue

        if _NETSCAPE_HEADER_RE.match(line.strip()):
            has_header = True
            continue

        if line.lstrip().startswith("#") and not line.lower().startswith("#httponly_"):
            continue

        if not _validate_netscape_line(line):
            return CookieValidationResult(
                platform=platform,
                status="invalid",
                path=candidate,
                has_header=has_header,
                cookie_count=cookie_count,
                reason=f"Malformed Netscape cookie entry at line {line_no} (expected 7 tab-separated columns).",
            )
        cookie_count += 1

    if not has_header and cookie_count == 0:
        return CookieValidationResult(
            platform=platform,
            status="invalid",
            path=candidate,
            reason="Cookie file lacks '# Netscape HTTP Cookie File' header and valid cookie entries.",
        )

    return CookieValidationResult(
        platform=platform,
        status="valid",
        path=candidate,
        cookie_count=cookie_count,
        has_header=has_header,
        reason="Valid Netscape cookie file.",
    )


def _resolve_candidate_path(raw_path: str) -> Path:
    p = Path(raw_path)
    if p.is_absolute() or p.exists():
        return p
    rooted = ROOT_DIR / p
    return rooted if rooted.exists() else p


def resolve_cookie_file(
    platform: str,
    *,
    cookies_dir: Path | str | None = None,
    cookie_file: Path | str | None = None,
    settings: Any = None,
) -> CookieValidationResult:
    """Resolve and validate the Netscape cookie file for a platform."""
    norm_platform = (platform or "").strip().lower()

    if cookie_file is not None and str(cookie_file).strip():
        candidate = _resolve_candidate_path(str(cookie_file).strip())
        res = validate_netscape_cookie_file(candidate, platform=norm_platform)
        if res.status == "invalid":
            logger.warning(
                "Ignoring invalid cookie file for platform=%s: %s",
                norm_platform,
                res.reason,
            )
        return res

    if cookies_dir is not None:
        candidate = Path(cookies_dir) / f"{norm_platform}.txt"
        res = validate_netscape_cookie_file(candidate, platform=norm_platform)
        if res.status == "invalid":
            logger.warning(
                "Ignoring invalid cookie file for platform=%s: %s",
                norm_platform,
                res.reason,
            )
        return res

    cfg = settings or get_settings()
    mapping = {
        "youtube": getattr(cfg, "cookies_youtube", ""),
        "instagram": getattr(cfg, "cookies_instagram", ""),
        "facebook": getattr(cfg, "cookies_facebook", ""),
    }
    configured = str(mapping.get(norm_platform, "") or "").strip()
    if configured:
        configured_path = _resolve_candidate_path(configured)
        if configured_path.exists():
            res = validate_netscape_cookie_file(configured_path, platform=norm_platform)
            if res.status == "invalid":
                logger.warning(
                    "Ignoring invalid configured cookie file for platform=%s: %s",
                    norm_platform,
                    res.reason,
                )
            return res

    secrets_dir = Path(getattr(cfg, "secrets_dir", ROOT_DIR / "secrets"))
    default_path = secrets_dir / "cookies" / f"{norm_platform}.txt"
    res = validate_netscape_cookie_file(default_path, platform=norm_platform)
    if res.status == "invalid":
        logger.warning(
            "Ignoring invalid cookie file for platform=%s: %s",
            norm_platform,
            res.reason,
        )
    return res
