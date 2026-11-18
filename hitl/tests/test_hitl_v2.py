from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from hitl.cli import main as hitl_main
from hitl.cli import parse_reply_command, run_loop_status
from hitl.models import BlockerRequired, BlockerStatus, BlockerType, NeededItem
from hitl.raise_blocker import (
    create_or_get_blocker,
    get_blocker,
    list_blockers,
    reject_blocker,
    resolve_blocker,
    resolve_state_dir,
)
from scripts.secret_scan import main as secret_scan_main
from scripts.secret_scan import scan_paths, scan_text


def test_clipper_state_v2_end_to_end_lifecycle(tmp_path: Path) -> None:
    (tmp_path / ".clipper-state").mkdir()

    b = create_or_get_blocker(
        btype=BlockerType.SECRET,
        task_id="T-5.1",
        step=2,
        title="Gemini API key needed for highlight scoring",
        needed=[
            NeededItem(
                key="GEMINI_API_KEY",
                where=".env",
                how_to_get="https://aistudio.google.com/app/apikey",
            )
        ],
        why="LLM semantic scoring",
        alternatives=["Reply 'USE OLLAMA' to proceed offline."],
        unblocked_meanwhile=["T-5.2", "T-6.1"],
        root_dir=tmp_path,
    )
    assert b.id == "BLK-0001"
    assert b.status == BlockerStatus.OPEN

    yaml_path = tmp_path / ".clipper-state" / "blockers" / "BLK-0001.yaml"
    assert yaml_path.exists()
    state_json = json.loads((tmp_path / ".clipper-state" / "state.json").read_text(encoding="utf-8"))
    assert state_json["blocked_tasks"]["T-5.1"] == "BLK-0001"

    blockers_md = (tmp_path / "BLOCKERS.md").read_text(encoding="utf-8")
    assert "`BLK-0001`" in blockers_md
    assert "`OPEN`" in blockers_md
    assert "Gemini API key needed for highlight scoring" in blockers_md

    # Idempotency check + raise_exc
    same = create_or_get_blocker(
        btype=BlockerType.SECRET,
        task_id="T-5.1",
        title="Gemini API key needed for highlight scoring",
        needed=[],
        why="Duplicate",
        root_dir=tmp_path,
    )
    assert same.id == "BLK-0001"
    assert len(list_blockers(tmp_path)) == 1

    with pytest.raises(BlockerRequired):
        create_or_get_blocker(
            btype=BlockerType.SECRET,
            task_id="T-5.1",
            title="Gemini API key needed for highlight scoring",
            needed=[],
            why="Duplicate",
            root_dir=tmp_path,
            raise_exc=True,
        )

    # Validation fails when key missing from .env
    rejected = resolve_blocker(
        b.id,
        note="Attempted before setting key",
        root_dir=tmp_path,
        validate=True,
    )
    assert rejected.status == BlockerStatus.REJECTED
    assert "GEMINI_API_KEY" in (rejected.resolution_note or "")
    assert "`REJECTED`" in (tmp_path / "BLOCKERS.md").read_text(encoding="utf-8")

    # Provide key in tmp_path/.env and resolve with validation
    (tmp_path / ".env").write_text("GEMINI_API_KEY=valid_local_test_value\n", encoding="utf-8")
    resolved = resolve_blocker(
        b.id,
        note="Added GEMINI_API_KEY to .env",
        root_dir=tmp_path,
        validate=True,
    )
    assert resolved.status == BlockerStatus.RESOLVED
    assert resolved.resolved_at is not None

    updated_state = json.loads(
        (tmp_path / ".clipper-state" / "state.json").read_text(encoding="utf-8")
    )
    assert "T-5.1" not in updated_state["blocked_tasks"]

    updated_md = (tmp_path / "BLOCKERS.md").read_text(encoding="utf-8")
    assert "`RESOLVED`" in updated_md
    assert "Added GEMINI_API_KEY to .env" in updated_md


def test_legacy_agent_dir_backward_compatibility(tmp_path: Path) -> None:
    (tmp_path / ".agent").mkdir()
    assert resolve_state_dir(tmp_path) == tmp_path / ".agent"

    b = create_or_get_blocker(
        btype=BlockerType.ASSET,
        task_id="T-0.4",
        title="Legacy blocker test",
        needed=[NeededItem(key="assets/fonts/test.ttf", where="assets/fonts/")],
        why="Verify .agent fallback",
        root_dir=tmp_path,
    )
    assert (tmp_path / ".agent" / "blockers" / f"{b.id}.yaml").exists()
    assert not (tmp_path / ".clipper-state").exists()

    # Explicit state_dir overrides auto-detection even when both exist
    (tmp_path / ".clipper-state").mkdir()
    assert resolve_state_dir(tmp_path) == tmp_path / ".clipper-state"
    assert resolve_state_dir(tmp_path, state_dir=".agent") == tmp_path / ".agent"
    assert get_blocker(b.id, root_dir=tmp_path, state_dir=".agent") is not None


