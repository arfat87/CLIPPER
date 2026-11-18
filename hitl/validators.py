from __future__ import annotations

import json
import os
from pathlib import Path

from hitl.models import Blocker, BlockerType, detect_leaked_secret

_PLACEHOLDER_VALUES: frozenset[str] = frozenset({"", "changeme", "todo", "your_key_here"})


def parse_dotenv(dotenv_path: Path) -> dict[str, str]:
    """Parse key=value pairs from a .env file without mutating os.environ."""
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def validate_blocker(blocker: Blocker) -> list[str]:
    """Validate structural and security invariants of a Blocker."""
    errors: list[str] = []
    if not blocker.id.startswith("BLK-"):
        errors.append(f"Invalid blocker id '{blocker.id}'; expected 'BLK-XXXX'.")
    if not blocker.task_id.strip():
        errors.append("Blocker task_id must be non-empty.")
    if not blocker.title.strip():
        errors.append("Blocker title must be non-empty.")
    if blocker.step < 1:
        errors.append("Blocker step must be >= 1.")

    for field_name, value in (
        ("title", blocker.title),
        ("why", blocker.why),
        ("resolution_note", blocker.resolution_note or ""),
    ):
        if detect_leaked_secret(value):
            errors.append(f"Raw secret detected in blocker.{field_name}.")

    return errors


def validate_blocker_resolution(
    blocker: Blocker,
    root_dir: Path | None = None,
    env_map: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Validate that required artifacts/secrets exist before resolving a blocker."""
    root = root_dir or Path.cwd()
    merged_env: dict[str, str] = {}
    merged_env.update(parse_dotenv(root / ".env"))
    merged_env.update(os.environ)
    if env_map:
        merged_env.update(env_map)

    if blocker.type == BlockerType.SECRET:
        if not blocker.needed:
            return True, "No secret keys specified."
        missing = [
            item.key
            for item in blocker.needed
            if merged_env.get(item.key, "").strip().lower() in _PLACEHOLDER_VALUES
        ]
        if missing:
            return False, f"Missing or empty secret key(s) in .env / environment: {', '.join(missing)}"
        return True, "All required secret keys are present."

    if blocker.type == BlockerType.CREDENTIAL:
        for item in blocker.needed:
            candidate = _resolve_needed_path(root, item.where, item.key)
            if candidate is None or not candidate.exists() or candidate.stat().st_size == 0:
                return False, f"Required credential file missing or empty: {item.where or item.key}"
            if candidate.suffix.lower() == ".json":
                try:
                    json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    return False, f"Invalid JSON credential file {candidate.name}: {exc}"
        return True, "Credential files verified."

    if blocker.type == BlockerType.ASSET:
        for item in blocker.needed:
            candidate = _resolve_needed_path(root, item.where, item.key)
            if candidate is not None and candidate.exists():
                if candidate.is_dir() and not any(candidate.iterdir()):
                    return False, f"Required asset directory is empty: {candidate}"
                continue
            return False, f"Required asset missing: {item.key or item.where}"
        return True, "Required assets verified."

    return True, "Blocker resolution validated."


def _resolve_needed_path(root: Path, where: str, key: str) -> Path | None:
    for raw in (key, where):
        cleaned = raw.strip()
        if not cleaned or " " in cleaned or "*" in cleaned:
            continue
        p = Path(cleaned)
        candidate = p if p.is_absolute() else (root / p)
        if candidate.exists():
            return candidate
    # Fallback to first non-empty relative path even if missing
    for raw in (where, key):
        cleaned = raw.strip()
        if cleaned and " " not in cleaned and "*" not in cleaned:
            p = Path(cleaned)
            return p if p.is_absolute() else (root / p)
    return None
