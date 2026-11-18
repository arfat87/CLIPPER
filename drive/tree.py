from __future__ import annotations

import argparse
import json
import os
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drive.routing import VALID_ROUTES, classify_default_route
from drive.security import DEFAULT_ROUTING


@dataclass
class _TreeNode:
    name: str
    is_folder: bool
    path: str
    children: dict[str, _TreeNode] = field(default_factory=dict)
    entry: Mapping[str, Any] | None = None
    explicit_route: str | None = None


def human_size(num_bytes: int | None) -> str:
    """Format byte count into deterministic human-readable units."""
    if num_bytes is None or num_bytes < 0:
        return ""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    if num_bytes < 1024 * 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024 * 1024):.1f} GB"
    return f"{num_bytes / (1024 * 1024 * 1024 * 1024):.1f} TB"


def _format_utc_header(generated_at: str) -> tuple[str, str]:
    raw = (generated_at or "").strip()
    if not raw:
        return "unknown UTC", "unknown"
    try:
        cleaned = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
        return dt.strftime("%Y-%m-%d %H:%M UTC"), dt.strftime("%Y-%m-%d")
    except ValueError:
        return raw, raw[:10] if len(raw) >= 10 else raw


def _collect_subtree_stats(node: _TreeNode) -> tuple[int, int, list[str]]:
    """Return (active_file_count, total_bytes, active_routes) for a folder node."""
    if not node.is_folder:
        entry = node.entry or {}
        status = str(entry.get("status", "SYNCED"))
        if status == "REMOVED":
            return 0, 0, []
        size_val = entry.get("size")
        bytes_val = int(size_val) if isinstance(size_val, int) and not isinstance(size_val, bool) and size_val >= 0 else 0
        route = str(entry.get("route", "unsorted"))
        return 1, bytes_val, [route]

    total_files = 0
    total_bytes = 0
    direct_routes: list[str] = []
    descendant_routes: list[str] = []
    for child in node.children.values():
        c_files, c_bytes, c_routes = _collect_subtree_stats(child)
        total_files += c_files
        total_bytes += c_bytes
        if not child.is_folder:
            direct_routes.extend(c_routes)
        descendant_routes.extend(c_routes)
    routes = direct_routes or descendant_routes
    return total_files, total_bytes, routes


def _resolve_folder_route(node: _TreeNode, descendant_routes: list[str]) -> str:
    if node.explicit_route and node.explicit_route in VALID_ROUTES:
        return node.explicit_route
    if descendant_routes:
        counts = Counter(descendant_routes)
        best_route, _ = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if best_route in VALID_ROUTES:
            return best_route
    return classify_default_route(f"{node.path}/", DEFAULT_ROUTING)


def _sorted_children(node: _TreeNode) -> list[_TreeNode]:
    return sorted(
        node.children.values(),
        key=lambda child: (
            0 if child.is_folder else 1,
            child.name.lower(),
            child.name,
            str((child.entry or {}).get("id", "")),
        ),
    )


def _render_folder_line(node: _TreeNode, connector: str, prefix: str) -> tuple[str, str]:
    file_count, total_bytes, routes = _collect_subtree_stats(node)
    route = _resolve_folder_route(node, routes)
    count_label = f"{file_count} {'file' if file_count == 1 else 'files'}"
    parts = [f"{prefix}{connector}{node.name}/", f"[{route}]", count_label]
    if total_bytes > 0:
        parts.append(human_size(total_bytes))
    if route == "doc":
        parts.append("(mirrored to docs/drive/)")
    return "  ".join(parts), route


def _render_file_line(
    node: _TreeNode,
    connector: str,
    prefix: str,
    *,
    parent_route: str | None,
) -> str:
    entry = node.entry or {}
    route = str(entry.get("route", "unsorted"))
    status = str(entry.get("status", "SYNCED"))
    size_val = entry.get("size")
    size_str = (
        human_size(int(size_val))
        if isinstance(size_val, int) and not isinstance(size_val, bool) and size_val >= 0
        else ""
    )
    error_msg = entry.get("error")
    exported_as = entry.get("exported_as")

    parts = [f"{prefix}{connector}{node.name}"]
    if parent_route is None or route == "unsorted" or route != parent_route:
        parts.append(f"[{route}]")
    parts.append(status)
    if size_str and status != "SKIPPED":
        parts.append(size_str)
    if route == "unsorted" and status != "REMOVED":
        parts.append("!! not in a known folder")
    if status == "STALE":
        parts.append("(modified after last download)")
    elif status == "ERROR" and error_msg:
        parts.append(f"({error_msg})")
    if exported_as:
        parts.append(f"(exported: {exported_as})")
    return "  ".join(parts)


def _render_subtree(
    node: _TreeNode,
    prefix: str,
    lines: list[str],
    *,
    parent_route: str | None,
) -> None:
    children = _sorted_children(node)
    total = len(children)
    for idx, child in enumerate(children):
        is_last = idx == total - 1
        connector = "└── " if is_last else "├── "
        child_prefix = f"{prefix}{'    ' if is_last else '│   '}"
        if child.is_folder:
            line, folder_route = _render_folder_line(child, connector, prefix)
            lines.append(line)
            _render_subtree(child, child_prefix, lines, parent_route=folder_route)
        else:
            lines.append(_render_file_line(child, connector, prefix, parent_route=parent_route))


