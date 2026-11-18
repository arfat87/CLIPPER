from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session

from backend.app.config import get_settings
from backend.app.db import init_schema, reset_engine
from backend.app.main import app
from backend.app.models.entities import Clip, Job, JobStatus
from backend.app.services.storage import LocalStorageBackend
from backend.pipeline.publish import load_publish_pack_schema


@pytest.fixture
def pub_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test_publish.db"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")

    get_settings.cache_clear()
    reset_engine()

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_schema(engine)
    storage = LocalStorageBackend(base_dir=data_dir)
    client = TestClient(app)

    yield client, engine, storage, tmp_path

    engine.dispose()
    reset_engine()
    get_settings.cache_clear()


def test_publish_pack_fallback_generation_edit_regenerate_all_languages(pub_env):
    client, engine, storage, _tmp = pub_env
    schema = load_publish_pack_schema()

    samples = [
        ("en", "latn", "Shipping fast beats perfection every time!"),
        ("hi", "deva", "सफलता हर दिन एक प्रतिशत बेहतर बनने से मिलती है!"),
        ("hinglish", "latn", "99 percent creators fail kyunki woh hook nahi dete!"),
        ("bn", "beng", "প্রথম তিন সেকেন্ডেই দর্শকের মনোযোগ ধরে রাখা জরুরি!"),
    ]

    for idx, (lang, script, snippet) in enumerate(samples, start=1):
        job_id = f"job_pub_{lang}"
        clip_id = f"clip_pub_{lang}"

        with Session(engine) as session:
            session.add(
                Job(
                    id=job_id,
                    source_kind="url",
                    source_url=f"https://youtu.be/test_{lang}",
                    title=f"Source Video ({lang})",
                    language=lang,
                    detected_language=lang,
                    script=script,
                    status=JobStatus.COMPLETED.value,
                )
            )
            session.add(
                Clip(
                    id=clip_id,
                    job_id=job_id,
                    rank=idx,
                    start=5.0,
                    end=25.0,
                    start_s=5.0,
                    end_s=25.0,
                    duration=20.0,
                    score=0.91,
                    title=f"Top Moment ({lang})",
                    hook_line=snippet,
                    transcript_snippet=snippet,
                    hashtags_json=json.dumps(["#Shorts", "#Viral", f"#{lang}"]),
                    words_json=json.dumps(
                        [
                            {"w": w, "s": 5.0 + i * 0.4, "e": 5.35 + i * 0.4}
                            for i, w in enumerate(snippet.split())
                        ],
                        ensure_ascii=False,
                    ),
                    status="RENDERED",
                )
            )
            session.commit()

        # 1. GET auto-generates and persists schema-valid PublishPack + sidecars
        g_resp = client.get(f"/api/v1/clips/{clip_id}/publish-pack")
        assert g_resp.status_code == 200, g_resp.text
        pack = g_resp.json()
        jsonschema.validate(instance=pack, schema=schema)
        assert pack["clip_id"] == clip_id
        assert pack["language"] == lang
        assert pack["platform"] == "youtube_shorts"
        assert pack["valid"] is True

        clip_dir = storage.job_dir(job_id) / "clips" / clip_id
        assert (clip_dir / "publish_pack.json").exists()
        assert (clip_dir / "publish_pack.md").exists()

        # 2. PUT edits title, description, hashtags, pinned_comment, and schedule_notes
        put_resp = client.put(
            f"/api/v1/clips/{clip_id}/publish-pack",
            json={
                "title": f"Edited {lang.upper()} Title!",
                "description": f"{snippet}\n\nSubscribe now! #Shorts #Creator",
                "hashtags": ["Shorts", "#Creator", "#Viral"],
                "tags": ["shorts", "clipper", lang],
                "pinned_comment": "Drop your thoughts below!",
                "schedule_notes": "Post at 18:00 IST",
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        edited = put_resp.json()
        jsonschema.validate(instance=edited, schema=schema)
        assert edited["title"] == f"Edited {lang.upper()} Title!"
        assert edited["hashtags"] == ["#Shorts", "#Creator", "#Viral"]
        assert edited["pinned_comment"] == "Drop your thoughts below!"
        assert edited["produced_by"]["schedule_notes"] == "Post at 18:00 IST"
        assert "Edited" in (clip_dir / "publish_pack.md").read_text(encoding="utf-8")

        # 3. Invalid PUT (title > 100 chars) is rejected with 422
        bad_put = client.put(
            f"/api/v1/clips/{clip_id}/publish-pack",
            json={"title": "X" * 105},
        )
        assert bad_put.status_code == 422

        # 4. POST /regenerate with publish_template_id, language, and tone
        regen = client.post(
            f"/api/v1/clips/{clip_id}/publish-pack/regenerate",
            json={
                "publish_template_id": "creator_punchy",
                "language": lang,
                "tone": "energetic",
                "platform": "instagram_reels",
            },
        )
        assert regen.status_code == 200, regen.text
        r_pack = regen.json()
        jsonschema.validate(instance=r_pack, schema=schema)
        assert r_pack["platform"] == "instagram_reels"
        assert r_pack["language"] == lang
        assert r_pack["template"] == "creator_punchy"


def test_download_job_zip_bundle_contents(pub_env):
    client, engine, storage, _tmp = pub_env
    job_id = "job_zip_01"
    clip_id = "clip_zip_01"
    jdir = storage.job_dir(job_id)
    cdir = jdir / "clips" / clip_id
    cdir.mkdir(parents=True, exist_ok=True)

    mp4_path = cdir / "out.mp4"
    ass_path = cdir / "captions.ass"
    srt_path = cdir / "captions.srt"
    vtt_path = cdir / "captions.vtt"
    thumb_path = cdir / "thumb.jpg"
    plan_path = cdir / "clip_plan.json"

    mp4_path.write_bytes(b"\x00\x00\x00\x18ftypmp42renderedvideo")
    ass_path.write_text("[Script Info]\nTitle: Test\n", encoding="utf-8")
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello world\n", encoding="utf-8")
    vtt_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world\n", encoding="utf-8")
    thumb_path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    plan_path.write_text(
        json.dumps({"schema_version": 1, "clip_id": clip_id, "segments": [{"start": 0.0, "end": 15.0}]}),
        encoding="utf-8",
    )

    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                source_kind="url",
                source_url="https://youtu.be/zip01",
                title="ZIP Export Test Job",
                language="hinglish",
                detected_language="hinglish",
                script="latn",
                status=JobStatus.COMPLETED.value,
            )
        )
        session.add(
            Clip(
                id=clip_id,
                job_id=job_id,
                rank=1,
                start=0.0,
                end=15.0,
                start_s=0.0,
                end_s=15.0,
                duration=15.0,
                score=0.95,
                title="Viral Hinglish Hook",
                hook_line="Dosto ye trick dekho!",
                transcript_snippet="Dosto ye trick dekho aur share karo!",
                plan_path=str(plan_path),
                render_mp4_path=str(mp4_path),
                render_ass_path=str(ass_path),
                render_srt_path=str(srt_path),
                thumb_path=str(thumb_path),
                status="RENDERED",
            )
        )
        session.commit()

    z_resp = client.get(f"/api/v1/jobs/{job_id}/download.zip")
    assert z_resp.status_code == 200
    assert z_resp.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(z_resp.content)) as zf:
        names = set(zf.namelist())
        assert "job_manifest.json" in names
        assert f"clips/{clip_id}/publish_pack.json" in names
        assert f"clips/{clip_id}/publish_pack.md" in names
        assert f"clips/{clip_id}/clip_plan.json" in names
        assert f"clips/{clip_id}/out.mp4" in names
        assert f"clips/{clip_id}/captions.ass" in names
        assert f"clips/{clip_id}/captions.srt" in names
        assert f"clips/{clip_id}/captions.vtt" in names
        assert f"clips/{clip_id}/thumb.jpg" in names

        manifest = json.loads(zf.read("job_manifest.json").decode("utf-8"))
        assert manifest["job_id"] == job_id
        assert manifest["clip_count"] == 1
        assert manifest["clips"][0]["id"] == clip_id

        pp = json.loads(zf.read(f"clips/{clip_id}/publish_pack.json").decode("utf-8"))
        jsonschema.validate(instance=pp, schema=load_publish_pack_schema())
        assert pp["clip_id"] == clip_id
