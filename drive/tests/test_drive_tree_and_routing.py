from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]
import pytest

from drive.client import DriveRateLimitError, MockDriveClient
from drive.events import (
    EVENT_FILE_ERROR,
    EVENT_FILE_REMOVED,
    EVENT_FILE_STALE,
    EVENT_FILE_SYNCED,
    EVENT_SYNC_COMPLETED,
    DriveEventBus,
    DriveSyncEvent,
)
from drive.routing import parse_clipper_yaml, validate_manifest_invariants
from drive.security import load_drive_config
from drive.sync import DriveSync, validate_manifest
from drive.tree import main as tree_cli_main
from drive.tree import render_tree_md, write_tree_md

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "drive_manifest.schema.json"
EXAMPLE_MANIFEST_PATH = (
    REPO_ROOT / "assets" / "samples" / "fixtures" / "drive_manifest.example.json"
)


def _load_schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_render_tree_md_deterministic_from_example_fixture(tmp_path: Path) -> None:
    manifest = json.loads(EXAMPLE_MANIFEST_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=manifest, schema=_load_schema())

    rendered_1 = render_tree_md(manifest)
    rendered_2 = render_tree_md(manifest)
    assert rendered_1 == rendered_2

    # Shuffling manifest["files"] order must produce byte-for-byte identical TREE.md
    reversed_manifest = dict(manifest)
    reversed_manifest["files"] = list(reversed(manifest["files"]))
    assert render_tree_md(reversed_manifest) == rendered_1

    assert "# Drive workspace tree  (generated 2026-11-18 13:15 UTC, token 1042)" in rendered_1
    assert "Clipper/" in rendered_1
    assert "├── 01_Sources/" in rendered_1
    assert "[source]" in rendered_1
    assert "1 file" in rendered_1
    assert "10.0 MB" in rendered_1
    assert "└── 05_Docs/" in rendered_1
    assert "[doc]" in rendered_1
    assert "└── Brand_Guidelines" in rendered_1
    assert "SYNCED" in rendered_1
    assert "## Summary\nfiles 3 | folders 2 | errors 0 | unsorted 0 | last sync 2026-11-18" in rendered_1
    assert "## Attention\n- None." in rendered_1

    out_path = tmp_path / "TREE.md"
    write_tree_md(manifest, out_path)
    assert out_path.read_text(encoding="utf-8") == rendered_1

    cli_out = tmp_path / "CLI_TREE.md"
    rc = tree_cli_main(["--manifest", str(EXAMPLE_MANIFEST_PATH), "--output", str(cli_out)])
    assert rc == 0
    assert cli_out.read_text(encoding="utf-8") == rendered_1


