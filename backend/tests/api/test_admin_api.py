from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session

from backend.app.config import get_settings
from backend.app.db import init_schema, reset_engine
from backend.app.main import app
from backend.app.models.entities import Clip, Job, JobStatus
from backend.app.services.admin import hash_admin_password, reset_login_rate_limits
from backend.app.services.storage import LocalStorageBackend


def _build_minimal_sfnt_ttf(ranges: list[tuple[int, int]]) -> bytes:
    """Build a minimal valid SFNT TTF binary with a cmap format-4 subtable for unit tests."""
    seg_ranges = sorted(ranges) + [(0xFFFF, 0xFFFF)]
    seg_count = len(seg_ranges)
    seg_count_x2 = seg_count * 2

    end_codes = b"".join(struct.pack(">H", e) for _s, e in seg_ranges)
    start_codes = b"".join(struct.pack(">H", s) for s, _e in seg_ranges)
    id_deltas = b"".join(struct.pack(">h", 1) for _ in seg_ranges)
    id_range_offsets = b"".join(struct.pack(">H", 0) for _ in seg_ranges)

    fmt4_body = (
        struct.pack(">HHHH", seg_count_x2, 2, 0, 0)
        + end_codes
        + struct.pack(">H", 0)
        + start_codes
        + id_deltas
        + id_range_offsets
    )
    fmt4 = struct.pack(">HHH", 4, 6 + len(fmt4_body), 0) + fmt4_body
    cmap_header = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12)
    cmap_table = cmap_header + fmt4

    sfnt_header = struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0)
    table_offset = 12 + 16
    table_record = struct.pack(">4sIII", b"cmap", 0, table_offset, len(cmap_table))
    return sfnt_header + table_record + cmap_table


@pytest.fixture
def admin_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = tmp_path / "assets"
    (assets_dir / "templates").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test_admin.db"
    db_url = f"sqlite:///{db_path}"

    pw = "super-secret-owner-pass"
    pw_hash = hash_admin_password(pw)

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("ASSETS_DIR", str(assets_dir))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("ADMIN_ENABLED", "true")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", pw_hash)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSySecretKeyNeverLeak")
    monkeypatch.setenv("NOTIFY_SMTP_APP_PASSWORD", "smtp-secret-never-leak")
    monkeypatch.setenv("S3_SECRET_KEY", "s3-secret-never-leak")
    monkeypatch.setenv("HF_TOKEN", "hf_secret_never_leak")

    get_settings.cache_clear()
    reset_engine()
    reset_login_rate_limits()

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_schema(engine)
    storage = LocalStorageBackend(base_dir=data_dir)
    client = TestClient(app)

    yield client, engine, storage, tmp_path, pw

    reset_login_rate_limits()
    engine.dispose()
    reset_engine()
    get_settings.cache_clear()


