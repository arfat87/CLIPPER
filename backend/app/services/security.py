from __future__ import annotations

import hmac
import ipaddress
import os
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.services.admin import ADMIN_SESSION_COOKIE, verify_admin_session_token
from backend.app.services.resilience import ErrorCode

_TRUTHY = {"1", "true", "yes", "on"}

_FORBIDDEN_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "instance-data",
    "169.254.169.254",
    "100.100.100.200",
}


class SecurityError(RuntimeError):
    """Structured security / authorization / rate-limit error."""

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

    def to_envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": dict(self.details),
                "retryable": self.retryable,
            }
        }


# ---------------------------------------------------------------------------
# 1. Bearer Token & Session Auth Enforcement (CLIPPER_AUTH_TOKEN)
# ---------------------------------------------------------------------------


def get_configured_auth_token(env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return (source.get("CLIPPER_AUTH_TOKEN") or "").strip()


def extract_bearer_token(headers: Mapping[str, str]) -> str | None:
    auth_val = ""
    for k, v in headers.items():
        if k.lower() == "authorization":
            auth_val = v.strip()
            break
    if not auth_val:
        return None
    parts = auth_val.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def verify_bearer_or_session_auth(
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    expected_token: str | None = None,
    path: str = "",
) -> bool:
    """Verify request authorization when `CLIPPER_AUTH_TOKEN` is configured."""
    clean_path = (path or "").strip()
    if clean_path in ("/", "/api/v1/health", "/api/v1/admin/auth/login"):
        return True

    token_cfg = (
        expected_token.strip()
        if expected_token is not None
        else get_configured_auth_token()
    )
    if not token_cfg:
        return True

    hdrs = headers or {}
    cks = cookies or {}

    bearer = extract_bearer_token(hdrs)
    if bearer and hmac.compare_digest(bearer.encode("utf-8"), token_cfg.encode("utf-8")):
        return True

    session_tok = cks.get(ADMIN_SESSION_COOKIE)
    if session_tok and verify_admin_session_token(session_tok):
        return True

    raise SecurityError(
        401,
        ErrorCode.LOGIN_REQUIRED.value,
        "Valid Bearer token or authenticated session required",
        details={"header": "Authorization: Bearer <token>"},
        retryable=False,
    )


def require_bearer_auth(request: Request) -> str:
    """FastAPI dependency enforcing `CLIPPER_AUTH_TOKEN` when configured."""
    verify_bearer_or_session_auth(
        headers=dict(request.headers),
        cookies=dict(request.cookies),
        path=request.url.path,
    )
    return "authenticated"


# ---------------------------------------------------------------------------
# 2. Token-Bucket & Sliding-Window Rate Limiters per (IP, route)
# ---------------------------------------------------------------------------


@dataclass
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_s: float
    limit: int


class TokenBucketRateLimiter:
    """Thread-safe token-bucket rate limiter keyed by `(client_ip, route)`."""

    def __init__(
        self,
        *,
        capacity: int = 30,
        refill_rate_per_sec: float = 1.0,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = max(1, int(capacity))
        self.refill_rate_per_sec = max(0.001, float(refill_rate_per_sec))
        self.now_fn = now_fn
        self._lock = threading.RLock()
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def check(
        self,
        client_ip: str,
        route: str = "*",
        *,
        cost: float = 1.0,
        raise_on_limit: bool = False,
    ) -> RateLimitDecision:
        key = ((client_ip or "unknown").strip(), (route or "*").strip())
        needed = max(1.0, float(cost))
        with self._lock:
            now = self.now_fn()
            tokens, last_ts = self._buckets.get(key, (float(self.capacity), now))
            elapsed = max(0.0, now - last_ts)
            tokens = min(float(self.capacity), tokens + elapsed * self.refill_rate_per_sec)
            if tokens >= needed:
                tokens -= needed
                self._buckets[key] = (tokens, now)
                return RateLimitDecision(
                    allowed=True,
                    remaining=int(tokens),
                    retry_after_s=0.0,
                    limit=self.capacity,
                )
            deficit = needed - tokens
            retry_after = round(deficit / self.refill_rate_per_sec, 3)
            self._buckets[key] = (tokens, now)

        if raise_on_limit:
            raise SecurityError(
                429,
                ErrorCode.RATE_LIMITED.value,
                f"Rate limit exceeded for {route}; retry after {retry_after}s",
                details={"retry_after_s": retry_after, "route": route, "limit": self.capacity},
                retryable=True,
            )
        return RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after_s=retry_after,
            limit=self.capacity,
        )


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window rate limiter keyed by `(client_ip, route)`."""

    def __init__(
        self,
        *,
        max_requests: int = 30,
        window_seconds: float = 60.0,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(0.1, float(window_seconds))
        self.now_fn = now_fn
        self._lock = threading.RLock()
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(
        self,
        client_ip: str,
        route: str = "*",
        *,
        raise_on_limit: bool = False,
    ) -> RateLimitDecision:
        key = ((client_ip or "unknown").strip(), (route or "*").strip())
        with self._lock:
            now = self.now_fn()
            cutoff = now - self.window_seconds
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) < self.max_requests:
                q.append(now)
                return RateLimitDecision(
                    allowed=True,
                    remaining=self.max_requests - len(q),
                    retry_after_s=0.0,
                    limit=self.max_requests,
                )
            oldest = q[0]
            retry_after = round(max(0.001, (oldest + self.window_seconds) - now), 3)

        if raise_on_limit:
            raise SecurityError(
                429,
                ErrorCode.RATE_LIMITED.value,
                f"Rate limit exceeded for {route}; retry after {retry_after}s",
                details={"retry_after_s": retry_after, "route": route, "limit": self.max_requests},
                retryable=True,
            )
        return RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after_s=retry_after,
            limit=self.max_requests,
        )


# ---------------------------------------------------------------------------
# 3. Rights Checkbox Enforcement (rights_confirmed / confirm_rights)
# ---------------------------------------------------------------------------


def is_rights_confirmation_required(
    *,
    strict: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    if strict is not None:
        return bool(strict)
    source = env if env is not None else os.environ
    raw = (source.get("CLIPPER_REQUIRE_RIGHTS_CONFIRMATION") or "").strip().lower()
    if raw in _TRUTHY:
        return True
    app_env = (source.get("APP_ENV") or "").strip().lower()
    return app_env == "prod"


def enforce_rights_confirmation(
    payload: Mapping[str, Any] | None = None,
    *,
    rights_confirmed: bool | None = None,
    confirm_rights: bool | None = None,
    strict: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Validate rights confirmation checkbox (`rights_confirmed` or `confirm_rights`)."""
    val: Any = rights_confirmed if rights_confirmed is not None else confirm_rights
    if val is None and isinstance(payload, Mapping):
        if "rights_confirmed" in payload and payload["rights_confirmed"] is not None:
            val = payload["rights_confirmed"]
        elif "confirm_rights" in payload and payload["confirm_rights"] is not None:
            val = payload["confirm_rights"]

    if val is False:
        raise SecurityError(
            422,
            ErrorCode.VALIDATION_ERROR.value,
            "Rights confirmation is required (rights_confirmed / confirm_rights must be true)",
            details={"field": "rights_confirmed"},
            retryable=False,
        )

    must_require = is_rights_confirmation_required(strict=strict, env=env)
    if must_require and val is not True:
        raise SecurityError(
            422,
            ErrorCode.VALIDATION_ERROR.value,
            "Explicit rights confirmation checkbox (rights_confirmed=true) is required",
            details={"field": "rights_confirmed", "strict": True},
            retryable=False,
        )

    return True


# ---------------------------------------------------------------------------
# 4. SSRF & Path-Traversal Guards
# ---------------------------------------------------------------------------


def _is_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_safe_source_url(
    raw_url: str,
    *,
    allow_sample_scheme: bool = True,
    allow_drive_scheme: bool = True,
    resolve_dns: bool = False,
    resolver: Callable[[str], list[str]] | None = None,
) -> str:
    """Validate a source URL against SSRF (private/loopback/link-local/metadata IPs & bad schemes)."""
    url = (raw_url or "").strip()
    if not url:
        raise SecurityError(422, ErrorCode.UNSUPPORTED_URL.value, "Source URL cannot be empty")

    if allow_sample_scheme and url.startswith("sample://"):
        sample_id = url[len("sample://") :].strip()
        if not sample_id or "/" in sample_id or "\\" in sample_id or ".." in sample_id:
            raise SecurityError(422, ErrorCode.UNSUPPORTED_URL.value, f"Invalid sample URL: {url!r}")
        return url

    if allow_drive_scheme and url.startswith("drive://"):
        file_id = url[len("drive://") :].strip()
        if not file_id or "/" in file_id or "\\" in file_id or ".." in file_id:
            raise SecurityError(422, ErrorCode.UNSUPPORTED_URL.value, f"Invalid drive URL: {url!r}")
        return url

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SecurityError(
            422,
            ErrorCode.UNSUPPORTED_URL.value,
            f"Unsupported URL scheme {scheme!r}; only http and https are allowed",
            details={"scheme": scheme},
        )

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise SecurityError(422, ErrorCode.UNSUPPORTED_URL.value, "URL is missing hostname")

    if hostname in _FORBIDDEN_HOSTNAMES or hostname.endswith(".internal") or hostname.endswith(".local"):
        raise SecurityError(
            422,
            ErrorCode.UNSUPPORTED_URL.value,
            f"Access to internal/local host {hostname!r} is forbidden (SSRF protection)",
            details={"hostname": hostname},
        )

    # Direct IP literal check (including IPv6 and integer/hex representations parsed by ip_address)
    ip_parsed: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        ip_parsed = ipaddress.ip_address(hostname)
    except ValueError:
        ip_parsed = None

    if ip_parsed is not None:
        if _is_forbidden_ip(ip_parsed):
            raise SecurityError(
                422,
                ErrorCode.UNSUPPORTED_URL.value,
                f"Access to private/loopback/link-local IP {ip_parsed} is forbidden (SSRF protection)",
                details={"ip": str(ip_parsed)},
            )
        return url

    if resolve_dns or resolver is not None:
        try:
            if resolver is not None:
                resolved_ips = resolver(hostname)
            else:
                addr_info = socket.getaddrinfo(hostname, None)
                resolved_ips = [str(item[4][0]) for item in addr_info if item and item[4]]
        except Exception as exc:
            raise SecurityError(
                422,
                ErrorCode.UNSUPPORTED_URL.value,
                f"Failed to resolve hostname {hostname!r}: {exc}",
            ) from exc

        for ip_str in resolved_ips:
            try:
                ip_val = ipaddress.ip_address(ip_str.split("%")[0])
            except ValueError:
                continue
            if _is_forbidden_ip(ip_val):
                raise SecurityError(
                    422,
                    ErrorCode.UNSUPPORTED_URL.value,
                    f"Hostname {hostname!r} resolves to forbidden IP {ip_val} (SSRF protection)",
                    details={"hostname": hostname, "ip": str(ip_val)},
                )

    return url


def validate_safe_file_path(
    base_dir: Path,
    candidate_path: str | Path,
    *,
    allow_symlinks: bool = False,
) -> Path:
    """Validate that `candidate_path` stays strictly inside `base_dir` (no `..`, null bytes, or symlink escape)."""
    raw_str = str(candidate_path)
    if "\x00" in raw_str or not raw_str.strip():
        raise SecurityError(
            422,
            ErrorCode.VALIDATION_ERROR.value,
            "Invalid file path",
        )

    norm_slashes = raw_str.replace("\\", "/")
    parts = [seg for seg in norm_slashes.split("/") if seg and seg != "."]
    if any(seg == ".." for seg in parts):
        raise SecurityError(
            422,
            ErrorCode.VALIDATION_ERROR.value,
            f"Path traversal sequence '..' is forbidden in {raw_str!r}",
        )

    cand = Path(raw_str)
    base_resolved = base_dir.resolve()
    joined = cand if cand.is_absolute() else (base_dir / cand)

    if not allow_symlinks:
        # Walk prefix components to reject symlinks inside base_dir
        cur = base_dir
        rel_parts = parts if not cand.is_absolute() else []
        for seg in rel_parts:
            cur = cur / seg
            if cur.is_symlink():
                raise SecurityError(
                    422,
                    ErrorCode.VALIDATION_ERROR.value,
                    f"Symlink path {cur} is forbidden",
                )

    resolved = joined.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise SecurityError(
            422,
            ErrorCode.VALIDATION_ERROR.value,
            f"Path {raw_str!r} escapes allowed root directory",
        ) from exc

    return resolved


# ---------------------------------------------------------------------------
# 5. Optional ASGI Middleware combining Bearer Auth & Rate Limiting
# ---------------------------------------------------------------------------


class ClipperSecurityMiddleware(BaseHTTPMiddleware):
    """ASGI middleware enforcing optional `CLIPPER_AUTH_TOKEN` and per-IP/route rate limiting."""

    def __init__(
        self,
        app: Any,
        *,
        rate_limiter: TokenBucketRateLimiter | SlidingWindowRateLimiter | None = None,
        auth_token: str | None = None,
    ) -> None:
        super().__init__(app)
        self.rate_limiter = rate_limiter
        self.auth_token = auth_token

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Any:
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path

        try:
            verify_bearer_or_session_auth(
                headers=dict(request.headers),
                cookies=dict(request.cookies),
                expected_token=self.auth_token,
                path=path,
            )
            if self.rate_limiter is not None:
                self.rate_limiter.check(client_ip, path, raise_on_limit=True)
        except SecurityError as exc:
            headers: dict[str, str] = {}
            if exc.status_code == 429 and "retry_after_s" in exc.details:
                headers["Retry-After"] = str(max(1, int(round(float(exc.details["retry_after_s"])))))
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.to_envelope(),
                headers=headers,
            )

        return await call_next(request)


__all__ = [
    "ClipperSecurityMiddleware",
    "RateLimitDecision",
    "SecurityError",
    "SlidingWindowRateLimiter",
    "TokenBucketRateLimiter",
    "enforce_rights_confirmation",
    "extract_bearer_token",
    "get_configured_auth_token",
    "is_rights_confirmation_required",
    "require_bearer_auth",
    "validate_safe_file_path",
    "validate_safe_source_url",
    "verify_bearer_or_session_auth",
]
