from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session, select

from backend.app.config import get_settings
from backend.app.db import init_schema, reset_engine
from backend.app.main import app
from backend.app.models.entities import (
    Clip,
    FeedbackEvent,
    Job,
    JobStatus,
    Render,
    StageCheckpoint,
    Transcript,
)
from backend.app.services import queue as queue_mod
from backend.app.services.orchestrator import (
    STAGES,
    clear_job_events,
    record_job_event,
)
from backend.app.services.storage import LocalStorageBackend


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test_api.db"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("QUEUE_MODE", "huey_sqlite")
    monkeypatch.setenv("QUEUE_URL", f"sqlite:///{tmp_path / 'huey.db'}")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    reset_engine()

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_schema(engine)
    storage = LocalStorageBackend(base_dir=data_dir)
    queue_mod.configure_queue(queue_url=f"sqlite:///{tmp_path / 'huey.db'}", immediate=False)

    client = TestClient(app)
    yield client, engine, storage, tmp_path

    engine.dispose()
    reset_engine()
    get_settings.cache_clear()


def _parse_sse_frames(raw_text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip() or block.strip().startswith(":"):
            continue
        frame: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("id:"):
                frame["id"] = int(line[3:].strip())
            elif line.startswith("event:"):
                frame["event"] = line[6:].strip()
            elif line.startswith("data:"):
                frame["data"] = json.loads(line[5:].strip())
        if frame:
            frames.append(frame)
    return frames


def test_create_job_spec_v2_and_idempotency(api_env, monkeypatch: pytest.MonkeyPatch):
    client, engine, _storage, _tmp = api_env
    enqueued: list[str] = []

    def _fake_enqueue(job_id: str, from_stage: str | None = None):
        enqueued.append(job_id)
        return None

    monkeypatch.setattr("backend.app.main.enqueue_job", _fake_enqueue)

    payload = {
        "source_kind": "url",
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "clip_brief": "hook moments",
        "preset_id": "preset_default",
        "language": "hinglish",
        "script_pref": "latn",
        "aspect": "9:16",
        "auto_mode": True,
        "max_clips": 5,
        "rights_confirmed": True,
    }
    r1 = client.post("/api/v1/jobs", headers={"Idempotency-Key": "idem-key-001"}, json=payload)
    assert r1.status_code == 201, r1.text
    d1 = r1.json()
    assert d1["source_kind"] == "url"
    assert d1["platform"] == "youtube"
    assert d1["clip_brief"] == "hook moments"
    assert d1["preset_id"] == "preset_default"
    assert d1["status"] == "QUEUED"
    assert len(enqueued) == 1

    # Replay with same Idempotency-Key returns the same job without re-enqueuing
    r2 = client.post("/api/v1/jobs", headers={"Idempotency-Key": "idem-key-001"}, json=payload)
    assert r2.status_code == 201
    assert r2.json()["id"] == d1["id"]
    assert len(enqueued) == 1

    # Different Idempotency-Key creates a new job
    r3 = client.post("/api/v1/jobs", headers={"Idempotency-Key": "idem-key-002"}, json=payload)
    assert r3.status_code == 201
    assert r3.json()["id"] != d1["id"]
    assert len(enqueued) == 2

    # Explicitly false rights_confirmed is rejected
    bad = client.post(
        "/api/v1/jobs",
        json={**payload, "rights_confirmed": False},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_jobs_cursor_pagination_and_status_filter(api_env):
    client, engine, _storage, _tmp = api_env
    with Session(engine) as session:
        for idx in range(5):
            session.add(
                Job(
                    id=f"job_p_{idx:02d}",
                    source_kind="url",
                    source_url=f"https://youtu.be/vid{idx}",
                    platform="youtube",
                    status=JobStatus.COMPLETED.value if idx % 2 == 0 else JobStatus.FAILED.value,
                    created_at=f"2026-11-18T10:00:0{idx}+00:00",
                )
            )
        session.commit()

    # Page 1 of all jobs (limit=2)
    p1 = client.get("/api/v1/jobs?limit=2")
    assert p1.status_code == 200
    b1 = p1.json()
    assert b1["total"] == 5
    assert [it["id"] for it in b1["items"]] == ["job_p_04", "job_p_03"]
    assert b1["next_cursor"] is not None

    # Page 2
    p2 = client.get(f"/api/v1/jobs?limit=2&cursor={b1['next_cursor']}")
    assert p2.status_code == 200
    b2 = p2.json()
    assert [it["id"] for it in b2["items"]] == ["job_p_02", "job_p_01"]
    assert b2["next_cursor"] is not None

    # Page 3 (final)
    p3 = client.get(f"/api/v1/jobs?limit=2&cursor={b2['next_cursor']}")
    assert p3.status_code == 200
    b3 = p3.json()
    assert [it["id"] for it in b3["items"]] == ["job_p_00"]
    assert b3["next_cursor"] is None

    # Status filter
    pf = client.get("/api/v1/jobs?status=FAILED")
    assert pf.status_code == 200
    bf = pf.json()
    assert bf["total"] == 2
    assert [it["id"] for it in bf["items"]] == ["job_p_03", "job_p_01"]


def test_retry_cancel_and_delete_job(api_env, monkeypatch: pytest.MonkeyPatch):
    client, engine, storage, _tmp = api_env
    enqueued: list[tuple[str, str | None]] = []

    def _fake_enqueue(job_id: str, from_stage: str | None = None):
        enqueued.append((job_id, from_stage))
        return None

    monkeypatch.setattr("backend.app.services.orchestrator.enqueue_job", _fake_enqueue)

    with Session(engine) as session:
        session.add(
            Job(
                id="job_running",
                source_kind="url",
                source_url="https://youtu.be/run1",
                status=JobStatus.TRANSCRIBING.value,
                stage="transcribe",
            )
        )
        session.add(
            Job(
                id="job_failed",
                source_kind="url",
                source_url="https://youtu.be/fail1",
                status=JobStatus.FAILED.value,
                stage="analyze",
            )
        )
        for st in ("fetch", "prepare", "transcribe", "analyze"):
            session.add(
                StageCheckpoint(
                    job_id="job_failed",
                    stage=st,
                    status="DONE" if st != "analyze" else "FAILED",
                    attempt=1,
                )
            )
        session.commit()

    # 409 conflict when job is actively running
    r_conflict = client.post("/api/v1/jobs/job_running/retry", json={"from_stage": "transcribe"})
    assert r_conflict.status_code == 409
    assert "error" in r_conflict.json()

    # 202 retry from stage when failed
    r_retry = client.post("/api/v1/jobs/job_failed/retry", json={"from_stage": "transcribe"})
    assert r_retry.status_code == 202
    retry_data = r_retry.json()
    assert retry_data["status"] == "QUEUED"
    assert enqueued == [("job_failed", "transcribe")]

    with Session(engine) as session:
        cps = {
            cp.stage: cp.status
            for cp in session.exec(
                select(StageCheckpoint).where(StageCheckpoint.job_id == "job_failed")
            ).all()
        }
        assert cps["fetch"] == "DONE"
        assert cps["prepare"] == "DONE"
        assert cps["transcribe"] == "STALE"
        assert cps["analyze"] == "STALE"

    # Cancel running job -> 200 CANCELLED
    r_cancel = client.post("/api/v1/jobs/job_running/cancel")
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "CANCELLED"

    # Delete job -> 204, removes DB and disk directory
    jdir = storage.job_dir("job_failed")
    (jdir / "source" / "dummy.txt").write_text("hi", encoding="utf-8")
    assert jdir.exists()

    r_del = client.delete("/api/v1/jobs/job_failed")
    assert r_del.status_code == 204
    assert not jdir.exists()
    assert client.get("/api/v1/jobs/job_failed").status_code == 404


def test_sse_events_stream_and_last_event_id_replay(api_env):
    client, engine, storage, _tmp = api_env
    job_id = "job_sse_01"
    clear_job_events(job_id)
    jdir = storage.job_dir(job_id)

    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                source_kind="url",
                source_url="https://youtu.be/sse",
                status=JobStatus.TRANSCRIBING.value,
                stage="transcribe",
            )
        )
        session.commit()

    ev1 = record_job_event(
        job_id,
        "state",
        {"stage": "fetch", "status": "FETCHING", "pct": 10, "message": "Fetching"},
        job_dir=jdir,
    )
    ev2 = record_job_event(
        job_id,
        "progress",
        {"stage": "transcribe", "status": "TRANSCRIBING", "pct": 40, "message": "Transcribing"},
        job_dir=jdir,
    )

    def _finish_later():
        time.sleep(0.15)
        record_job_event(
            job_id,
            "warning",
            {"stage": "render", "status": "RENDERING", "message": "Fallback font used"},
            job_dir=jdir,
        )
        with Session(engine) as s:
            j = s.get(Job, job_id)
            assert j is not None
            j.status = JobStatus.COMPLETED.value
            j.stage = "COMPLETED"
            j.progress_pct = 100
            s.add(j)
            s.commit()
        record_job_event(
            job_id,
            "done",
            {"stage": "COMPLETED", "status": "COMPLETED", "pct": 100, "message": "Done"},
            job_dir=jdir,
        )

    t = threading.Thread(target=_finish_later, daemon=True)
    t.start()

    # Stream starting after ev1 (Last-Event-ID replay)
    with client.stream(
        "GET",
        f"/api/v1/jobs/{job_id}/events?timeout=5",
        headers={"Last-Event-ID": str(ev1.id)},
    ) as resp:
        assert resp.status_code == 200
        raw = resp.read().decode("utf-8")

    t.join(timeout=2.0)
    frames = _parse_sse_frames(raw)
    assert [f["id"] for f in frames] == [ev2.id, ev2.id + 1, ev2.id + 2]
    assert [f["event"] for f in frames] == ["progress", "warning", "done"]
    assert frames[-1]["data"]["status"] == "COMPLETED"


