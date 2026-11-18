from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

VALID_ROUTES: frozenset[str] = frozenset(
    {
        "source",
        "template",
        "font",
        "brandkit",
        "doc",
        "reference",
        "export",
        "unsorted",
    }
)

CLIPPER_YAML_FILENAME = ".clipper.yaml"
MAX_CLIPPER_YAML_BYTES = 64 * 1024
MAX_IGNORE_PATTERNS = 64
MAX_REGEX_LENGTH = 256
_ALLOWED_KEYS: frozenset[str] = frozenset({"route", "ignore"})


@dataclass(frozen=True)
class FolderRoutingOverride:
    """Validated routing and ignore overrides from a folder's .clipper.yaml."""

    route: str | None = None
    ignore_patterns: tuple[re.Pattern[str], ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.route is None and not self.ignore_patterns

    def matches_ignore(self, item_name: str, rel_to_folder: str = "") -> bool:
        for pattern in self.ignore_patterns:
            if pattern.search(item_name):
                return True
            if rel_to_folder and pattern.search(rel_to_folder):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "ignore": [p.pattern for p in self.ignore_patterns],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> FolderRoutingOverride:
        if not data:
            return cls()
        raw_route = data.get("route")
        route = raw_route if isinstance(raw_route, str) and raw_route in VALID_ROUTES else None
        compiled: list[re.Pattern[str]] = []
        raw_ignore = data.get("ignore")
        if isinstance(raw_ignore, list):
            for pat in raw_ignore:
                if isinstance(pat, str):
                    ok, regex_obj, _ = validate_safe_regex(pat)
                    if ok and regex_obj is not None:
                        compiled.append(regex_obj)
        return cls(route=route, ignore_patterns=tuple(compiled))


@dataclass(frozen=True)
class ClipperYamlResult:
    """Result of parsing and validating an untrusted .clipper.yaml file."""

    override: FolderRoutingOverride
    warnings: list[str] = field(default_factory=list)
    valid: bool = True


def _has_nested_quantifier(pattern: str) -> bool:
    """Heuristic check rejecting nested quantifiers like (a+)+ or (.*)* to prevent ReDoS."""
    stack: list[bool] = []
    i = 0
    n = len(pattern)
    in_char_class = False

    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if in_char_class:
            if ch == "]":
                in_char_class = False
            i += 1
            continue
        if ch == "[":
            in_char_class = True
            i += 1
            continue
        if ch == "(":
            stack.append(False)
            i += 1
            continue
        if ch == ")":
            contained_quantifier = stack.pop() if stack else False
            if stack and contained_quantifier:
                stack[-1] = True
            next_ch = pattern[i + 1] if (i + 1) < n else ""
            if contained_quantifier and next_ch in {"+", "*", "{"}:
                return True
            i += 1
            continue
        if ch in {"+", "*"}:
            if stack:
                stack[-1] = True
        elif ch == "{":
            close_idx = pattern.find("}", i + 1)
            if close_idx != -1 and "," in pattern[i + 1 : close_idx]:
                if stack:
                    stack[-1] = True
        i += 1

    return False


def validate_safe_regex(pattern: str) -> tuple[bool, re.Pattern[str] | None, str | None]:
    """Validate and compile an untrusted ignore regex pattern."""
    if not isinstance(pattern, str) or not pattern:
        return False, None, "regex pattern must be a non-empty string"
    if len(pattern) > MAX_REGEX_LENGTH:
        return False, None, f"regex pattern exceeds {MAX_REGEX_LENGTH} characters"
    if "\x00" in pattern:
        return False, None, "regex pattern contains NUL byte"
    if _has_nested_quantifier(pattern):
        return False, None, "regex pattern contains nested quantifiers"
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return False, None, f"invalid regex syntax: {exc}"
    return True, compiled, None


def parse_clipper_yaml(
    raw_content: bytes | str,
    *,
    folder_path: str = "",
) -> ClipperYamlResult:
    """Strictly parse and validate an untrusted .clipper.yaml file.

    Any malformed YAML, invalid type, invalid route enum, or unsafe/invalid regex
    produces a warning and safely falls back to an empty FolderRoutingOverride
    so default routing from config/drive.yaml is used.
    """
    location = folder_path or "<root>"
    if isinstance(raw_content, bytes):
        if len(raw_content) > MAX_CLIPPER_YAML_BYTES:
            return ClipperYamlResult(
                override=FolderRoutingOverride(),
                warnings=[
                    f"Invalid .clipper.yaml in '{location}': file exceeds {MAX_CLIPPER_YAML_BYTES} bytes; "
                    "falling back to default routing."
                ],
                valid=False,
            )
        try:
            text = raw_content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            return ClipperYamlResult(
                override=FolderRoutingOverride(),
                warnings=[
                    f"Invalid .clipper.yaml in '{location}': invalid UTF-8 ({exc}); "
                    "falling back to default routing."
                ],
                valid=False,
            )
    else:
        if len(raw_content.encode("utf-8")) > MAX_CLIPPER_YAML_BYTES:
            return ClipperYamlResult(
                override=FolderRoutingOverride(),
                warnings=[
                    f"Invalid .clipper.yaml in '{location}': file exceeds {MAX_CLIPPER_YAML_BYTES} bytes; "
                    "falling back to default routing."
                ],
                valid=False,
            )
        text = raw_content

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return ClipperYamlResult(
            override=FolderRoutingOverride(),
            warnings=[
                f"Invalid .clipper.yaml in '{location}': YAML parse error ({exc}); "
                "falling back to default routing."
            ],
            valid=False,
        )

    if loaded is None:
        return ClipperYamlResult(override=FolderRoutingOverride(), warnings=[], valid=True)

    if not isinstance(loaded, dict):
        return ClipperYamlResult(
            override=FolderRoutingOverride(),
            warnings=[
                f"Invalid .clipper.yaml in '{location}': top-level value must be a mapping; "
                "falling back to default routing."
            ],
            valid=False,
        )

    warnings: list[str] = []
    unknown_keys = sorted(str(k) for k in loaded if str(k) not in _ALLOWED_KEYS)
    if unknown_keys:
        return ClipperYamlResult(
            override=FolderRoutingOverride(),
            warnings=[
                f"Invalid .clipper.yaml in '{location}': unsupported key(s) {', '.join(unknown_keys)}; "
                "falling back to default routing."
            ],
            valid=False,
        )

    route_val: str | None = None
    if "route" in loaded and loaded["route"] is not None:
        raw_route = loaded["route"]
        if not isinstance(raw_route, str) or raw_route not in VALID_ROUTES:
            return ClipperYamlResult(
                override=FolderRoutingOverride(),
                warnings=[
                    f"Invalid .clipper.yaml in '{location}': invalid route {raw_route!r} "
                    f"(expected one of {sorted(VALID_ROUTES)}); falling back to default routing."
                ],
                valid=False,
            )
        route_val = raw_route

    compiled_ignores: list[re.Pattern[str]] = []
    if "ignore" in loaded and loaded["ignore"] is not None:
        raw_ignore = loaded["ignore"]
        if not isinstance(raw_ignore, list):
            return ClipperYamlResult(
                override=FolderRoutingOverride(),
                warnings=[
                    f"Invalid .clipper.yaml in '{location}': 'ignore' must be a list of regex strings; "
                    "falling back to default routing."
                ],
                valid=False,
            )
        if len(raw_ignore) > MAX_IGNORE_PATTERNS:
            return ClipperYamlResult(
                override=FolderRoutingOverride(),
                warnings=[
                    f"Invalid .clipper.yaml in '{location}': 'ignore' exceeds {MAX_IGNORE_PATTERNS} patterns; "
                    "falling back to default routing."
                ],
                valid=False,
            )
        for pat in raw_ignore:
            if not isinstance(pat, str):
                return ClipperYamlResult(
                    override=FolderRoutingOverride(),
                    warnings=[
                        f"Invalid .clipper.yaml in '{location}': ignore entry {pat!r} is not a string; "
                        "falling back to default routing."
                    ],
                    valid=False,
                )
            ok, compiled, reason = validate_safe_regex(pat)
            if not ok or compiled is None:
                return ClipperYamlResult(
                    override=FolderRoutingOverride(),
                    warnings=[
                        f"Invalid .clipper.yaml in '{location}': invalid ignore regex {pat!r} ({reason}); "
                        "falling back to default routing."
                    ],
                    valid=False,
                )
            compiled_ignores.append(compiled)

    return ClipperYamlResult(
        override=FolderRoutingOverride(route=route_val, ignore_patterns=tuple(compiled_ignores)),
        warnings=warnings,
        valid=True,
    )


def classify_default_route(rel_path: str, routing: Iterable[Mapping[str, str]]) -> str:
    """Determine default route from first workspace folder segment using config/drive.yaml rules."""
    first_folder = rel_path.split("/", 1)[0] if "/" in rel_path else ""
    for rule in routing:
        pattern = rule["match"]
        if pattern == "*":
            route = rule["route"]
            return route if route in VALID_ROUTES else "unsorted"
        if first_folder and first_folder.lower() == pattern.lower():
            route = rule["route"]
            return route if route in VALID_ROUTES else "unsorted"
    return "unsorted"


def resolve_folder_routing(
    workspace_path: str,
    item_name: str,
    *,
    default_routing: Iterable[Mapping[str, str]],
    folder_chain: Iterable[str],
    folder_overrides: Mapping[str, FolderRoutingOverride],
    folder_segments: Mapping[str, list[str]] | None = None,
) -> tuple[str, bool]:
    """Resolve effective route and ignore status for an item along its folder ancestor chain.

    Returns (route, is_ignored).
    """
    effective_route = classify_default_route(workspace_path, default_routing)
    ignored = False

    for fid in folder_chain:
        override = folder_overrides.get(fid)
        if override is None:
            continue
        if override.route is not None and override.route in VALID_ROUTES:
            effective_route = override.route

        rel_to_folder = item_name
        if folder_segments is not None and fid in folder_segments:
            segs = folder_segments[fid]
            prefix = f"{'/'.join(segs)}/" if segs else ""
            if prefix and workspace_path.startswith(prefix):
                rel_to_folder = workspace_path[len(prefix) :]
            elif not prefix:
                rel_to_folder = workspace_path

        if override.matches_ignore(item_name, rel_to_folder):
            ignored = True

    if effective_route not in VALID_ROUTES:
        effective_route = "unsorted"
    return effective_route, ignored


def validate_manifest_invariants(manifest: Mapping[str, Any]) -> None:
    """Enforce manifest invariants: unique POSIX path and single valid route per item."""
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("Manifest 'files' must be a list.")

    seen_paths: set[str] = set()
    for idx, entry in enumerate(files):
        if not isinstance(entry, Mapping):
            raise ValueError(f"Manifest files[{idx}] must be an object.")
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError(f"Manifest files[{idx}] missing non-empty POSIX 'path'.")
        if path.startswith("/") or "\\" in path:
            raise ValueError(f"Manifest files[{idx}] has non-POSIX path: {path!r}")
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"Manifest files[{idx}] has invalid path segment: {path!r}")
        if path in seen_paths:
            raise ValueError(f"Duplicate manifest path detected: {path!r}")
        seen_paths.add(path)

        route = entry.get("route")
        if not isinstance(route, str) or route not in VALID_ROUTES:
            raise ValueError(f"Manifest files[{idx}] has invalid route: {route!r}")
