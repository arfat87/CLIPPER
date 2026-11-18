from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jsonschema  # type: ignore[import-untyped]
import pytest

from drive.client import (
    DRIVE_API_BASE,
    DRIVE_READONLY_SCOPE,
    DriveClient,
    DriveExportTooLargeError,
    DriveRateLimitError,
    MockDriveClient,
    ServiceAccountDriveClient,
)
from drive.security import (
    DrivePolicyConfig,
    assert_safe_mirror_path,
    load_drive_config,
    sanitize_mirror_path,
    sanitize_segment,
)
from drive.sync import DriveSync, sync_drive, validate_manifest
from hitl.models import BlockerRequired, BlockerType

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVE_PKG_DIR = REPO_ROOT / "drive"
SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "drive_manifest.schema.json"


def _load_schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_path_traversal_and_sanitization_security(tmp_path: Path) -> None:
    # Path traversal name like "../../etc/passwd"
    seg = sanitize_segment("../../etc/passwd", "file12345678")
    assert ".." not in seg
    assert "/" not in seg
    assert "\\" not in seg
    assert seg == "etc_passwd"

    # Windows reserved names and control characters
    assert sanitize_segment("CON.txt") == "_CON.txt"
    assert sanitize_segment("nul") == "_nul"
    assert sanitize_segment("my\x00clip\n\t.mp4") == "myclip.mp4"
    assert sanitize_segment("..") == "unnamed"

    # Collision flattening with ~<id8>
    used: set[str] = set()
    p1 = sanitize_mirror_path(["01_Sources", "clip.mp4"], "aaaa1111bbbb", used_paths=used)
    p2 = sanitize_mirror_path(["01_Sources", "clip.mp4"], "cccc2222dddd", used_paths=used)
    assert p1 == "01_Sources/clip.mp4"
    assert p2 == "01_Sources/clip~cccc2222.mp4"

    # Length capping preserves uniqueness
    long_name = "a" * 200 + ".mp4"
    p_long = sanitize_mirror_path(
        ["01_Sources", long_name],
        "zzzz99990000",
        max_segment_len=60,
        max_path_len=80,
    )
    assert len(p_long) <= 80
    assert p_long.endswith(".mp4")

    # assert_safe_mirror_path rejects traversal and accepts safe paths
    mirror_root = tmp_path / "drive_mirror"
    mirror_root.mkdir()
    safe = assert_safe_mirror_path(mirror_root, "01_Sources/clip.mp4")
    assert safe == mirror_root.resolve() / "01_Sources" / "clip.mp4"

    for bad in ("../escape.txt", "01_Sources/../../etc/passwd", "/abs/path", "a\\b"):
        with pytest.raises(ValueError):
            assert_safe_mirror_path(mirror_root, bad)


