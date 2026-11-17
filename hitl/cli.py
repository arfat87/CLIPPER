from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from hitl.models import BlockerType, NeededItem, detect_leaked_secret
from hitl.raise_blocker import (
    create_or_get_blocker,
    list_blockers,
    resolve_blocker,
)


def parse_reply_command(subject: str, body: str) -> dict:
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


def run_loop_status(root_dir: Path | None = None) -> dict:
    root = root_dir or Path.cwd()
    tasks_file = root / ".agent" / "tasks.yaml"
    tasks_data = yaml.safe_load(tasks_file.read_text(encoding="utf-8")) if tasks_file.exists() else {"tasks": []}
    tasks = tasks_data.get("tasks", [])
    blockers = list_blockers(root)
    open_blockers = [b for b in blockers if b.status.value == "OPEN"]
    done_tasks = [t for t in tasks if t.get("status") == "DONE"]
    return {
        "total_tasks": len(tasks),
        "done_tasks": len(done_tasks),
        "open_blockers": [b.model_dump(mode="json") for b in open_blockers],
        "all_blockers_count": len(blockers),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="hitl", description="CLIPPER HITL & Autonomous Loop CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show task graph and blocker status")

    p_raise = sub.add_parser("raise", help="Raise a new HITL blocker")
    p_raise.add_argument("--type", choices=[e.value for e in BlockerType], default="SECRET")
    p_raise.add_argument("--task", required=True)
    p_raise.add_argument("--key", required=True)
    p_raise.add_argument("--title", required=True)
    p_raise.add_argument("--why", default="Required by pipeline stage")

    p_res = sub.add_parser("resolve", help="Resolve a blocker by ID")
    p_res.add_argument("blocker_id")
    p_res.add_argument("--note", default="Resolved via CLI")

    sub.add_parser("loop", help="Run autonomous task scheduler check")

    args = parser.parse_args()
    if args.cmd == "status":
        st = run_loop_status()
        print(f"Tasks: {st['done_tasks']}/{st['total_tasks']} DONE | Open Blockers: {len(st['open_blockers'])}")
        for b in st["open_blockers"]:
            print(f"  - [{b['id']}] ({b['type']}) {b['title']} [token: {b['correlation_token']}]")
    elif args.cmd == "raise":
        b = create_or_get_blocker(
            btype=BlockerType(args.type),
            task_id=args.task,
            title=args.title,
            needed=[NeededItem(key=args.key, where=".env", how_to_get="Provide in .env")],
            why=args.why,
        )
        print(f"Created blocker {b.id} (token {b.correlation_token})")
    elif args.cmd == "resolve":
        b = resolve_blocker(args.blocker_id, note=args.note)
        print(f"Resolved {b.id} at {b.resolved_at}")
    elif args.cmd == "loop":
        st = run_loop_status()
        print(f"[LOOP] {st['done_tasks']}/{st['total_tasks']} tasks complete. Open blockers: {len(st['open_blockers'])}")


if __name__ == "__main__":
    main()