def render_tree_md(manifest: Mapping[str, Any]) -> str:
    """Render deterministic TREE.md markdown purely from a Drive manifest dict."""
    ts_display, date_display = _format_utc_header(str(manifest.get("generated_at", "")))
    token = str(manifest.get("start_page_token", ""))
    root_label = str(manifest.get("root_name") or "Clipper").rstrip("/")

    lines: list[str] = [
        f"# Drive workspace tree  (generated {ts_display}, token {token})",
        f"{root_label}/",
    ]

    raw_files = manifest.get("files") or []
    files: list[Mapping[str, Any]] = [
        item for item in raw_files if isinstance(item, Mapping)
    ]

    root_node = _TreeNode(name=root_label, is_folder=True, path="")
    folder_paths: set[str] = set()

    for entry in sorted(files, key=lambda item: (str(item.get("path", "")), str(item.get("id", "")))):
        rel_path = str(entry.get("path") or entry.get("name") or "").strip("/")
        if not rel_path:
            continue
        segments = [seg for seg in rel_path.split("/") if seg]
        if not segments:
            continue

        is_folder_entry = bool(entry.get("is_folder", False))
        cur = root_node
        acc: list[str] = []
        folder_segs = segments if is_folder_entry else segments[:-1]
        for seg in folder_segs:
            acc.append(seg)
            folder_rel = "/".join(acc)
            folder_paths.add(folder_rel)
            if seg not in cur.children:
                cur.children[seg] = _TreeNode(name=seg, is_folder=True, path=folder_rel)
            cur = cur.children[seg]

        if is_folder_entry:
            route_val = entry.get("route")
            if isinstance(route_val, str) and route_val in VALID_ROUTES:
                cur.explicit_route = route_val
        else:
            leaf = segments[-1]
            node_key = leaf if leaf not in cur.children else f"{leaf}#{entry.get('id', len(cur.children))}"
            cur.children[node_key] = _TreeNode(
                name=leaf,
                is_folder=False,
                path=rel_path,
                entry=entry,
            )

    if root_node.children:
        _render_subtree(root_node, "", lines, parent_route=None)
    else:
        lines.append("└── (empty)")

    # Summary statistics (prefer manifest["stats"] with deterministic fallbacks)
    stats = manifest.get("stats") if isinstance(manifest.get("stats"), Mapping) else {}
    active_entries = [f for f in files if f.get("status") != "REMOVED"]
    computed_files = len(active_entries)
    computed_folders = len(folder_paths)
    computed_errors = sum(1 for f in active_entries if f.get("status") == "ERROR")
    computed_unsorted = sum(1 for f in active_entries if f.get("route") == "unsorted")

    summary_files = int(stats.get("files", computed_files)) if stats else computed_files
    summary_folders = int(stats.get("folders", computed_folders)) if stats else computed_folders
    summary_errors = int(stats.get("errors", computed_errors)) if stats else computed_errors
    summary_unsorted = int(stats.get("unsorted", computed_unsorted)) if stats else computed_unsorted

    lines.append("## Summary")
    lines.append(
        f"files {summary_files} | folders {summary_folders} | errors {summary_errors} | "
        f"unsorted {summary_unsorted} | last sync {date_display}"
    )

    # Attention bullets: unsorted, STALE, ERROR, and manifest/clipper warnings
    attention_bullets: list[str] = []
    for entry in sorted(files, key=lambda item: (str(item.get("path", "")), str(item.get("id", "")))):
        path = str(entry.get("path") or entry.get("name") or "")
        status = str(entry.get("status", ""))
        route = str(entry.get("route", ""))
        if status == "REMOVED":
            continue
        if route == "unsorted":
            attention_bullets.append(
                f"- {path} is unsorted: move it into a numbered folder or add a routing rule."
            )
        if status == "STALE":
            attention_bullets.append(
                f"- {path} is STALE: local mirror is out of date with Drive."
            )
        if status == "ERROR":
            err = str(entry.get("error") or "unknown error")
            attention_bullets.append(f"- {path} has ERROR: {err}.")

    raw_warnings = manifest.get("warnings")
    if isinstance(raw_warnings, list):
        seen_warnings: set[str] = set()
        for warn in raw_warnings:
            text = str(warn).strip()
            if text and text not in seen_warnings:
                seen_warnings.add(text)
                attention_bullets.append(f"- {text}")

    lines.append("## Attention")
    if attention_bullets:
        lines.extend(attention_bullets)
    else:
        lines.append("- None.")

    return "\n".join(lines) + "\n"


def write_tree_md(manifest: Mapping[str, Any], output_path: Path) -> Path:
    """Render TREE.md from manifest and write atomically via temp file + rename."""
    content = render_tree_md(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path = output_path.parent / f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        staged_path.write_text(content, encoding="utf-8")
        os.replace(staged_path, output_path)
    except BaseException:
        staged_path.unlink(missing_ok=True)
        raise
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate Drive TREE.md offline from manifest.json.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("drive_mirror/manifest.json"),
        help="Path to drive_mirror/manifest.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/drive/TREE.md"),
        help="Output path for TREE.md",
    )
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    write_tree_md(manifest, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
