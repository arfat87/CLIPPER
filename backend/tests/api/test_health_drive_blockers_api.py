from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from backend.app.config import get_settings
from backend.app.db import init_schema, reset_engine
from backend.app.main import app
from backend.app.services.admin import hash_admin_password, reset_login_rate_limits
from hitl.models import BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker


@pytest.fixture
def hdb_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = tmp_path / "assets"
    (assets_dir / "templates").mkdir(parents=True, exist_ok=True)
    mirror_dir = tmp_path / "drive_mirror"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test_hdb.db"
    db_url = f"sqlite:///{db_path}"

    pw = "owner-health-secret-pass"
    pw_hash = hash_admin_password(pw)

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("ASSETS_DIR", str(assets_dir))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("ADMIN_ENABLED", "true")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", pw_hash)
    monkeypatch.setenv("DRIVE_MIRROR_DIR", str(mirror_dir))
    monkeypatch.setenv("CLIPPER_STATE_ROOT", str(tmp_path))

    get_settings.cache_clear()
    reset_engine()
    reset_login_rate_limits()

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_schema(engine)
    client = TestClient(app)

    yield client, engine, tmp_path, mirror_dir, pw

    reset_login_rate_limits()
    engine.dispose()
    reset_engine()
    get_settings.cache_clear()