def test_unquoted_yaml_timestamp_loads_cleanly(tmp_path: Path) -> None:
    bdir = tmp_path / ".clipper-state" / "blockers"
    bdir.mkdir(parents=True)
    # PyYAML parses unquoted ISO-8601 timestamps into datetime objects
    (bdir / "BLK-0001.yaml").write_text(
        "\n".join(
            [
                "id: BLK-0001",
                "type: ASSET",
                "status: OPEN",
                "task_id: T-0.4",
                "step: 2",
                'title: "Indic fonts needed"',
                "created_at: 2026-11-18T13:17:00Z",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    items = list_blockers(tmp_path)
    assert len(items) == 1
    assert items[0].created_at.startswith("2026-11-18T13:17:00")


def test_secret_leak_rejection_across_hitl_flows(tmp_path: Path) -> None:
    (tmp_path / ".clipper-state").mkdir()
    fake_gemini = "AIza" + "SyDummySecretLeakTest0123456789abcdef"
    fake_ghp = "ghp_" + "DummyGitHubPat0123456789abcdefghijklmnop"

    # 1. Creating a blocker containing a secret pattern is rejected
    with pytest.raises(ValueError, match="Secret leak detected"):
        create_or_get_blocker(
            btype=BlockerType.SECRET,
            task_id="T-5.1",
            title="Leaked key test",
            needed=[NeededItem(key="GEMINI_API_KEY", where=".env")],
            why=f"Do not put {fake_gemini} here",
            root_dir=tmp_path,
        )
    assert list_blockers(tmp_path) == []

    # 2. Resolving or rejecting with a secret in the note/reason is rejected
    b = create_or_get_blocker(
        btype=BlockerType.SECRET,
        task_id="T-5.1",
        title="Clean blocker",
        needed=[NeededItem(key="GEMINI_API_KEY", where=".env")],
        why="Need key in .env",
        root_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="Secret leak detected"):
        resolve_blocker(b.id, note=f"Here is my key {fake_ghp}", root_dir=tmp_path)

    with pytest.raises(ValueError, match="Secret leak detected"):
        reject_blocker(b.id, reason=f"Key {fake_gemini} invalid", root_dir=tmp_path)

    # 3. Inbound email reply parser rejects leaked secret
    parsed = parse_reply_command(f"Re: [{b.id}] [{b.correlation_token}]", fake_ghp)
    assert parsed["valid"] is False
    assert parsed["error"] == "SECRET_LEAK_DETECTED"


def test_credential_and_asset_validators_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state_dir = tmp_path / ".clipper-state"
    state_dir.mkdir()
    (state_dir / "tasks.yaml").write_text(
        yaml.safe_dump({"version": 2, "tasks": [{"id": "T-9.1", "status": "DONE"}]}),
        encoding="utf-8",
    )

    # Raise CREDENTIAL blocker via CLI
    rc = hitl_main(
        [
            "raise",
            "--root",
            str(tmp_path),
            "--state-dir",
            ".clipper-state",
            "--type",
            "CREDENTIAL",
            "--task",
            "T-9.1",
            "--key",
            "secrets/drive_sa.json",
            "--where",
            "secrets/drive_sa.json",
            "--title",
            "Drive service account JSON required",
        ]
    )
    assert rc == 0

    # Validate fails before file exists
    assert hitl_main(["validate", "--root", str(tmp_path), "BLK-0001"]) == 1

    # Invalid JSON fails validation
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "drive_sa.json").write_text("{not-json", encoding="utf-8")
    assert hitl_main(["validate", "--root", str(tmp_path), "BLK-0001"]) == 1

    # Valid JSON passes validation and resolves via CLI
    (tmp_path / "secrets" / "drive_sa.json").write_text('{"type": "service_account"}', encoding="utf-8")
    assert hitl_main(["validate", "--root", str(tmp_path), "BLK-0001"]) == 0
    assert hitl_main(["resolve", "--root", str(tmp_path), "BLK-0001", "--validate"]) == 0
    assert hitl_main(["status", "--root", str(tmp_path)]) == 0
    assert hitl_main(["sync-md", "--root", str(tmp_path)]) == 0
    assert hitl_main(["loop", "--root", str(tmp_path)]) == 0

    st = run_loop_status(root_dir=tmp_path)
    assert st["done_tasks"] == 1
    assert st["open_blockers"] == []
    out = capsys.readouterr().out
    assert "[LOOP] 1/1 tasks complete" in out


def test_secret_scanner_catches_seeded_keys_and_respects_allowlist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_gemini = "AIza" + "SySeededTestKey0123456789abcdefghijkl"
    fake_aws = "AKIA" + "IOSFODNN7EXAMPLE"
    fake_sk = "sk-" + "proj-AbCdEfGhIjKlMnOpQrStUvWxYz1234"

    leaky_file = tmp_path / "leaky.py"
    leaky_file.write_text(
        f'KEY1 = "{fake_gemini}"\nKEY2 = "{fake_aws}"\nKEY3 = "{fake_sk}"\n',
        encoding="utf-8",
    )

    findings = scan_paths([], root=tmp_path)
    assert len(findings) == 3
    assert {f.rule for f in findings} == {
        "Google / Gemini API key",
        "AWS Access Key ID",
        "OpenAI / Anthropic key",
    }
    # Ensure CLI returns non-zero and never prints the raw secret
    assert secret_scan_main(["--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert fake_gemini not in err
    assert fake_aws not in err
    assert fake_sk not in err

    # Inline allowlist marker and .env skipping
    clean_file = tmp_path / "clean.py"
    clean_file.write_text(f'ALLOWED = "{fake_gemini}"  # secret-scan: allow\n', encoding="utf-8")
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={fake_gemini}\n", encoding="utf-8")
    leaky_file.unlink()
    assert scan_text(clean_file.read_text(encoding="utf-8"), "clean.py") == []
    assert secret_scan_main(["--root", str(tmp_path)]) == 0