def test_transcript_patch_marks_analyze_stale_and_reselect_render_all(
    api_env, monkeypatch: pytest.MonkeyPatch
):
    client, engine, storage, _tmp = api_env
    job_id = "job_tr_01"
    jdir = storage.job_dir(job_id)
    tp = jdir / "asr" / "transcript.json"
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "language": "en",
                "script": "latn",
                "duration": 10.0,
                "engine": "faster_whisper",
                "edited": False,
                "segments": [
                    {
                        "id": 0,
                        "start": 0.0,
                        "end": 5.0,
                        "text": "hello world",
                        "words": [
                            {"w": "hello", "s": 0.0, "e": 0.5, "p": 0.9},
                            {"w": "world", "s": 0.5, "e": 1.0, "p": 0.9},
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
                source_url="https://youtu.be/tr",
                status=JobStatus.COMPLETED.value,
                stage="COMPLETED",
            )
        )
        session.add(
            Transcript(
                id="tr_01",
                job_id=job_id,
                language="en",
                script="latn",
                path_json=str(tp),
                edited=False,
            )
        )
        for st in STAGES:
            session.add(StageCheckpoint(job_id=job_id, stage=st, status="DONE", attempt=1))
        session.commit()

    # GET transcript
    rg = client.get(f"/api/v1/jobs/{job_id}/transcript")
    assert rg.status_code == 200
    assert rg.json()["segments"][0]["text"] == "hello world"

    # PATCH transcript word index 1 -> "clipper"
    rp = client.patch(
        f"/api/v1/jobs/{job_id}/transcript",
        json={"edits": [{"idx": 1, "text": "clipper"}]},
    )
    assert rp.status_code == 200
    updated = rp.json()
    assert updated["edited"] is True
    assert updated["segments"][0]["text"] == "hello clipper"
    assert "edited" in updated["segments"][0]["words"][1]["flags"]
    assert json.loads(tp.read_text(encoding="utf-8"))["segments"][0]["text"] == "hello clipper"

    with Session(engine) as session:
        tr_row = session.get(Transcript, "tr_01")
        assert tr_row is not None and tr_row.edited is True
        cps = {
            cp.stage: cp.status
            for cp in session.exec(
                select(StageCheckpoint).where(StageCheckpoint.job_id == job_id)
            ).all()
        }
        assert cps["fetch"] == "DONE"
        assert cps["prepare"] == "DONE"
        assert cps["transcribe"] == "DONE"
        assert cps["analyze"] == "STALE"
        assert cps["select"] == "STALE"
        assert cps["render"] == "STALE"
        assert cps["package"] == "STALE"

    enqueued: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "backend.app.services.orchestrator.enqueue_job",
        lambda jid, from_stage=None: enqueued.append((jid, from_stage)),
    )

    # Reselect clips
    rr = client.post(
        f"/api/v1/jobs/{job_id}/reselect",
        json={"clip_brief": "only funny clips", "threshold": 0.7, "max_clips": 4},
    )
    assert rr.status_code == 202
    assert rr.json()["clip_brief"] == "only funny clips"
    assert rr.json()["max_clips"] == 4
    assert enqueued[-1] == (job_id, "select")

    # Reset status to AWAITING_REVIEW and test render-all
    with Session(engine) as session:
        j = session.get(Job, job_id)
        assert j is not None
        j.status = JobStatus.AWAITING_REVIEW.value
        session.add(j)
        session.commit()

    ra = client.post(f"/api/v1/jobs/{job_id}/render-all", json={"preset_id": "preset_bold"})
    assert ra.status_code == 202
    assert ra.json()["preset_id"] == "preset_bold"
    assert enqueued[-1] == (job_id, "render")


