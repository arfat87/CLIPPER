from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from hitl.models import (
    Blocker,
    BlockerRequired,
    BlockerStatus,
    BlockerType,
    NeededItem,
    assert_no_leaked_secrets,
    detect_leaked_secret,
)
from hitl.notifier import send_blocker_notification, write_blockers_md
from hitl.validators import validate_blocker, validate_blocker_resolution


def resolve_state_dir(
    root_dir: Path | None = None,
    state_dir: Path | str | None = None,
    *,
    create: bool = False,
) -> Path:
    """Resolve HITL state dir, preferring Spec v2 .clipper-state/ with .agent/ fallback."""
    root = root_dir or Path.cwd()
    if state_dir is not None:
        candidate = Path(state_dir)
        sdir = candidate if candidate.is_absolute() else (root / candidate)
    elif (root / ".clipper-state").exists():
        sdir = root / ".clipper-state"
    elif (root / ".agent").exists():
        sdir = root / ".agent"
    else:
        sdir = root / ".clipper-state"

    if create:
        (sdir / "blockers").mkdir(parents=True, exist_ok=True)
    return sdir


def _state_label(root: Path, sdir: Path) -> str:
    try:
        return f"{sdir.relative_to(root).as_posix()}/blockers/"
    except ValueError:
        return f"{sdir.name}/blockers/"


def list_blockers(
    root_dir: Path | None = None,
    *,
    state_dir: Path | str | None = None,
) -> list[Blocker]:
    bdir = resolve_state_dir(root_dir, state_dir, create=True) / "blockers"
    items: list[Blocker] = []
    for p in sorted(bdir.glob("BLK-*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if data:
            items.append(Blocker.model_validate(data))
    return items


def get_blocker(
    blocker_id: str,
    root_dir: Path | None = None,
    *,
    state_dir: Path | str | None = None,
) -> Blocker | None:
    p = resolve_state_dir(root_dir, state_dir, create=True) / "blockers" / f"{blocker_id}.yaml"
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not data:
        return None
    return Blocker.model_validate(data)


def save_blocker(
    blocker: Blocker,
    root_dir: Path | None = None,
    *,
    state_dir: Path | str | None = None,
) -> Path:
    assert_no_leaked_secrets(blocker)
    errors = validate_blocker(blocker)
    if errors:
        raise ValueError("; ".join(errors))
    p = resolve_state_dir(root_dir, state_dir, create=True) / "blockers" / f"{blocker.id}.yaml"
    p.write_text(
        yaml.safe_dump(blocker.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return p


def sync_blockers_md(
    root_dir: Path | None = None,
    *,
    state_dir: Path | str | None = None,
) -> Path:
    root = root_dir or Path.cwd()
    sdir = resolve_state_dir(root, state_dir, create=True)
    blockers = list_blockers(root, state_dir=sdir)
    return write_blockers_md(blockers, root_dir=root, state_label=_state_label(root, sdir))


def _next_blocker_id(
    root_dir: Path | None = None,
    *,
    state_dir: Path | str | None = None,
) -> str:
    existing = list_blockers(root_dir, state_dir=state_dir)
    max_num = 0
    for b in existing:
        m = re.search(r"BLK-(\d+)", b.id)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"BLK-{max_num + 1:04d}"


def _update_state_json_on_block(
    sdir: Path,
    task_id: str,
    step: int,
    blocker_id: str,
) -> None:
    state_path = sdir / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"current_task": task_id, "step": step, "blocked_tasks": {}}
    state.setdefault("blocked_tasks", {})[task_id] = blocker_id
    state["updated_at"] = datetime.now(UTC).isoformat()
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _update_state_json_on_unblock(sdir: Path, blocker_id: str) -> None:
    state_path = sdir / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        blocked = state.get("blocked_tasks", {})
        state["blocked_tasks"] = {k: v for k, v in blocked.items() if v != blocker_id}
        state["updated_at"] = datetime.now(UTC).isoformat()
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


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
    state_dir: Path | str | None = None,
    raise_exc: bool = False,
) -> Blocker:
    """Create a persistent blocker, update state.json, sync BLOCKERS.md, notify owner, and optionally raise BlockerRequired."""
    root = root_dir or Path.cwd()
    sdir = resolve_state_dir(root, state_dir, create=True)

    # Check if an open blocker with the same task_id and title already exists
    for b in list_blockers(root, state_dir=sdir):
        if b.task_id == task_id and b.title == title and b.status == BlockerStatus.OPEN:
            if raise_exc:
                raise BlockerRequired(b)
            return b

    bid = _next_blocker_id(root, state_dir=sdir)
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
    save_blocker(blocker, root, state_dir=sdir)
    _update_state_json_on_block(sdir, task_id, step, blocker.id)

    all_blockers = list_blockers(root, state_dir=sdir)
    send_blocker_notification(blocker, root_dir=root, state_dir=sdir, blockers=all_blockers)
    if raise_exc:
        raise BlockerRequired(blocker)
    return blocker


def resolve_blocker(
    blocker_id: str,
    note: str = "Resolved by owner",
    root_dir: Path | None = None,
    *,
    state_dir: Path | str | None = None,
    validate: bool = False,
    env_map: dict[str, str] | None = None,
) -> Blocker:
    if detect_leaked_secret(note):
        raise ValueError(
            "Secret leak detected in resolution note; place secrets in .env or secrets/ instead."
        )

    root = root_dir or Path.cwd()
    sdir = resolve_state_dir(root, state_dir, create=True)
    blocker = get_blocker(blocker_id, root, state_dir=sdir)
    if not blocker:
        raise ValueError(f"Blocker {blocker_id} not found")

    if validate:
        ok, reason = validate_blocker_resolution(blocker, root_dir=root, env_map=env_map)
        if not ok:
            blocker.status = BlockerStatus.REJECTED
            blocker.resolution_note = f"Validation failed: {reason}"
            save_blocker(blocker, root, state_dir=sdir)
            sync_blockers_md(root, state_dir=sdir)
            return blocker

    blocker.status = BlockerStatus.RESOLVED
    blocker.resolved_at = datetime.now(UTC).isoformat()
    blocker.resolution_note = note
    save_blocker(blocker, root, state_dir=sdir)
    _update_state_json_on_unblock(sdir, blocker_id)
    sync_blockers_md(root, state_dir=sdir)
    return blocker


def reject_blocker(
    blocker_id: str,
    reason: str,
    root_dir: Path | None = None,
    *,
    state_dir: Path | str | None = None,
) -> Blocker:
    if detect_leaked_secret(reason):
        raise ValueError("Secret leak detected in rejection reason.")
    root = root_dir or Path.cwd()
    sdir = resolve_state_dir(root, state_dir, create=True)
    blocker = get_blocker(blocker_id, root, state_dir=sdir)
    if not blocker:
        raise ValueError(f"Blocker {blocker_id} not found")
    blocker.status = BlockerStatus.REJECTED
    blocker.resolution_note = reason
    save_blocker(blocker, root, state_dir=sdir)
    sync_blockers_md(root, state_dir=sdir)
    return blocker