def _login_admin(client: TestClient, password: str) -> None:
    resp = client.post("/api/v1/admin/auth/login", json={"password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["authenticated"] is True
    assert "clipper_admin_session" in resp.cookies


def test_admin_auth_rate_limit_and_disabled_mode(admin_env, monkeypatch: pytest.MonkeyPatch):
    client, _engine, _storage, _tmp, pw = admin_env

    # Unauthenticated access rejected (401)
    unauth = client.get("/api/v1/admin/settings")
    assert unauth.status_code == 401

    # Brute-force bad passwords trigger 429 after 5 failed attempts
    for _ in range(5):
        bad = client.post("/api/v1/admin/auth/login", json={"password": "wrong-password"})
        assert bad.status_code == 401

    rate_limited = client.post("/api/v1/admin/auth/login", json={"password": pw})
    assert rate_limited.status_code == 429
    assert rate_limited.json()["error"]["code"] == "RATE_LIMITED"

    # Reset rate limits and log in cleanly
    reset_login_rate_limits()
    _login_admin(client, pw)
    assert client.get("/api/v1/admin/settings").status_code == 200

    # Logout clears session
    client.post("/api/v1/admin/auth/logout")
    assert client.get("/api/v1/admin/settings").status_code == 401

    # ADMIN_ENABLED=false returns 403
    monkeypatch.setenv("ADMIN_ENABLED", "false")
    get_settings.cache_clear()
    assert client.get("/api/v1/admin/settings").status_code == 403
    assert client.post("/api/v1/admin/auth/login", json={"password": pw}).status_code == 403


def test_public_templates_preview_and_presets(admin_env):
    client, _engine, _storage, _tmp, _pw = admin_env

    t_list = client.get("/api/v1/templates")
    assert t_list.status_code == 200
    items = t_list.json()["items"]
    assert len(items) >= 6
    assert any(it["id"] == "bold_pop_yellow" and it["status"] == "published" for it in items)

    t_detail = client.get("/api/v1/templates/bold_pop_yellow")
    assert t_detail.status_code == 200
    assert t_detail.json()["id"] == "bold_pop_yellow"

    t_vers = client.get("/api/v1/templates/bold_pop_yellow/versions")
    assert t_vers.status_code == 200
    assert len(t_vers.json()["items"]) >= 1

    # Server-side PNG test-frame preview render
    prev = client.post(
        "/api/v1/templates/bold_pop_yellow/preview",
        json={"language": "hinglish", "sample_text": "Dosto viral hook"},
    )
    assert prev.status_code == 200
    assert prev.headers["content-type"] == "image/png"
    assert prev.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(prev.content) > 1000

    p_list = client.get("/api/v1/presets")
    assert p_list.status_code == 200
    assert any(p["id"] == "default" and p["is_default"] is True for p in p_list.json()["items"])


def test_admin_template_lifecycle_and_presets(admin_env):
    client, _engine, _storage, _tmp, pw = admin_env
    _login_admin(client, pw)

    # 1. Import invalid template JSON -> status="invalid", publish rejected (400)
    invalid_tpl = {"id": "bad_tpl", "name": "Broken"}
    bad_import = client.post(
        "/api/v1/admin/templates",
        files={"file": ("bad_tpl.json", json.dumps(invalid_tpl).encode("utf-8"), "application/json")},
    )
    assert bad_import.status_code == 201
    assert bad_import.json()["status"] == "invalid"
    assert bad_import.json()["valid"] is False

    bad_pub = client.post("/api/v1/admin/templates/bad_tpl/publish")
    assert bad_pub.status_code == 400
    assert bad_pub.json()["error"]["code"] == "TEMPLATE_INVALID"

    # 2. Import valid .cliptpl.zip -> draft -> duplicate -> publish -> archive -> export
    valid_v2_tpl: dict[str, Any] = {
        "schema_version": 2,
        "meta": {
            "id": "custom_gold",
            "name": "Custom Gold",
            "version": 1,
            "license": "MIT",
        },
        "aspects": ["9:16", "1:1"],
        "fonts": {
            "latn": {"family": "Sans", "weight": 700},
            "deva": {"family": "Sans", "weight": 700},
            "beng": {"family": "Sans", "weight": 700},
        },
        "text": {"size_px": 72, "case": "upper"},
        "colors": {"primary": "#FFFFFF", "active": "#FFD700", "outline": "#000000"},
        "stroke": {"outline_px": 5, "shadow_px": 2},
        "layout": {
            "anchor": "lower_third",
            "y_percent": 72,
            "align": "center",
            "max_words_per_line": 3,
            "max_lines": 2,
        },
        "animation": {"mode": "pages", "active_word": "highlight"},
    }
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("template.json", json.dumps(valid_v2_tpl))
        zf.writestr("notes.txt", "Custom template asset")
    zip_buf.seek(0)

    imp = client.post(
        "/api/v1/admin/templates",
        files={"file": ("custom_gold.cliptpl.zip", zip_buf.getvalue(), "application/zip")},
    )
    assert imp.status_code == 201
    assert imp.json()["id"] == "custom_gold"
    assert imp.json()["status"] == "draft"
    assert imp.json()["valid"] is True

    # Duplicate into new draft
    dup = client.post(
        "/api/v1/admin/templates/custom_gold/duplicate",
        json={"new_id": "custom_gold_v2", "new_name": "Custom Gold Copy"},
    )
    assert dup.status_code == 201
    assert dup.json()["id"] == "custom_gold_v2"
    assert dup.json()["status"] == "draft"

    # Publish custom_gold
    pub = client.post("/api/v1/admin/templates/custom_gold/publish")
    assert pub.status_code == 200
    assert pub.json()["status"] == "published"
    assert pub.json()["latest_version"] == 1

    # Re-publishing creates immutable version 2
    pub2 = client.post("/api/v1/admin/templates/custom_gold/publish")
    assert pub2.status_code == 200
    assert pub2.json()["latest_version"] == 2

    # Export .cliptpl.zip
    exp = client.get("/api/v1/admin/templates/custom_gold/export")
    assert exp.status_code == 200
    assert exp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exp.content)) as zf:
        names = set(zf.namelist())
        assert "template.json" in names
        assert "manifest.json" in names

    # Archive removes from public picker but keeps in admin list
    arch = client.post("/api/v1/admin/templates/custom_gold/archive")
    assert arch.status_code == 200
    assert arch.json()["status"] == "archived"
    pub_ids = {t["id"] for t in client.get("/api/v1/templates").json()["items"]}
    assert "custom_gold" not in pub_ids
    adm_ids = {t["id"] for t in client.get("/api/v1/admin/templates").json()["items"]}
    assert "custom_gold" in adm_ids

    # Save preset and set default
    pr = client.post(
        "/api/v1/admin/presets",
        json={
            "id": "creator_gold",
            "name": "Creator Gold",
            "caption": "custom_gold@2",
            "layout": "fill@latest",
            "render_profile": "1080p_hq",
            "is_default": True,
        },
    )
    assert pr.status_code == 200
    assert pr.json()["is_default"] is True
    presets = client.get("/api/v1/admin/presets").json()["items"]
    by_id = {p["id"]: p for p in presets}
    assert by_id["creator_gold"]["is_default"] is True
    assert by_id["default"]["is_default"] is False