def test_full_crawl_and_incremental_move_rename_delete(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_src", "01_Sources", "root1")
    client.add_folder("f_tpl", "02_Templates", "root1")
    client.add_folder("f_ref", "06_Reference", "root1")

    video_bytes = b"fake-mp4-binary-payload-001"
    notes_bytes = b"random unsorted note"
    tmp_bytes = b"partial upload"
    client.add_file("vid1", "podcast_ep1.mp4", "f_src", video_bytes, "video/mp4")
    client.add_file("note1", "scratch.txt", "root1", notes_bytes, "text/plain")
    client.add_file("tmp1", "upload.part", "f_src", tmp_bytes, "application/octet-stream")

    mirror_dir = tmp_path / "drive_mirror"
    current_time = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)

    syncer = DriveSync(
        client,
        "root1",
        mirror_dir=mirror_dir,
        root_dir=tmp_path,
        policy=load_drive_config(),
        sleep_fn=lambda _: None,
        now_fn=lambda: current_time,
    )

    res1 = syncer.sync(full=True)
    assert res1.mode == "full"
    assert res1.manifest_path.is_file()
    jsonschema.validate(instance=res1.manifest, schema=_load_schema())
    validate_manifest(res1.manifest)

    by_id = {entry["id"]: entry for entry in res1.manifest["files"]}
    assert by_id["vid1"]["route"] == "source"
    assert by_id["vid1"]["status"] == "SYNCED"
    assert by_id["vid1"]["mirror_path"] == "drive_mirror/01_Sources/podcast_ep1.mp4"
    assert (mirror_dir / "01_Sources" / "podcast_ep1.mp4").read_bytes() == video_bytes

    assert by_id["note1"]["route"] == "unsorted"
    assert by_id["note1"]["status"] == "SYNCED"
    assert res1.manifest["stats"]["unsorted"] == 1
    assert res1.manifest["stats"]["folders"] == 3

    assert by_id["tmp1"]["status"] == "SKIPPED"
    assert by_id["tmp1"]["mirror_path"] is None

    # Incremental 1: rename vid1 and move note1 into 06_Reference (no re-download when unchanged)
    downloads_before = len(client.download_calls)
    client.simulate_rename("vid1", "podcast_ep1_renamed.mp4")
    client.simulate_move("note1", "f_ref", new_name="ref_transcript.txt")

    res2 = syncer.sync()
    assert res2.mode == "incremental"
    assert len(client.download_calls) == downloads_before
    jsonschema.validate(instance=res2.manifest, schema=_load_schema())

    by_id2 = {entry["id"]: entry for entry in res2.manifest["files"]}
    assert by_id2["vid1"]["path"] == "01_Sources/podcast_ep1_renamed.mp4"
    assert not (mirror_dir / "01_Sources" / "podcast_ep1.mp4").exists()
    assert (mirror_dir / "01_Sources" / "podcast_ep1_renamed.mp4").read_bytes() == video_bytes

    assert by_id2["note1"]["route"] == "reference"
    assert by_id2["note1"]["path"] == "06_Reference/ref_transcript.txt"
    assert not (mirror_dir / "scratch.txt").exists()
    assert (mirror_dir / "06_Reference" / "ref_transcript.txt").read_bytes() == notes_bytes

    # Incremental 2: remove vid1 (kept in mirror for 7 days, then purged)
    client.simulate_removal("vid1")
    res3 = syncer.sync()
    by_id3 = {entry["id"]: entry for entry in res3.manifest["files"]}
    assert by_id3["vid1"]["status"] == "REMOVED"
    assert (mirror_dir / "01_Sources" / "podcast_ep1_renamed.mp4").exists()

    # Advance clock past 7-day retention window and sync again
    current_time = current_time + timedelta(days=8)
    res4 = syncer.sync()
    by_id4 = {entry["id"]: entry for entry in res4.manifest["files"]}
    assert "vid1" not in by_id4
    assert not (mirror_dir / "01_Sources" / "podcast_ep1_renamed.mp4").exists()