def _login_admin(client: TestClient, password: str) -> None:
    resp = client.post("/api/v1/admin/auth/login", json={"password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["authenticated"] is True


def test_health_endpoint_spec_v2_and_backward_compatibility(hdb_env):
    client, _engine, tmp_path, _mirror_dir, _pw = hdb_env

    # Create an open blocker in .clipper-state/blockers to verify open_blockers & subsystems.blockers
    create_or_get_blocker(
        btype=BlockerType.ENVIRONMENT,
        task_id="T-0.4",
        title="Test Health Blocker",
        needed=[NeededItem(key="FFMPEG_CHECK", where="system", how_to_get="Install ffmpeg")],
        why="Health test",
        root_dir=tmp_path,
        state_dir=tmp_path / ".clipper-state",
    )

    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()

    # Legacy backward-compatible fields
    assert body["ok"] is True
    assert body["app"] == "CLIPPER"
    assert body["status"] == "ok"
    assert body["version"] == "2.0.0"
    assert isinstance(body["cookies_configured"], dict)
    assert isinstance(body["open_blockers"], int)
    assert body["open_blockers"] >= 1

    # Spec v2 subsystems object
    sub = body["subsystems"]
    assert set(sub.keys()) >= {
        "ffmpeg",
        "gpu",
        "models",
        "drive",
        "queue",
        "templates",
        "blockers",
    }
    assert sub["ffmpeg"]["libass"] is True
    assert isinstance(sub["ffmpeg"]["version"], str)
    assert "cuda" in sub["gpu"]
    assert isinstance(sub["models"]["asr"], list)
    assert "provider" in sub["models"]["llm"]
    assert "status" in sub["drive"]
    assert isinstance(sub["queue"]["depth"], int)
    assert sub["templates"]["valid"] >= 6
    assert sub["blockers"]["open"] >= 1

    # Top-level OpenAPI HealthResponse fields
    assert "ffmpeg" in body
    assert "gpu" in body
    assert "models" in body
    assert "disk" in body and body["disk"]["free_gb"] >= 0
    assert "ytdlp" in body
    assert "queue" in body
    assert "drive" in body
    assert "templates" in body


def test_drive_status_tree_sync_files_and_pick(hdb_env, monkeypatch: pytest.MonkeyPatch):
    client, _engine, tmp_path, mirror_dir, pw = hdb_env

    # 1. Initial status & fallback empty tree before sync
    st0 = client.get("/api/v1/drive/status")
    assert st0.status_code == 200
    d0 = st0.json()
    assert "configured" in d0
    assert "folder_id" in d0
    assert "mirror_dir" in d0
    assert "file_count" in d0
    assert d0["status"] in ("DISABLED", "CONNECTED")

    tr0 = client.get("/api/v1/drive/tree")
    assert tr0.status_code == 200
    assert "tree_md" in tr0.json()
    assert "manifest" in tr0.json()
    assert (tmp_path / "data" / "drive_tree.json").exists()

    # 2. Seed a mirrored Drive workspace manifest + local mirrored files
    src_video = mirror_dir / "04_sources" / "podcast_ep1.mp4"
    src_video.parent.mkdir(parents=True, exist_ok=True)
    src_video.write_bytes(b"\x00\x00\x00\x18ftypmp42fakevideo")

    tpl_file = mirror_dir / "01_templates" / "neon_pop.json"
    tpl_file.parent.mkdir(parents=True, exist_ok=True)
    tpl_file.write_text(json.dumps({"id": "neon_pop"}), encoding="utf-8")

    font_file = mirror_dir / "02_fonts" / "HindSiliguri-Bold.ttf"
    font_file.parent.mkdir(parents=True, exist_ok=True)
    font_file.write_bytes(b"\x00\x01\x00\x00")

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "root_folder_id": "16EnG42pTWexYiJYao3sSM5XLhqmaeNB-",
        "root_name": "CLIPPER",
        "generated_at": "2026-11-18T19:00:00Z",
        "start_page_token": "42",
        "stats": {"files": 3, "folders": 3, "bytes": 1024, "unsorted": 0, "errors": 0},
        "files": [
            {
                "id": "drv_src_01",
                "name": "podcast_ep1.mp4",
                "mime": "video/mp4",
                "path": "04_sources/podcast_ep1.mp4",
                "mirror_path": "04_sources/podcast_ep1.mp4",
                "size": 25,
                "md5": "abc123",
                "modified_time": "2026-11-18T18:55:00Z",
                "route": "source",
                "status": "SYNCED",
            },
            {
                "id": "drv_tpl_01",
                "name": "neon_pop.json",
                "mime": "application/json",
                "path": "01_templates/neon_pop.json",
                "mirror_path": "01_templates/neon_pop.json",
                "size": 18,
                "md5": "def456",
                "modified_time": "2026-11-18T18:56:00Z",
                "route": "template",
                "status": "SYNCED",
            },
            {
                "id": "drv_font_01",
                "name": "HindSiliguri-Bold.ttf",
                "mime": "font/ttf",
                "path": "02_fonts/HindSiliguri-Bold.ttf",
                "mirror_path": "02_fonts/HindSiliguri-Bold.ttf",
                "size": 4,
                "md5": "ghi789",
                "modified_time": "2026-11-18T18:57:00Z",
                "route": "font",
                "status": "SYNCED",
            },
        ],
    }
    (mirror_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    # 3. Trigger dry_run sync check and full sync
    sync_dry = client.post("/api/v1/drive/sync", json={"dry_run": True})
    assert sync_dry.status_code == 200
    assert sync_dry.json()["dry_run"] is True
    assert sync_dry.json()["file_count"] == 3

    sync_real = client.post("/api/v1/drive/sync", json={"full": False})
    assert sync_real.status_code == 202
    sr = sync_real.json()
    assert sr["status"] == "CONNECTED"
    assert sr["configured"] is True
    assert sr["folder_id"] == "16EnG42pTWexYiJYao3sSM5XLhqmaeNB-"
    assert sr["file_count"] == 3
    assert sr["counts_by_route"]["source"] == 1
    assert sr["counts_by_route"]["template"] == 1
    assert sr["counts_by_route"]["font"] == 1

    # 4. Tree endpoint returns indexed Drive tree
    tree_resp = client.get("/api/v1/drive/tree")
    assert tree_resp.status_code == 200
    tree_data = tree_resp.json()
    assert "podcast_ep1.mp4" in tree_data["tree_md"]
    assert len(tree_data["manifest"]["files"]) == 3

    # 5. Filter Drive files by route / category (singular and plural supported)
    f_sources = client.get("/api/v1/drive/files?category=sources")
    assert f_sources.status_code == 200
    assert [f["id"] for f in f_sources.json()["items"]] == ["drv_src_01"]

    f_tpls = client.get("/api/v1/drive/files?route=template")
    assert f_tpls.status_code == 200
    assert [f["id"] for f in f_tpls.json()["items"]] == ["drv_tpl_01"]

    f_fonts = client.get("/api/v1/drive/files?subfolder=fonts")
    assert f_fonts.status_code == 200
    assert [f["id"] for f in f_fonts.json()["items"]] == ["drv_font_01"]

    bad_filter = client.get("/api/v1/drive/files?route=nonexistent")
    assert bad_filter.status_code == 422

    # 6. Pick Drive source file -> returns source_ref and optionally creates a Job
    pick1 = client.post("/api/v1/drive/pick", json={"file_id": "drv_src_01"})
    assert pick1.status_code == 200
    p1 = pick1.json()
    assert p1["drive_file_id"] == "drv_src_01"
    assert p1["source_ref"] == str(src_video)

    pick_job = client.post(
        "/api/v1/drive/pick",
        json={"file_id": "drv_src_01", "create_job": True, "language": "hinglish"},
    )
    assert pick_job.status_code == 200
    pj = pick_job.json()
    assert "job_id" in pj
    assert pj["job_status"] == "QUEUED"

    missing_pick = client.post("/api/v1/drive/pick", json={"file_id": "unknown_id"})
    assert missing_pick.status_code == 404
    assert missing_pick.json()["error"]["code"] == "DRIVE_NOT_FOUND"

    # Admin drive view requires auth
    assert client.get("/api/v1/admin/drive").status_code == 401
    _login_admin(client, pw)
    adm_drv = client.get("/api/v1/admin/drive")
    assert adm_drv.status_code == 200
    assert adm_drv.json()["file_count"] == 3


def test_admin_blockers_list_both_dirs_and_resolve_without_secret_leaks(hdb_env):
    client, _engine, tmp_path, _mirror_dir, pw = hdb_env

    # Create one blocker in .clipper-state/blockers and one in legacy .agent/blockers
    b1 = create_or_get_blocker(
        btype=BlockerType.SECRET,
        task_id="T-5.1",
        title="Missing secret: GEMINI_API_KEY",
        needed=[NeededItem(key="GEMINI_API_KEY", where=".env", how_to_get="Google AI Studio")],
        why="LLM highlight scoring",
        root_dir=tmp_path,
        state_dir=tmp_path / ".clipper-state",
    )
    assert b1.id == "BLK-0001"

    legacy_dir = tmp_path / ".agent" / "blockers"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    b2_payload = {
        "id": "BLK-0002",
        "type": "CREDENTIAL",
        "status": "OPEN",
        "task_id": "T-2.2",
        "step": 1,
        "title": "Instagram cookies needed",
        "needed": [
            {
                "key": "COOKIES_INSTAGRAM",
                "where": "secrets/instagram_cookies.txt",
                "how_to_get": "Export Netscape cookies",
            }
        ],
        "why": "Login wall encountered",
        "alternatives": ["Use public reel"],
        "unblocked_meanwhile": ["T-2.3"],
        "created_at": "2026-11-18T12:00:00+00:00",
        "resolved_at": None,
        "resolution_note": None,
        "reminders_sent": 0,
        "correlation_token": "CLP-123456",
    }
    (legacy_dir / "BLK-0002.yaml").write_text(
        yaml.safe_dump(b2_payload, sort_keys=False),
        encoding="utf-8",
    )

    # Unauthenticated access rejected
    assert client.get("/api/v1/admin/blockers").status_code == 401
    _login_admin(client, pw)

    # List all blockers reads both .clipper-state and .agent
    blist = client.get("/api/v1/admin/blockers")
    assert blist.status_code == 200
    items = blist.json()["items"]
    by_id = {b["id"]: b for b in items}
    assert {"BLK-0001", "BLK-0002"}.issubset(by_id.keys())
    for b in items:
        assert set(b.keys()) >= {
            "id",
            "type",
            "status",
            "task_id",
            "title",
            "payload",
            "created_at",
        }
        assert b["status"] in ("OPEN", "RESOLVED")

    # Attempting to resolve with a raw leaked API key in resolution_note is rejected (422)
    leaked_resolve = client.post(
        "/api/v1/admin/blockers/BLK-0001/resolve",
        json={"resolution_note": "Added key AIzaSyD-1234567890abcdefghijklmnopqrstuvwx"},
    )
    assert leaked_resolve.status_code == 422

    # Clean resolution succeeds and sets resolved_by="web" without leaking any secret
    res1 = client.post(
        "/api/v1/admin/blockers/BLK-0001/resolve",
        json={"resolution_note": "Configured GEMINI_API_KEY in .env", "resolved_by": "web"},
    )
    assert res1.status_code == 200
    r1_data = res1.json()
    assert r1_data["id"] == "BLK-0001"
    assert r1_data["status"] == "RESOLVED"
    assert r1_data["resolved_by"] == "web"
    assert r1_data["resolution_note"] == "Configured GEMINI_API_KEY in .env"
    assert r1_data["resolved_at"] is not None
    assert "AIzaSy" not in res1.text

    # Resolve legacy .agent blocker as well
    res2 = client.post(
        "/api/v1/admin/blockers/BLK-0002/resolve",
        json={"note": "Added instagram_cookies.txt"},
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "RESOLVED"
