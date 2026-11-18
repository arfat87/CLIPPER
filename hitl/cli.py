from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from hitl.models import BlockerStatus, BlockerType, NeededItem, detect_leaked_secret
from hitl.raise_blocker import (
    create_or_get_blocker,
    get_blocker,
    list_blockers,
    reject_blocker,
    resolve_blocker,
    resolve_state_dir,
    sync_blockers_md,
)
from hitl.validators import validate_blocker_resolution


def parse_reply_command(subject: str, body: str) -> dict[str, Any]:
    """Parse an inbound owner email reply for correlation token, command, and leak warnings."""
    token_match = re.search(r"(CLP-[0-9a-f]{6})", subject + " " + body, re.IGNORECASE)
    blocker_match = re.search(r"(BLK-\d{4})", subject + " " + body, re.IGNORECASE)

    if detect_leaked_secret(body):
        return {
            "valid": False,
            "error": "SECRET_LEAK_DETECTED",
            "message": "Raw secret detected in email body. Refusing to ingest; please rotate the key and place it in .env.",
            "token": token_match.group(1) if token_match else None,
            "blocker_id": blocker_match.group(1).upper() if blocker_match else None,
        }

    # First non-empty, non-quoted line
    first_line = ""
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(">") or s.lower().startswith("on "):
            continue
        first_line = s
        break

    upper = first_line.upper()
    command = "NOTE"
    if upper.startswith("DONE"):
        command = "DONE"
    elif "USE OLLAMA" in upper:
        command = "USE_OLLAMA"
    elif upper.startswith("SKIP"):
        command = "SKIP"
    elif upper.startswith("OPTION"):
        command = upper

    return {
        "valid": True,
        "command": command,
        "note": first_line,
        "token": token_match.group(1) if token_match else None,
        "blocker_id": blocker_match.group(1).upper() if blocker_match else None,
    }


def run_loop_status(
    root_dir: Path | None = None,
    state_dir: Path | str | None = None,
) -> dict[str, Any]:
    root = root_dir or Path.cwd()
    sdir = resolve_state_dir(root, state_dir, create=False)
    tasks_file = sdir / "tasks.yaml"
    if not tasks_file.exists():
        # Backward-compat fallback if tasks.yaml lives in .agent/
        alt_tasks = root / ".agent" / "tasks.yaml"
        if alt_tasks.exists():
            tasks_file = alt_tasks
    tasks_data = (
        yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
        if tasks_file.exists()
        else {"tasks": []}
    )
    tasks = tasks_data.get("tasks", [])
    blockers = list_blockers(root, state_dir=state_dir)
    open_blockers = [b for b in blockers if b.status == BlockerStatus.OPEN]
    done_tasks = [t for t in tasks if t.get("status") == "DONE"]
    return {
        "state_dir": sdir.as_posix(),
        "total_tasks": len(tasks),
        "done_tasks": len(done_tasks),
        "open_blockers": [b.model_dump(mode="json") for b in open_blockers],
        "all_blockers_count": len(blockers),
    }


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=None, help="Project root directory")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="State directory (defaults to .clipper-state/ when present or new, else .agent/)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hitl", description="CLIPPER HITL & Autonomous Loop CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Show task graph and blocker status")
    _add_common_flags(p_status)

    p_raise = sub.add_parser("raise", help="Raise a new HITL blocker")
    _add_common_flags(p_raise)
    p_raise.add_argument("--type", choices=[e.value for e in BlockerType], default="SECRET")
    p_raise.add_argument("--task", required=True)
    p_raise.add_argument("--key", required=True)
    p_raise.add_argument("--title", required=True)
    p_raise.add_argument("--why", default="Required by pipeline stage")
    p_raise.add_argument("--step", type=int, default=1)
    p_raise.add_argument("--where", default=".env")
    p_raise.add_argument("--how-to-get", default="Provide in .env")

    p_res = sub.add_parser("resolve", help="Resolve a blocker by ID")
    _add_common_flags(p_res)
    p_res.add_argument("blocker_id")
    p_res.add_argument("--note", default="Resolved via CLI")
    p_res.add_argument("--validate", action="store_true", help="Validate requirements before resolving")

    p_rej = sub.add_parser("reject", help="Reject a blocker resolution by ID")
    _add_common_flags(p_rej)
    p_rej.add_argument("blocker_id")
    p_rej.add_argument("--reason", required=True)

    p_val = sub.add_parser("validate", help="Validate a blocker's resolution requirements")
    _add_common_flags(p_val)
    p_val.add_argument("blocker_id")

    p_sync = sub.add_parser("sync-md", help="Regenerate BLOCKERS.md from blocker YAML files")
    _add_common_flags(p_sync)

    p_loop = sub.add_parser("loop", help="Run autonomous task scheduler check")
    _add_common_flags(p_loop)

    args = parser.parse_args(argv)
    if args.cmd == "status":
        st = run_loop_status(root_dir=args.root, state_dir=args.state_dir)
        print(
            f"Tasks: {st['done_tasks']}/{st['total_tasks']} DONE | Open Blockers: {len(st['open_blockers'])}"
        )
        for b in st["open_blockers"]:
            print(
                f"  - [{b['id']}] ({b['type']}) {b['title']} [token: {b['correlation_token']}]"
            )
        return 0
    if args.cmd == "raise":
        b = create_or_get_blocker(
            btype=BlockerType(args.type),
            task_id=args.task,
            title=args.title,
            needed=[NeededItem(key=args.key, where=args.where, how_to_get=args.how_to_get)],
            why=args.why,
            step=args.step,
            root_dir=args.root,
            state_dir=args.state_dir,
        )
        print(f"Created blocker {b.id} (token {b.correlation_token})")
        return 0
    if args.cmd == "resolve":
        b = resolve_blocker(
            args.blocker_id,
            note=args.note,
            root_dir=args.root,
            state_dir=args.state_dir,
            validate=args.validate,
        )
        if b.status == BlockerStatus.REJECTED:
            print(f"Rejected {b.id}: {b.resolution_note}", file=sys.stderr)
            return 1
        print(f"Resolved {b.id} at {b.resolved_at}")
        return 0
    if args.cmd == "reject":
        b = reject_blocker(
            args.blocker_id,
            reason=args.reason,
            root_dir=args.root,
            state_dir=args.state_dir,
        )
        print(f"Rejected {b.id}: {b.resolution_note}")
        return 0
    if args.cmd == "validate":
        b_opt = get_blocker(args.blocker_id, root_dir=args.root, state_dir=args.state_dir)
        if not b_opt:
            print(f"Blocker {args.blocker_id} not found", file=sys.stderr)
            return 1
        ok, msg = validate_blocker_resolution(b_opt, root_dir=args.root)
        print(f"{'OK' if ok else 'FAILED'}: {msg}")
        return 0 if ok else 1
    if args.cmd == "sync-md":
        out = sync_blockers_md(root_dir=args.root, state_dir=args.state_dir)
        print(f"Synced {out}")
        return 0
    if args.cmd == "loop":
        st = run_loop_status(root_dir=args.root, state_dir=args.state_dir)
        print(
            f"[LOOP] {st['done_tasks']}/{st['total_tasks']} tasks complete. Open blockers: {len(st['open_blockers'])}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