def test_clip_detail_merge_patch_render_and_feedback(api_env, monkeypatch: pytest.MonkeyPatch):
    client, engine, storage, _tmp = api_env
    job_id = "job_clip_01"
    clip_id = "clip_01"
    jdir = storage.job_dir(job_id)

    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                source_kind="url",
                source_url="https://youtu.be/clip1",
                status=JobStatus.COMPLETED.value,
                script="latn",
            )
        )
        session.add(
            Clip(
                id=clip_id,
                job_id=job_id,
                rank=1,
                start=10.0,
                end=30.0,
                start_s=10.0,
                end_s=30.0,
                duration=20.0,
                score=0.88,
                title="Original Title",
                words_json=json.dumps([{"w": "Hi", "s": 10.0, "e": 10.5}]),
            )
        )
        session.commit()

    # GET clip detail includes clip_plan
    gc = client.get(f"/api/v1/clips/{clip_id}")
    assert gc.status_code == 200
    detail = gc.json()
    assert detail["clip_plan"]["clip_id"] == clip_id
    assert detail["clip_plan"]["segments"] == [{"start": 10.0, "end": 30.0}]

    # RFC-7396 JSON Merge Patch (null deletes keys, nested dicts merge, DB stays in sync)
    pc = client.patch(
        f"/api/v1/clips/{clip_id}",
        headers={"Content-Type": "application/merge-patch+json"},
        json={
            "segments": [{"start": 12.5, "end": 28.5}],
            "layout": {"force": "fit_blur"},
            "brand_kit": None,
            "audio": {"fade_in_ms": 250, "fade_out_ms": None},
            "captions": {"template": "clean_minimal@1", "overrides": {"font_size": 60}},
        },
    )
    assert pc.status_code == 200
    patched = pc.json()
    assert patched["start"] == 12.5
    assert patched["start_s"] == 12.5
    assert patched["end"] == 28.5
    assert patched["end_s"] == 28.5
    assert patched["duration"] == 16.0
    assert patched["layout"] == "fit_blur"
    assert patched["template_id"] == "clean_minimal"
    assert "brand_kit" not in patched["clip_plan"]
    assert "fade_out_ms" not in patched["clip_plan"]["audio"]
    assert patched["clip_plan"]["audio"]["fade_in_ms"] == 250
    assert patched["clip_plan"]["audio"]["loudness_lufs"] == -14
    assert patched["clip_plan"]["captions"]["overrides"] == {"font_size": 60}

    # Legacy flat patch also syncs clip_plan
    pc2 = client.patch(
        f"/api/v1/clips/{clip_id}",
        json={"start": 14.0, "end": 26.0, "title": "Updated Title", "aspect": "1:1"},
    )
    assert pc2.status_code == 200
    p2 = pc2.json()
    assert p2["title"] == "Updated Title"
    assert p2["aspect"] == "1:1"
    assert p2["clip_plan"]["segments"] == [{"start": 14.0, "end": 26.0}]
    assert p2["clip_plan"]["output"]["aspect"] == "1:1"

    # Mock render_clip_media to create real dummy files and test render + renders list + file endpoints
    def _fake_render_clip_media(**kwargs):
        rdir = jdir / "renders"
        rdir.mkdir(parents=True, exist_ok=True)
        mp4 = rdir / f"{kwargs['clip_id']}.mp4"
        ass = rdir / f"{kwargs['clip_id']}.ass"
        srt = rdir / f"{kwargs['clip_id']}.srt"
        thumb = rdir / f"{kwargs['clip_id']}.jpg"
        mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        ass.write_text("[Script Info]\n", encoding="utf-8")
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
        thumb.write_bytes(b"\xff\xd8\xff\xe0")
        return {
            "mp4_path": str(mp4),
            "ass_path": str(ass),
            "srt_path": str(srt),
            "thumb_path": str(thumb),
        }

    monkeypatch.setattr("backend.app.main.render_clip_media", _fake_render_clip_media)

    rr = client.post(
        f"/api/v1/clips/{clip_id}/render",
        json={"aspect": "1:1", "profile": "1080p_hq"},
    )
    assert rr.status_code == 202
    ren_data = rr.json()
    ren_id = ren_data["id"]
    assert ren_data["clip_id"] == clip_id
    assert ren_data["status"] == "DONE"
    assert ren_data["profile"] == "1080p_hq"

    rl = client.get(f"/api/v1/clips/{clip_id}/renders")
    assert rl.status_code == 200
    assert len(rl.json()["items"]) == 1
    assert rl.json()["items"][0]["id"] == ren_id

    rg = client.get(f"/api/v1/renders/{ren_id}")
    assert rg.status_code == 200
    assert rg.json()["id"] == ren_id

    rf = client.get(f"/api/v1/renders/{ren_id}/file")
    assert rf.status_code == 200
    assert rf.content.startswith(b"\x00\x00\x00\x18ftypmp42")

    # Feedback endpoint
    fb = client.post(
        f"/api/v1/clips/{clip_id}/feedback",
        json={"value": "up", "actual_views": 4200},
    )
    assert fb.status_code == 200
    assert fb.json()["feedback"] == "up"
    assert fb.json()["actual_views"] == 4200

    with Session(engine) as session:
        events = list(
            session.exec(select(FeedbackEvent).where(FeedbackEvent.clip_id == clip_id)).all()
        )
        assert {(e.kind, e.value) for e in events} == {
            ("rating", "up"),
            ("actual_views", "4200"),
        }
        assert session.get(Render, ren_id) is not None