def test_incremental_scope_filter_ignores_outside_root(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_src", "01_Sources", "root1")
    client.add_folder("outside_folder", "PrivateStuff", "unrelated_root")
    client.add_file("inside1", "clip.mp4", "f_src", b"inside", "video/mp4")
    client.add_file("outside1", "secret.mp4", "outside_folder", b"outside", "video/mp4")

    mirror_dir = tmp_path / "drive_mirror"
    syncer = DriveSync(client, "root1", mirror_dir=mirror_dir, root_dir=tmp_path)
    res1 = syncer.sync(full=True)
    assert [f["id"] for f in res1.manifest["files"]] == ["inside1"]

    # Change on outside1 is ignored; moving inside1 outside root marks it REMOVED
    client.simulate_rename("outside1", "secret_renamed.mp4")
    client.simulate_move("inside1", "outside_folder")
    res2 = syncer.sync()
    by_id2 = {f["id"]: f for f in res2.manifest["files"]}
    assert "outside1" not in by_id2
    assert by_id2["inside1"]["status"] == "REMOVED"


def test_native_doc_export_and_too_large_error_handling(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_docs", "05_Docs", "root1")
    client.add_file(
        "doc1",
        "Brand_Guidelines",
        "f_docs",
        b"",
        "application/vnd.google-apps.document",
        is_native=True,
    )
    client.set_export_bytes("doc1", "text/markdown", b"# Brand Guidelines\nUse Noto fonts.\n")

    client.add_file(
        "sheet1",
        "Metrics",
        "f_docs",
        b"",
        "application/vnd.google-apps.spreadsheet",
        is_native=True,
    )
    client.set_export_bytes("sheet1", "text/csv", b"col1,col2\n1,2\n")

    client.add_file(
        "slides1",
        "Big_Deck",
        "f_docs",
        b"",
        "application/vnd.google-apps.presentation",
        is_native=True,
    )
    # Exceed 1 MB custom cap
    client.set_export_bytes("slides1", "application/pdf", b"x" * (2 * 1024 * 1024))

    client.add_file(
        "slides2",
        "Api_Cap_Deck",
        "f_docs",
        b"",
        "application/vnd.google-apps.presentation",
        is_native=True,
    )
    client.set_export_outcomes(
        "slides2",
        "application/pdf",
        [DriveExportTooLargeError("exportSizeLimitExceeded")],
    )

    policy = DrivePolicyConfig(max_export_mb=1)
    mirror_dir = tmp_path / "drive_mirror"
    res = sync_drive(client, "root1", mirror_dir=mirror_dir, root_dir=tmp_path, policy=policy)
    jsonschema.validate(instance=res.manifest, schema=_load_schema())

    by_id = {entry["id"]: entry for entry in res.manifest["files"]}
    assert by_id["doc1"]["status"] == "SYNCED"
    assert by_id["doc1"]["exported_as"] == "text/markdown"
    assert by_id["doc1"]["md5"] is None
    assert (mirror_dir / "05_Docs" / "Brand_Guidelines.md").read_text(encoding="utf-8").startswith(
        "# Brand Guidelines"
    )

    assert by_id["sheet1"]["status"] == "SYNCED"
    assert by_id["sheet1"]["exported_as"] == "text/csv"
    assert (mirror_dir / "05_Docs" / "Metrics.csv").read_text(encoding="utf-8") == "col1,col2\n1,2\n"

    assert by_id["slides1"]["status"] == "ERROR"
    assert "too_large" in str(by_id["slides1"]["error"])
    assert by_id["slides1"]["mirror_path"] is None
    assert not (mirror_dir / "05_Docs" / "Big_Deck.pdf").exists()

    assert by_id["slides2"]["status"] == "ERROR"
    assert by_id["slides2"]["error"] == "too_large"


def test_md5_mismatch_retry_and_error_and_rate_limit_backoff(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_src", "01_Sources", "root1")

    good_bytes = b"valid-video-content"
    good_md5 = hashlib.md5(good_bytes).hexdigest()
    client.add_file(
        "vid_retry",
        "retry_clip.mp4",
        "f_src",
        good_bytes,
        "video/mp4",
        md5_checksum=good_md5,
    )
    # 1st attempt: rate-limit error; 2nd attempt: corrupted bytes; 3rd attempt: valid bytes
    client.set_download_outcomes(
        "vid_retry",
        [
            DriveRateLimitError("429"),
            b"corrupted-bytes",
            good_bytes,
        ],
    )

    bad_bytes = b"always-corrupted"
    client.add_file(
        "vid_fail",
        "bad_clip.mp4",
        "f_src",
        b"expected-content",
        "video/mp4",
        md5_checksum=hashlib.md5(b"expected-content").hexdigest(),
    )
    client.set_download_outcomes("vid_fail", [bad_bytes, bad_bytes, bad_bytes])

    sleeps: list[float] = []
    mirror_dir = tmp_path / "drive_mirror"
    syncer = DriveSync(
        client,
        "root1",
        mirror_dir=mirror_dir,
        root_dir=tmp_path,
        max_md5_retries=3,
        max_rate_retries=4,
        backoff_base_seconds=0.1,
        sleep_fn=sleeps.append,
    )
    res = syncer.sync(full=True)
    jsonschema.validate(instance=res.manifest, schema=_load_schema())

    by_id = {entry["id"]: entry for entry in res.manifest["files"]}
    assert by_id["vid_retry"]["status"] == "SYNCED"
    assert by_id["vid_retry"]["md5"] == good_md5
    assert (mirror_dir / "01_Sources" / "retry_clip.mp4").read_bytes() == good_bytes
    assert len(sleeps) == 1

    assert by_id["vid_fail"]["status"] == "ERROR"
    assert "md5_mismatch" in str(by_id["vid_fail"]["error"])
    assert by_id["vid_fail"]["mirror_path"] is None
    assert not (mirror_dir / "01_Sources" / "bad_clip.mp4").exists()


def test_shortcut_inside_and_outside_root(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_src", "01_Sources", "root1")
    client.add_folder("f_tpl", "02_Templates", "root1")
    client.add_folder("outside", "OtherDrive", "other_root")

    client.add_file("target_in", "master.mp4", "f_src", b"master-video", "video/mp4")
    client.add_file("target_out", "external.mp4", "outside", b"secret-ext", "video/mp4")

    client.add_shortcut("sc_in", "alias_master.mp4", "f_tpl", "target_in", "video/mp4")
    client.add_shortcut("sc_out", "alias_external.mp4", "f_src", "target_out", "video/mp4")

    mirror_dir = tmp_path / "drive_mirror"
    with caplog.at_level("WARNING"):
        res = sync_drive(client, "root1", mirror_dir=mirror_dir, root_dir=tmp_path)

    jsonschema.validate(instance=res.manifest, schema=_load_schema())
    by_id = {entry["id"]: entry for entry in res.manifest["files"]}

    assert "sc_in" in by_id
    assert by_id["sc_in"]["status"] == "SYNCED"
    assert (mirror_dir / "02_Templates" / "alias_master.mp4").read_bytes() == b"master-video"

    assert "sc_out" not in by_id
    assert any("outside root" in w.lower() for w in res.warnings)
    assert any("outside root" in rec.message.lower() for rec in caplog.records)


def test_path_traversal_file_in_sync_manifest_stays_inside_mirror(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_src", "01_Sources", "root1")
    client.add_file("evil1", "../../etc/passwd", "f_src", b"nope", "text/plain")

    mirror_dir = tmp_path / "drive_mirror"
    res = sync_drive(client, "root1", mirror_dir=mirror_dir, root_dir=tmp_path)
    jsonschema.validate(instance=res.manifest, schema=_load_schema())

    entry = res.manifest["files"][0]
    assert ".." not in entry["path"]
    assert ".." not in str(entry["mirror_path"])
    assert (mirror_dir / "01_Sources" / "etc_passwd").read_bytes() == b"nope"


def test_atomic_manifest_preserves_previous_on_failure(tmp_path: Path) -> None:
    client = MockDriveClient(root_folder_id="root1")
    client.add_folder("f_src", "01_Sources", "root1")
    client.add_file("vid1", "clip.mp4", "f_src", b"v1", "video/mp4")

    mirror_dir = tmp_path / "drive_mirror"
    res1 = sync_drive(client, "root1", mirror_dir=mirror_dir, root_dir=tmp_path)
    previous_manifest = res1.manifest_path.read_text(encoding="utf-8")

    # Simulate transient failure during subsequent full crawl
    client.set_list_outcomes([DriveRateLimitError("503"), DriveRateLimitError("503")])
    syncer = DriveSync(
        client,
        "root1",
        mirror_dir=mirror_dir,
        root_dir=tmp_path,
        max_rate_retries=2,
        sleep_fn=lambda _: None,
    )
    with pytest.raises(DriveRateLimitError):
        syncer.sync(full=True)

    assert res1.manifest_path.read_text(encoding="utf-8") == previous_manifest


def test_service_account_client_missing_or_invalid_raises_blocker_and_readonly_http(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".clipper-state"
    sa_path = tmp_path / "secrets" / "drive_sa.json"

    # Missing file -> BlockerRequired(CREDENTIAL)
    with pytest.raises(BlockerRequired) as exc_info:
        ServiceAccountDriveClient(sa_path, root_dir=tmp_path, state_dir=state_dir)
    assert exc_info.value.blocker.type == BlockerType.CREDENTIAL
    assert (state_dir / "blockers" / f"{exc_info.value.blocker.id}.yaml").exists()

    # Invalid JSON -> BlockerRequired(CREDENTIAL)
    sa_path.parent.mkdir(parents=True, exist_ok=True)
    sa_path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(BlockerRequired):
        ServiceAccountDriveClient(sa_path, root_dir=tmp_path, state_dir=state_dir)

    # Valid SA JSON + httpx MockTransport verifies strict GET-only Drive API calls
    sa_path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "clipper@example.iam.gserviceaccount.com",
                "private_key": "dummy-pem-for-mock-transport",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )

    seen_requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append((request.method, str(request.url)))
        assert request.method == "GET"
        assert request.headers.get("Authorization") == "Bearer mock-access-token"
        url_str = str(request.url)
        if url_str.startswith(f"{DRIVE_API_BASE}/files/f_doc/export"):
            return httpx.Response(200, content=b"# Hello Drive\n")
        if url_str.startswith(f"{DRIVE_API_BASE}/files/f_bin"):
            return httpx.Response(200, content=b"binary-data")
        if url_str.startswith(f"{DRIVE_API_BASE}/files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "f_bin",
                            "name": "sample.mp4",
                            "mimeType": "video/mp4",
                            "size": "11",
                            "md5Checksum": hashlib.md5(b"binary-data").hexdigest(),
                            "modifiedTime": "2026-11-18T10:00:00Z",
                            "parents": ["root1"],
                        }
                    ]
                },
            )
        if url_str.startswith(f"{DRIVE_API_BASE}/changes/startPageToken"):
            return httpx.Response(200, json={"startPageToken": "77"})
        if url_str.startswith(f"{DRIVE_API_BASE}/changes"):
            return httpx.Response(200, json={"changes": [], "newStartPageToken": "78"})
        return httpx.Response(404, json={"error": {"message": "not found"}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ServiceAccountDriveClient(
        sa_path,
        root_dir=tmp_path,
        state_dir=state_dir,
        http_client=http,
        token_provider=lambda: "mock-access-token",
    )
    assert isinstance(client, DriveClient)
    assert DRIVE_READONLY_SCOPE == "https://www.googleapis.com/auth/drive.readonly"
    assert client.get_start_page_token() == "77"
    listed = client.list_files("root1")
    assert len(listed.files) == 1
    assert listed.files[0].id == "f_bin"
    dl_dest = tmp_path / "dl.bin"
    assert client.download_file("f_bin", dl_dest) == 11
    assert dl_dest.read_bytes() == b"binary-data"
    exp_dest = tmp_path / "exp.md"
    assert client.export_file("f_doc", "text/markdown", exp_dest) == 14
    assert exp_dest.read_bytes() == b"# Hello Drive\n"
    changes = client.list_changes("77")
    assert changes.new_start_page_token == "78"
    assert all(method == "GET" for method, _ in seen_requests)


def test_read_only_guard_ast_and_regex_scan() -> None:
    """Assert ZERO mutating Drive API calls exist anywhere in drive/ source files."""
    forbidden_methods = {
        "create",
        "update",
        "delete",
        "copy",
        "permissions",
        "insert",
        "patch",
        "remove",
        "watch",
        "emptyTrash",
        "put",
    }
    forbidden_regex = re.compile(
        r"\.\s*(?:create|update|delete|copy|permissions|insert|patch|remove|watch|emptyTrash)\s*\("
    )
    drive_sources = sorted(
        p for p in DRIVE_PKG_DIR.glob("*.py") if p.is_file()
    )
    assert len(drive_sources) >= 4

    for src_path in drive_sources:
        text = src_path.read_text(encoding="utf-8")
        # Regex check across source text
        match = forbidden_regex.search(text)
        assert match is None, f"Forbidden mutating call pattern {match.group(0)!r} in {src_path}"

        # AST check across all Call nodes
        tree = ast.parse(text, filename=str(src_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                assert attr not in forbidden_methods, (
                    f"Forbidden mutating method call '.{attr}()' in {src_path}:{node.lineno}"
                )
                # Ensure no HTTP POST/PUT/PATCH/DELETE against Drive API URLs
                if attr in {"post", "put", "patch", "delete", "request"}:
                    call_src = ast.get_source_segment(text, node) or ""
                    assert "DRIVE_API_BASE" not in call_src and "googleapis.com/drive" not in call_src, (
                        f"Non-GET HTTP call to Drive API in {src_path}:{node.lineno}"
                    )

        # Verify DriveClient protocol itself only exposes read-only methods
        if src_path.name == "client.py":
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == "DriveClient":
                    proto_methods = {
                        item.name
                        for item in node.body
                        if isinstance(item, ast.FunctionDef)
                    }
                    assert proto_methods == {
                        "list_files",
                        "get_start_page_token",
                        "list_changes",
                        "download_file",
                        "export_file",
                    }