def test_render_tree_md_all_statuses_nested_folders_and_attention(tmp_path: Path) -> None:
    manifest = {
        "schema_version": 1,
        "root_folder_id": "root_1",
        "generated_at": "2026-11-18T14:30:00Z",
        "start_page_token": "999",
        "stats": {"files": 5, "folders": 3, "bytes": 2048, "unsorted": 1, "errors": 1},
        "warnings": [
            "Invalid .clipper.yaml in '01_Sources': invalid route 'bad'; falling back to default routing."
        ],
        "files": [
            {
                "id": "f1",
                "name": "ep1.mp4",
                "mime": "video/mp4",
                "is_folder": False,
                "path": "01_Sources/ep1.mp4",
                "mirror_path": "drive_mirror/01_Sources/ep1.mp4",
                "size": 2048,
                "md5": "abc",
                "modified_time": "2026-11-18T14:00:00Z",
                "head_revision_id": "1",
                "route": "source",
                "status": "SYNCED",
                "error": None,
                "exported_as": None,
            },
            {
                "id": "f2",
                "name": "vlog.mov",
                "mime": "video/quicktime",
                "is_folder": False,
                "path": "01_Sources/vlog.mov",
                "mirror_path": "drive_mirror/01_Sources/vlog.mov",
                "size": 1024,
                "md5": "def",
                "modified_time": "2026-11-18T14:05:00Z",
                "head_revision_id": "2",
                "route": "source",
                "status": "STALE",
                "error": "rate_limit",
                "exported_as": None,
            },
            {
                "id": "f3",
                "name": "broken.mp4",
                "mime": "video/mp4",
                "is_folder": False,
                "path": "01_Sources/Sub/broken.mp4",
                "mirror_path": None,
                "size": 512,
                "md5": "ghi",
                "modified_time": "2026-11-18T14:10:00Z",
                "head_revision_id": "1",
                "route": "source",
                "status": "ERROR",
                "error": "md5_mismatch",
                "exported_as": None,
            },
            {
                "id": "f4",
                "name": "upload.part",
                "mime": "application/octet-stream",
                "is_folder": False,
                "path": "02_Templates/upload.part",
                "mirror_path": None,
                "size": 128,
                "md5": None,
                "modified_time": "2026-11-18T14:12:00Z",
                "head_revision_id": "1",
                "route": "template",
                "status": "SKIPPED",
                "error": None,
                "exported_as": None,
            },
            {
                "id": "f5",
                "name": "old.mp4",
                "mime": "video/mp4",
                "is_folder": False,
                "path": "02_Templates/old.mp4",
                "mirror_path": "drive_mirror/02_Templates/old.mp4",
                "size": 1024,
                "md5": "jkl",
                "modified_time": "2026-11-18T13:00:00Z",
                "head_revision_id": "1",
                "route": "template",
                "status": "REMOVED",
                "error": None,
                "exported_as": None,
            },
            {
                "id": "f6",
                "name": "notes_random.txt",
                "mime": "text/plain",
                "is_folder": False,
                "path": "notes_random.txt",
                "mirror_path": "drive_mirror/notes_random.txt",
                "size": 64,
                "md5": "mno",
                "modified_time": "2026-11-18T14:15:00Z",
                "head_revision_id": "1",
                "route": "unsorted",
                "status": "SYNCED",
                "error": None,
                "exported_as": None,
            },
        ],
    }

    tree_md = render_tree_md(manifest)
    for token in ("├──", "└──", "│", "SYNCED", "STALE", "ERROR", "SKIPPED", "REMOVED"):
        assert token in tree_md
    assert "!! not in a known folder" in tree_md
    assert "- notes_random.txt is unsorted:" in tree_md
    assert "- 01_Sources/vlog.mov is STALE:" in tree_md
    assert "- 01_Sources/Sub/broken.mp4 has ERROR: md5_mismatch." in tree_md
    assert "- Invalid .clipper.yaml in '01_Sources': invalid route 'bad'" in tree_md

    # Verify atomic write preserves previous TREE.md on write failure and leaves no temp files
    tree_path = tmp_path / "TREE.md"
    write_tree_md(manifest, tree_path)
    assert [p.name for p in tmp_path.iterdir()] == ["TREE.md"]


def test_clipper_yaml_parsing_security_and_safe_regex() -> None:
    valid = parse_clipper_yaml(
        "route: template\nignore:\n  - '.*draft.*'\n  - '^tmp_.*\\.mp4$'\n",
        folder_path="Custom",
    )
    assert valid.valid is True
    assert valid.warnings == []
    assert valid.override.route == "template"
    assert valid.override.matches_ignore("my_draft_v1.zip") is True
    assert valid.override.matches_ignore("tmp_clip.mp4") is True
    assert valid.override.matches_ignore("final_pack.zip") is False

    malicious_or_invalid_inputs: list[bytes | str] = [
        "route: [unclosed",
        "- not_a_mapping",
        "route: superuser",
        "route: 123",
        "route: true",
        "unknown_key: hello",
        "ignore: not_a_list",
        "ignore:\n  - 123",
        "ignore:\n  - ''",
        "ignore:\n  - '[unclosed_regex'",
        "ignore:\n  - '(a+)+'",
        "ignore:\n  - '(.*)*'",
        "!!python/object/apply:os.system ['echo pwned']",
        b"\xff\xfe\xfd",
        b"route: template\n" + (b"#" * 70000),
    ]
    for raw in malicious_or_invalid_inputs:
        res = parse_clipper_yaml(raw, folder_path="UntrustedFolder")
        assert res.valid is False
        assert res.override.route is None
        assert res.override.ignore_patterns == ()
        assert len(res.warnings) == 1
        assert "UntrustedFolder" in res.warnings[0]


