from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "drive.yaml"

DEFAULT_MAX_DOWNLOAD_MB = 8192
DEFAULT_MAX_EXPORT_MB = 10
DEFAULT_PAGE_SIZE = 1000
DEFAULT_MAX_SEGMENT_LEN = 120
DEFAULT_MAX_PATH_LEN = 240

DEFAULT_SKIP_EXTENSIONS: tuple[str, ...] = (".tmp", ".part", ".crdownload")

DEFAULT_EXPORT_MAP: dict[str, str] = {
    "application/vnd.google-apps.document": "text/markdown",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "application/pdf",
}

DEFAULT_ROUTING: tuple[dict[str, str], ...] = (
    {"match": "01_Sources", "route": "source"},
    {"match": "02_Templates", "route": "template"},
    {"match": "03_Fonts", "route": "font"},
    {"match": "04_BrandKits", "route": "brandkit"},
    {"match": "05_Docs", "route": "doc"},
    {"match": "06_Reference", "route": "reference"},
    {"match": "07_Exports", "route": "export"},
    {"match": "*", "route": "unsorted"},
)

RESERVED_NAMES: frozenset[str] = frozenset(
    {
        ".",
        "..",
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_UNSAFE_CHAR_RE = re.compile(r'[:*?"<>|]')


@dataclass(frozen=True)
class DrivePolicyConfig:
    """Drive sync security and routing configuration loaded from config/drive.yaml."""

    version: int = 1
    enabled: bool = False
    auth: str = "service_account"
    root_folder_id: str = ""
    poll_minutes: int = 5
    max_download_mb: int = DEFAULT_MAX_DOWNLOAD_MB
    max_export_mb: int = DEFAULT_MAX_EXPORT_MB
    page_size: int = DEFAULT_PAGE_SIZE
    mirror_dir: str = "drive_mirror"
    read_only: bool = True
    routing: tuple[dict[str, str], ...] = DEFAULT_ROUTING
    export_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EXPORT_MAP))
    video_mimes: tuple[str, ...] = (
        "video/mp4",
        "video/quicktime",
        "video/webm",
        "video/x-matroska",
    )
    skip_extensions: tuple[str, ...] = DEFAULT_SKIP_EXTENSIONS

    @property
    def max_download_bytes(self) -> int:
        return max(0, self.max_download_mb) * 1024 * 1024

    @property
    def max_export_bytes(self) -> int:
        return max(0, self.max_export_mb) * 1024 * 1024