def test_admin_fonts_brandkits_prompts_scoring_settings_jobs_audit(admin_env):
    client, engine, storage, _tmp, pw = admin_env
    _login_admin(client, pw)

    # 1. Fonts upload with SFNT cmap coverage (Latin + Devanagari)
    ttf_bytes = _build_minimal_sfnt_ttf([(0x0041, 0x005A), (0x0900, 0x097F)])
    f_resp = client.post(
        "/api/v1/admin/fonts",
        files={"file": ("NotoDeva.ttf", ttf_bytes, "font/ttf")},
        data={"license": "OFL-1.1", "family": "Noto Sans Deva", "style": "Bold"},
    )
    assert f_resp.status_code == 201
    f_data = f_resp.json()
    assert f_data["license_ok"] is True
    assert f_data["scripts"] == {"latn": True, "deva": True, "beng": False}
    assert len(client.get("/api/v1/admin/fonts").json()["items"]) == 1

    # 2. Brand kits
    bk_resp = client.post(
        "/api/v1/admin/brandkits",
        json={
            "id": "brand_main",
            "name": "Main Channel",
            "watermark": "@clipper",
            "palette": ["#FFE600", "#0F172A"],
            "default_preset": "default",
        },
    )
    assert bk_resp.status_code == 201
    assert len(client.get("/api/v1/admin/brandkits").json()["items"]) == 1

    # 3. Prompts list, get, update, activate, test
    plist = client.get("/api/v1/admin/prompts")
    assert plist.status_code == 200
    assert any(p["name"] == "highlight_map" for p in plist.json()["items"])

    p_put = client.put(
        "/api/v1/admin/prompts/highlight_map",
        json={"content": "# v2 prompt\nReturn JSON moments."},
    )
    assert p_put.status_code == 200
    assert p_put.json()["active_version"] == 2

    p_act = client.post(
        "/api/v1/admin/prompts/highlight_map/activate",
        json={"version": 1},
    )
    assert p_act.status_code == 200
    assert p_act.json()["active_version"] == 1

    p_test = client.post("/api/v1/admin/prompts/highlight_map/test")
    assert p_test.status_code == 200
    assert p_test.json()["valid"] is True

    # 4. Seed a job + transcript + clip for scoring dry-run and admin jobs view
    job_id = "job_adm_01"
    jdir = storage.job_dir(job_id)
    tp = jdir / "asr" / "transcript.json"
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "language": "en",
                "script": "latn",
                "duration": 30.0,
                "engine": "heuristic_fallback",
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 18.0,
                        "text": "Here is the brutal secret why 99 percent fail!",
                        "words": [
                            {"w": "Here", "s": 0.0, "e": 0.4, "p": 0.95},
                            {"w": "secret", "s": 0.4, "e": 0.9, "p": 0.95},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                source_kind="url",
                source_url="https://youtu.be/adm01",
                status=JobStatus.COMPLETED.value,
            )
        )
        session.add(
            Clip(
                id="clip_adm_01",
                job_id=job_id,
                rank=1,
                start=0.0,
                end=18.0,
                score=0.81,
                title="Secret Clip",
                feedback="up",
                actual_views=2500,
            )
        )
        session.commit()

    sc_get = client.get("/api/v1/admin/scoring")
    assert sc_get.status_code == 200
    assert sc_get.json()["feedback_stats"]["up"] == 1

    sc_put = client.put(
        "/api/v1/admin/scoring",
        json={"weights": {"default": {"llm": 0.7, "energy": 0.2, "speech_rate": 0.1}}},
    )
    assert sc_put.status_code == 200
    assert sc_put.json()["weights"]["default"]["llm"] == 0.7

    sc_dry = client.post(
        "/api/v1/admin/scoring/dry-run",
        json={"job_id": job_id, "weights": {"llm": 0.8, "energy": 0.2}},
    )
    assert sc_dry.status_code == 200
    assert sc_dry.json()["job_id"] == job_id
    assert len(sc_dry.json()["proposed_picks"]) >= 1

    # 5. Settings NEVER leaks raw secrets; allows non-secret updates
    st_get = client.get("/api/v1/admin/settings")
    assert st_get.status_code == 200
    raw_st_text = st_get.text
    for secret_val in (
        "AIzaSySecretKeyNeverLeak",
        "smtp-secret-never-leak",
        "s3-secret-never-leak",
        "hf_secret_never_leak",
        "super-secret-owner-pass",
    ):
        assert secret_val not in raw_st_text
    st_json = st_get.json()
    assert st_json["secrets"]["gemini_api_key"] == {"set": True}
    assert st_json["secrets"]["notify_smtp_app_password"] == {"set": True}
    assert st_json["secrets"]["s3_secret_key"] == {"set": True}

    bad_st_put = client.put(
        "/api/v1/admin/settings",
        json={"gemini_api_key": "override"},
    )
    assert bad_st_put.status_code == 422

    st_put = client.put(
        "/api/v1/admin/settings",
        json={"ollama_model": "qwen2.5:14b-instruct", "max_video_hours": 4},
    )
    assert st_put.status_code == 200
    assert st_put.json()["ollama_model"] == "qwen2.5:14b-instruct"
    assert st_put.json()["max_video_hours"] == 4
    assert "AIzaSySecretKeyNeverLeak" not in st_put.text

    # 6. Admin jobs view includes disk usage & artifacts
    aj = client.get("/api/v1/admin/jobs")
    assert aj.status_code == 200
    job_entry = aj.json()["items"][0]
    assert job_entry["id"] == job_id
    assert job_entry["disk_usage_bytes"] > 0
    assert any(a["rel_path"] == "asr/transcript.json" for a in job_entry["artifacts"])

    # 7. Audit log records every write
    aud = client.get("/api/v1/admin/audit?limit=50")
    assert aud.status_code == 200
    actions = {item["action"] for item in aud.json()["items"]}
    assert {
        "font.register",
        "brandkit.create",
        "prompt.update",
        "prompt.activate",
        "scoring.update",
        "settings.update",
    }.issubset(actions)
