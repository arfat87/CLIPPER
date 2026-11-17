from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from hitl.models import (
    Blocker,
    BlockerRequired,
    BlockerStatus,
    BlockerType,
    NeededItem,
)
from hitl.notifier import send_blocker_notification


def _agent_dir(root_dir: Path | None = None) -> Path:
    root = root_dir or Path.cwd()
    d = root / ".agent"
    (d / "blockers").mkdir(parents=True, exist_ok=True)
    return d


def list_blockers(root_dir: Path | None = None) -> list[Blocker]:
    bdir = _agent_dir(root_dir) / "blockers"
    items: list[Blocker] = []
    for p in sorted(bdir.glob("BLK-*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if data:
            items.append(Blocker.model_validate(data))
    return items


def get_blocker(blocker_id: str, root_dir: Path | None = None) -> Blocker | None:
    p = _agent_dir(root_dir) / "blockers" / f"{blocker_id}.yaml"
    if not p.exists():
        return None
    return Blocker.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


def save_blocker(blocker: Blocker, root_dir: Path | None = None) -> Path:
    p = _agent_dir(root_dir) / "blockers" / f"{blocker.id}.yaml"
    p.write_text(
        yaml.safe_dump(blocker.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return p


def _next_blocker_id(root_dir: Path | None = None) -> str:
    existing = list_blockers(root_dir)
    return f"BLK-{len(existing) + 1:04d}"


def create_or_get_blocker(
    *,
    btype: BlockerType,
    task_id: str,
    title: str,
    needed: list[NeededItem],
    why: str,
    step: int = 1,
    alternatives: list[str] | None = None,
    unblocked_meanwhile: list[str] | None = None,
    root_dir: Path | None = None,
    raise_exc: bool = False,
) -> Blocker:
    """Create a persistent blocker, update .agent/state.json, notify owner, and optionally raise BlockerRequired."""
    # Check if an open blocker with the same task_id and title already exists
    for b in list_blockers(root_dir):
        if b.task_id == task_id and b.title == title and b.status == BlockerStatus.OPEN:
            if raise_exc:
                raise BlockerRequired(b)
            return b

    bid = _next_blocker_id(root_dir)
    token_hash = hashlib.sha1(f"{bid}:{task_id}:{title}".encode()).hexdigest()[:6]
    blocker = Blocker(
        id=bid,
        type=btype,
        status=BlockerStatus.OPEN,
        task_id=task_id,
        step=step,
        title=title,
        needed=needed,
        why=why,
        alternatives=alternatives or [],
        unblocked_meanwhile=unblocked_meanwhile or [],
        correlation_token=f"CLP-{token_hash}",
    )
    save_blocker(blocker, root_dir)

    # Record in state.json
    state_path = _agent_dir(root_dir) / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"current_task": task_id, "step": step, "blocked_tasks": {}}
    state.setdefault("blocked_tasks", {})[task_id] = blocker.id
    state["updated_at"] = datetime.now(UTC).isoformat()
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    send_blocker_notification(blocker, root_dir)
    if raise_exc:
        raise BlockerRequired(blocker)
    return blocker


def resolve_blocker(
    blocker_id: str,
    note: str = "Resolved by owner",
    root_dir: Path | None = None,
) -> Blocker:
    blocker = get_blocker(blocker_id, root_dir)
    if not blocker:
        raise ValueError(f"Blocker {blocker_id} not found")
    blocker.status = BlockerStatus.RESOLVED
    blocker.resolved_at = datetime.now(UTC).isoformat()
    blocker.resolution_note = note
    save_blocker(blocker, root_dir)

    state_path = _agent_dir(root_dir) / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        blocked = state.get("blocked_tasks", {})
        blocked = {k: v for k, v in blocked.items() if v != blocker_id}
        state["blocked_tasks"] = blocked
        state["updated_at"] = datetime.now(UTC).isoformat()
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    return blocker