def test_clipper_yaml_route_override_ignore_and_fallback_in_sync(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_custom", "CustomFolder", "root1")
    client.add_folder("f_sub", "SubBrand", "f_custom")
    client.add_folder("f_src", "01_Sources", "root1")

    client.add_file(
        "cfg_custom",
        ".clipper.yaml",
        "f_custom",
        b"route: template\nignore:\n  - '.*draft.*'\n",
        "text/yaml",
    )
    client.add_file(
        "cfg_sub",
        ".clipper.yaml",
        "f_sub",
        b"route: brandkit\n",
        "text/yaml",
    )
    client.add_file(
        "cfg_bad",
        ".clipper.yaml",
        "f_src",
        b"route: invalid_route\nignore:\n  - '[broken'\n",
        "text/yaml",
    )

    client.add_file("tpl1", "pack.cliptpl.zip", "f_custom", b"zip-bytes", "application/zip")
    client.add_file(
        "tpl_draft", "draft_pack.cliptpl.zip", "f_custom", b"draft-bytes", "application/zip"
    )
    client.add_file("logo1", "logo.png", "f_sub", b"png-bytes", "image/png")
    client.add_file("src1", "ep1.mp4", "f_src", b"mp4-bytes", "video/mp4")

    mirror_dir = tmp_path / "drive_mirror"
    tree_path = tmp_path / "TREE.md"
    now = datetime(2026, 11, 18, 15, 0, 0, tzinfo=UTC)

    syncer = DriveSync(
        client,
        "root1",
        mirror_dir=mirror_dir,
        root_dir=tmp_path,
        policy=load_drive_config(),
        tree_output_path=tree_path,
        now_fn=lambda: now,
    )
    res1 = syncer.sync(full=True)
    jsonschema.validate(instance=res1.manifest, schema=_load_schema())
    validate_manifest(res1.manifest)
    validate_manifest_invariants(res1.manifest)

    by_id = {f["id"]: f for f in res1.manifest["files"]}
    # .clipper.yaml files are control metadata, not mirrored assets
    assert "cfg_custom" not in by_id
    assert "cfg_sub" not in by_id
    assert "cfg_bad" not in by_id

    assert by_id["tpl1"]["route"] == "template"
    assert by_id["tpl1"]["status"] == "SYNCED"
    assert (mirror_dir / "CustomFolder" / "pack.cliptpl.zip").read_bytes() == b"zip-bytes"

    assert by_id["tpl_draft"]["route"] == "template"
    assert by_id["tpl_draft"]["status"] == "SKIPPED"
    assert by_id["tpl_draft"]["mirror_path"] is None
    assert not (mirror_dir / "CustomFolder" / "draft_pack.cliptpl.zip").exists()

    assert by_id["logo1"]["route"] == "brandkit"
    assert by_id["logo1"]["status"] == "SYNCED"

    # Invalid .clipper.yaml in 01_Sources warns and falls back to default routing ('source')
    assert by_id["src1"]["route"] == "source"
    assert by_id["src1"]["status"] == "SYNCED"
    assert any(".clipper.yaml" in w and "01_Sources" in w for w in res1.warnings)
    assert res1.tree_path == tree_path
    tree_text = tree_path.read_text(encoding="utf-8")
    assert "## Attention" in tree_text
    assert ".clipper.yaml" in tree_text

    # Offline regeneration from manifest.json matches byte-for-byte
    loaded_manifest = json.loads(res1.manifest_path.read_text(encoding="utf-8"))
    assert render_tree_md(loaded_manifest) == tree_text

    # Incremental update of CustomFolder/.clipper.yaml: change route to 'font' and remove ignore rule
    client.simulate_content_change("cfg_custom", b"route: font\nignore: []\n")
    res2 = syncer.sync()
    assert res2.mode == "incremental"
    by_id2 = {f["id"]: f for f in res2.manifest["files"]}
    assert by_id2["tpl1"]["route"] == "font"
    assert by_id2["tpl_draft"]["route"] == "font"
    assert by_id2["tpl_draft"]["status"] == "SYNCED"
    assert (mirror_dir / "CustomFolder" / "draft_pack.cliptpl.zip").read_bytes() == b"draft-bytes"
    # SubFolder has its own .clipper.yaml -> keeps brandkit
    assert by_id2["logo1"]["route"] == "brandkit"

    # Incremental removal of CustomFolder/.clipper.yaml -> falls back to default ('unsorted')
    client.simulate_removal("cfg_custom")
    res3 = syncer.sync()
    by_id3 = {f["id"]: f for f in res3.manifest["files"]}
    assert by_id3["tpl1"]["route"] == "unsorted"
    assert by_id3["logo1"]["route"] == "brandkit"


def test_manifest_invariants_rejects_duplicates_and_invalid_routes() -> None:
    with pytest.raises(ValueError, match="Duplicate manifest path"):
        validate_manifest_invariants(
            {
                "files": [
                    {"path": "01_Sources/a.mp4", "route": "source"},
                    {"path": "01_Sources/a.mp4", "route": "source"},
                ]
            }
        )

    with pytest.raises(ValueError, match="non-POSIX"):
        validate_manifest_invariants(
            {"files": [{"path": "01_Sources\\a.mp4", "route": "source"}]}
        )

    with pytest.raises(ValueError, match="invalid route"):
        validate_manifest_invariants(
            {"files": [{"path": "01_Sources/a.mp4", "route": "bogus"}]}
        )


def test_drive_sync_events_full_and_incremental_lifecycle(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_src", "01_Sources", "root1")
    client.add_folder("f_docs", "05_Docs", "root1")

    client.add_file("vid1", "ep1.mp4", "f_src", b"v1-bytes", "video/mp4")
    client.add_file(
        "doc1",
        "Guide",
        "f_docs",
        b"",
        "application/vnd.google-apps.document",
        is_native=True,
        head_revision_id="1",
    )
    client.set_export_bytes("doc1", "text/markdown", b"# Guide v1\n")

    client.add_file(
        "bad1",
        "corrupt.mp4",
        "f_src",
        b"expected",
        "video/mp4",
        md5_checksum=hashlib.md5(b"expected").hexdigest(),
    )
    client.set_download_outcomes("bad1", [b"bad", b"bad", b"bad"])

    bus = DriveEventBus()
    callback_events: list[DriveSyncEvent] = []
    mirror_dir = tmp_path / "drive_mirror"
    tree_path = tmp_path / "TREE.md"
    current_time = datetime(2026, 11, 18, 16, 0, 0, tzinfo=UTC)

    syncer = DriveSync(
        client,
        "root1",
        mirror_dir=mirror_dir,
        root_dir=tmp_path,
        tree_output_path=tree_path,
        event_bus=bus,
        on_event=callback_events.append,
        max_rate_retries=2,
        backoff_base_seconds=0.0,
        sleep_fn=lambda _: None,
        now_fn=lambda: current_time,
    )

    res1 = syncer.sync(full=True)
    assert res1.events == callback_events
    assert bus.events == res1.events

    by_fid_1 = {ev.file_id: ev for ev in res1.events if ev.file_id is not None}
    assert by_fid_1["vid1"].event_type == EVENT_FILE_SYNCED
    assert by_fid_1["vid1"].path == "01_Sources/ep1.mp4"
    assert by_fid_1["vid1"].route == "source"
    assert by_fid_1["vid1"].mirror_path == "drive_mirror/01_Sources/ep1.mp4"
    assert by_fid_1["vid1"].status == "SYNCED"
    assert by_fid_1["vid1"].md5 == hashlib.md5(b"v1-bytes").hexdigest()
    assert by_fid_1["vid1"].exported_as is None
    assert by_fid_1["vid1"].timestamp == "2026-11-18T16:00:00Z"

    assert by_fid_1["doc1"].event_type == EVENT_FILE_SYNCED
    assert by_fid_1["doc1"].exported_as == "text/markdown"
    assert by_fid_1["doc1"].route == "doc"

    assert by_fid_1["bad1"].event_type == EVENT_FILE_ERROR
    assert by_fid_1["bad1"].status == "ERROR"
    assert "md5_mismatch" in str(by_fid_1["bad1"].error)

    assert res1.events[-1].event_type == EVENT_SYNC_COMPLETED
    assert res1.events[-1].mode == "full"

    # Verify serialization roundtrip
    for ev in res1.events:
        roundtrip = DriveSyncEvent.from_dict(json.loads(ev.to_json()))
        assert roundtrip == ev

    # Incremental sync: update vid1, rate-limit doc1 -> STALE, remove bad1 -> REMOVED
    bus.clear()
    callback_events.clear()
    current_time = current_time + timedelta(minutes=5)

    client.simulate_content_change("vid1", b"v2-updated-bytes", head_revision_id="2")
    client.simulate_content_change("doc1", b"", head_revision_id="2")
    client.set_export_outcomes(
        "doc1",
        "text/markdown",
        [DriveRateLimitError("429"), DriveRateLimitError("429")],
    )
    client.simulate_removal("bad1")

    res2 = syncer.sync()
    assert res2.mode == "incremental"
    assert bus.events == res2.events
    assert callback_events == res2.events

    by_fid_2 = {ev.file_id: ev for ev in res2.events if ev.file_id is not None}
    assert by_fid_2["vid1"].event_type == EVENT_FILE_SYNCED
    assert by_fid_2["vid1"].md5 == hashlib.md5(b"v2-updated-bytes").hexdigest()

    assert by_fid_2["doc1"].event_type == EVENT_FILE_STALE
    assert by_fid_2["doc1"].status == "STALE"
    assert by_fid_2["doc1"].mirror_path == "drive_mirror/05_Docs/Guide.md"
    assert (mirror_dir / "05_Docs" / "Guide.md").read_bytes() == b"# Guide v1\n"

    assert by_fid_2["bad1"].event_type == EVENT_FILE_REMOVED
    assert by_fid_2["bad1"].status == "REMOVED"

    assert res2.events[-1].event_type == EVENT_SYNC_COMPLETED
    assert res2.events[-1].mode == "incremental"

    # TREE.md regenerated on incremental sync and matches offline regeneration
    assert render_tree_md(json.loads(res2.manifest_path.read_text(encoding="utf-8"))) == (
        tree_path.read_text(encoding="utf-8")
    )
    assert "STALE" in tree_path.read_text(encoding="utf-8")