def test_chunked_upload_and_legacy_multipart_upload(
    api_env, monkeypatch: pytest.MonkeyPatch
):
    client, _engine, _storage, _tmp = api_env
    payload = b"hello-clipper-" * 100
    chunk0 = payload[:700]
    chunk1 = payload[700:]
    digest = hashlib.sha256(payload).hexdigest()

    # 1. Init chunked upload
    init_resp = client.post(
        "/api/v1/uploads",
        json={
            "filename": "my_video.mp4",
            "size_bytes": len(payload),
            "mime_type": "video/mp4",
        },
    )
    assert init_resp.status_code == 201
    session_data = init_resp.json()
    upload_id = session_data["id"]
    assert session_data["status"] == "INIT"
    assert session_data["received_chunks"] == 0

    # 2. Upload chunks
    c0 = client.put(f"/api/v1/uploads/{upload_id}/chunk?n=0", content=chunk0)
    assert c0.status_code == 200
    assert c0.json()["status"] == "UPLOADING"
    assert c0.json()["received_chunks"] == 1

    c1 = client.put(f"/api/v1/uploads/{upload_id}/chunk?n=1", content=chunk1)
    assert c1.status_code == 200
    assert c1.json()["received_chunks"] == 2

    # 3. Checksum mismatch rejected
    bad_done = client.post(
        f"/api/v1/uploads/{upload_id}/complete",
        json={"sha256": "0" * 64},
    )
    assert bad_done.status_code == 400

    # 4. Complete with valid SHA-256
    done = client.post(
        f"/api/v1/uploads/{upload_id}/complete",
        json={"sha256": digest},
    )
    assert done.status_code == 200
    done_data = done.json()
    assert done_data["status"] == "COMPLETED"
    assert done_data["sha256"] == digest
    assert done_data["upload_id"] == upload_id
    assert Path(done_data["upload_path"]).read_bytes() == payload

    # 5. Create job referencing completed upload_id
    monkeypatch.setattr("backend.app.main.enqueue_job", lambda jid, from_stage=None: None)
    job_resp = client.post(
        "/api/v1/jobs",
        json={
            "source_kind": "upload",
            "source_ref": upload_id,
            "rights_confirmed": True,
        },
    )
    assert job_resp.status_code == 201
    assert job_resp.json()["source_kind"] == "upload"
    assert job_resp.json()["source_url"] == done_data["upload_path"]

    # 6. Legacy multipart upload preserved
    mp_resp = client.post(
        "/api/v1/uploads",
        files={"file": ("legacy.mp4", b"fake-mp4-bytes", "video/mp4")},
    )
    assert mp_resp.status_code == 200
    mp_data = mp_resp.json()
    assert mp_data["filename"] == "legacy.mp4"
    assert Path(mp_data["upload_path"]).read_bytes() == b"fake-mp4-bytes"