def load_drive_config(config_path: Path | None = None) -> DrivePolicyConfig:
    """Load Drive configuration from YAML file (defaults to config/drive.yaml)."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return DrivePolicyConfig()

    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    routing_raw = raw.get("routing") or list(DEFAULT_ROUTING)
    routing = tuple(
        {"match": str(item["match"]), "route": str(item["route"])}
        for item in routing_raw
        if isinstance(item, dict) and "match" in item and "route" in item
    )
    export_map_raw = raw.get("export_map") or DEFAULT_EXPORT_MAP
    export_map = {str(k): str(v) for k, v in export_map_raw.items()}
    video_mimes = tuple(str(m) for m in (raw.get("video_mimes") or ()))
    skip_extensions = tuple(
        str(ext).lower() for ext in (raw.get("skip_extensions") or DEFAULT_SKIP_EXTENSIONS)
    )
    return DrivePolicyConfig(
        version=int(raw.get("version", 1)),
        enabled=bool(raw.get("enabled", False)),
        auth=str(raw.get("auth", "service_account")),
        root_folder_id=str(raw.get("root_folder_id", "")),
        poll_minutes=int(raw.get("poll_minutes", 5)),
        max_download_mb=int(raw.get("max_download_mb", DEFAULT_MAX_DOWNLOAD_MB)),
        max_export_mb=int(raw.get("max_export_mb", DEFAULT_MAX_EXPORT_MB)),
        page_size=int(raw.get("page_size", DEFAULT_PAGE_SIZE)),
        mirror_dir=str(raw.get("mirror_dir", "drive_mirror")),
        read_only=bool(raw.get("read_only", True)),
        routing=routing or DEFAULT_ROUTING,
        export_map=export_map,
        video_mimes=video_mimes,
        skip_extensions=skip_extensions,
    )


def _strip_control_chars(value: str) -> str:
    return "".join(
        ch for ch in value if ord(ch) >= 0x20 and ord(ch) != 0x7F and unicodedata.category(ch) != "Cc"
    )


def _short_id(file_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", file_id)
    if len(cleaned) >= 8:
        return cleaned[:8]
    if cleaned:
        digest = hashlib.sha1(file_id.encode("utf-8")).hexdigest()
        return (cleaned + digest)[:8]
    return hashlib.sha1(file_id.encode("utf-8")).hexdigest()[:8]


def _split_stem_suffix(name: str) -> tuple[str, str]:
    if "." not in name or name.startswith(".") and name.count(".") == 1:
        return name, ""
    stem, ext = name.rsplit(".", 1)
    suffix = f".{ext}"
    if len(suffix) > 16:
        return name, ""
    return stem, suffix


def _truncate_with_suffix(name: str, max_len: int) -> str:
    if len(name) <= max_len:
        return name
    stem, suffix = _split_stem_suffix(name)
    allowed_stem = max(1, max_len - len(suffix))
    truncated = f"{stem[:allowed_stem]}{suffix}"
    return truncated[:max_len]


def _append_id_suffix(name: str, id8: str, max_len: int) -> str:
    stem, suffix = _split_stem_suffix(name)
    tag = f"~{id8}"
    allowed_stem = max(1, max_len - len(tag) - len(suffix))
    return f"{stem[:allowed_stem]}{tag}{suffix}"


def sanitize_segment(
    name: str,
    file_id: str = "",
    *,
    max_len: int = DEFAULT_MAX_SEGMENT_LEN,
) -> str:
    """Sanitize a single Drive file or folder name for safe local mirror storage."""
    cleaned = _strip_control_chars(name)
    parts = [p for p in re.split(r"[/\\]+", cleaned) if p and p not in {".", ".."}]
    joined = "_".join(parts)
    joined = _UNSAFE_CHAR_RE.sub("_", joined)
    while ".." in joined:
        joined = joined.replace("..", "_")
    joined = joined.strip().rstrip(". ")

    if not joined or joined in {".", ".."}:
        joined = f"unnamed~{_short_id(file_id)}" if file_id else "unnamed"

    stem_head = joined.split(".", 1)[0].upper()
    if stem_head in RESERVED_NAMES:
        joined = f"_{joined}"

    return _truncate_with_suffix(joined, max_len)


def sanitize_mirror_path(
    segments: Iterable[str],
    file_id: str = "",
    *,
    used_paths: set[str] | None = None,
    max_segment_len: int = DEFAULT_MAX_SEGMENT_LEN,
    max_path_len: int = DEFAULT_MAX_PATH_LEN,
    extension_override: str | None = None,
) -> str:
    """Sanitize path segments into a unique, safe POSIX relative mirror path."""
    raw_list = [seg for seg in segments if seg is not None]
    sanitized_parts: list[str] = []
    for idx, seg in enumerate(raw_list):
        is_last = idx == len(raw_list) - 1
        seg_id = file_id if is_last else ""
        sanitized_parts.append(sanitize_segment(seg, seg_id, max_len=max_segment_len))

    if not sanitized_parts:
        sanitized_parts = [sanitize_segment("", file_id, max_len=max_segment_len)]

    if extension_override:
        ext = extension_override if extension_override.startswith(".") else f".{extension_override}"
        last = sanitized_parts[-1]
        if not last.lower().endswith(ext.lower()):
            sanitized_parts[-1] = _truncate_with_suffix(f"{last}{ext}", max_segment_len)

    rel_path = "/".join(sanitized_parts)
    if len(rel_path) > max_path_len:
        prefix = "/".join(sanitized_parts[:-1])
        prefix_len = len(prefix) + (1 if prefix else 0)
        remaining = max(16, max_path_len - prefix_len)
        last = sanitized_parts[-1]
        if file_id:
            last = _append_id_suffix(last, _short_id(file_id), remaining)
        else:
            last = _truncate_with_suffix(last, remaining)
        sanitized_parts[-1] = last
        rel_path = "/".join(sanitized_parts)
        if len(rel_path) > max_path_len:
            digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:8]
            leaf = _append_id_suffix(sanitized_parts[-1], _short_id(file_id or digest), max_path_len)
            rel_path = leaf[:max_path_len]

    if used_paths is not None:
        candidate = rel_path
        if candidate.lower() in used_paths:
            id8 = _short_id(file_id or candidate)
            parts = candidate.rsplit("/", 1)
            leaf = parts[-1]
            parent = f"{parts[0]}/" if len(parts) == 2 else ""
            leaf_limit = max(16, max_path_len - len(parent))
            tagged_leaf = _append_id_suffix(leaf, id8, min(max_segment_len, leaf_limit))
            candidate = f"{parent}{tagged_leaf}"
            counter = 2
            while candidate.lower() in used_paths:
                numbered_leaf = _append_id_suffix(
                    leaf, f"{id8}_{counter}", min(max_segment_len, leaf_limit)
                )
                candidate = f"{parent}{numbered_leaf}"
                counter += 1
        used_paths.add(candidate.lower())
        rel_path = candidate

    return rel_path


def assert_safe_mirror_path(mirror_root: Path, rel_path: str) -> Path:
    """Resolve rel_path under mirror_root and verify it never escapes mirror_root."""
    if not rel_path or rel_path.startswith("/") or "\\" in rel_path:
        raise ValueError(f"Unsafe mirror relative path: {rel_path!r}")
    parts = rel_path.split("/")
    if any(p in {"", ".", ".."} for p in parts):
        raise ValueError(f"Path traversal segment detected in mirror path: {rel_path!r}")

    resolved_root = mirror_root.resolve()
    resolved_target = (resolved_root / rel_path).resolve()
    if resolved_target == resolved_root or not resolved_target.is_relative_to(resolved_root):
        raise ValueError(f"Mirror path escapes mirror root: {rel_path!r}")
    return resolved_target


def is_skipped_extension(name: str, skip_extensions: Iterable[str] = DEFAULT_SKIP_EXTENSIONS) -> bool:
    lowered = name.strip().lower()
    return any(lowered.endswith(ext.lower()) for ext in skip_extensions if ext)


def check_download_size(size_bytes: int | None, max_download_mb: int) -> tuple[bool, str | None]:
    if size_bytes is None:
        return True, None
    limit_bytes = max(0, max_download_mb) * 1024 * 1024
    if size_bytes > limit_bytes:
        return False, f"too_large: {size_bytes} bytes exceeds max_download_mb={max_download_mb}"
    return True, None


def check_export_size(size_bytes: int, max_export_mb: int = DEFAULT_MAX_EXPORT_MB) -> tuple[bool, str | None]:
    limit_bytes = max(0, max_export_mb) * 1024 * 1024
    if size_bytes > limit_bytes:
        return False, f"too_large: exported {size_bytes} bytes exceeds max_export_mb={max_export_mb}"
    return True, None
