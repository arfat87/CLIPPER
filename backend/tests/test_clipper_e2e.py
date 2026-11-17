from __future__ import annotations

from pathlib import Path

import pysubs2
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.pipeline.asr.engine import (
    detect_language_and_hinglish,
    detect_word_script,
    normalize_transcript_script,
)
from backend.pipeline.captions.ass_builder import build_ass_subtitles, chunk_words_into_pages
from backend.pipeline.captions.template_loader import (
    ensure_starter_templates,
    get_template_by_id,
    validate_template_dict,
)
from backend.pipeline.ingest.downloader import detect_platform
from backend.pipeline.render.renderer import build_ffmpeg_render_command
from backend.pipeline.sample_generator import verify_toolchain
from hitl.cli import parse_reply_command
from hitl.models import BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker, resolve_blocker


def test_toolchain_and_indic_libass_burnin():
    res = verify_toolchain()
    assert res["ffmpeg_available"] is True
    assert res["libass_enabled"] is True
    assert res["indic_render_ok"] is True


def test_platform_detection():
    assert detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detect_platform("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert detect_platform("https://www.instagram.com/reel/C12345/") == "instagram"
    assert detect_platform("https://www.facebook.com/watch/?v=12345") == "facebook"
    assert detect_platform("sample://sample_hinglish_podcast") == "sample"


def test_multilingual_script_and_hinglish_detection():
    assert detect_word_script("Hello") == "latn"
    assert detect_word_script("सफलता") == "deva"
    assert detect_word_script("ভাইরাল") == "beng"

    hinglish_segs = [
        {
            "text": "Dosto aaj hum baat karenge viral content creation ke baare mein",
            "words": [
                {"w": "Dosto", "s": 0.0, "e": 0.4},
                {"w": "aaj", "s": 0.4, "e": 0.7},
                {"w": "hum", "s": 0.7, "e": 1.0},
                {"w": "viral", "s": 1.0, "e": 1.4},
                {"w": "content", "s": 1.4, "e": 1.9},
            ],
        }
    ]
    det = detect_language_and_hinglish(hinglish_segs, "auto")
    assert det["language"] == "hinglish"
    assert det["script"] == "latn"

    # Verify Devanagari transliteration when script_pref="latn"
    deva_segs = [
        {
            "text": "सफलता",
            "words": [{"w": "सफलता", "s": 0.0, "e": 0.8}],
        }
    ]
    norm, sc = normalize_transcript_script(deva_segs, "hinglish", script_pref="latn")
    assert sc == "latn"
    assert detect_word_script(norm[0]["words"][0]["w"]) == "latn"


def test_caption_templates_and_ass_builder(tmp_path: Path):
    templates = ensure_starter_templates()
    assert len(templates) >= 6
    for t in templates:
        ok, err = validate_template_dict(t)
        assert ok is True, err

    tmpl = get_template_by_id("bold_pop_yellow")
    words = [
        {"w": "Dosto", "s": 10.0, "e": 10.4, "script": "latn"},
        {"w": "सफलता", "s": 10.4, "e": 10.9, "script": "deva"},
        {"w": "ভাইরাল", "s": 10.9, "e": 11.5, "script": "beng"},
    ]
    pages = chunk_words_into_pages(words, tmpl, clip_start_offset=10.0)
    assert len(pages) == 1
    # Latin upper-cased, Indic untouched
    assert pages[0]["words"][0]["w"] == "DOSTO"
    assert pages[0]["words"][1]["w"] == "सफलता"
    assert pages[0]["words"][2]["w"] == "ভাইরাল"

    ass_file = tmp_path / "out.ass"
    build_ass_subtitles(words, tmpl, ass_file, clip_start_offset=10.0)
    assert ass_file.exists()
    loaded = pysubs2.load(str(ass_file))
    assert len(loaded.events) == 3  # 1 event per active word in highlight_word mode


def test_ffmpeg_render_command_builder(tmp_path: Path):
    cmd = build_ffmpeg_render_command(
        source_mp4=tmp_path / "src.mp4",
        start=15.0,
        end=35.0,
        aspect="9:16",
        layout="fill",
        ass_path=tmp_path / "sub.ass",
        out_mp4=tmp_path / "out.mp4",
    )
    assert cmd[0] == "ffmpeg"
    assert "libx264" in cmd
    assert any("1080:1920" in part for part in cmd)


def test_hitl_blocker_lifecycle_and_secret_leak_guard(tmp_path: Path):
    b = create_or_get_blocker(
        btype=BlockerType.SECRET,
        task_id="T-4.3",
        title="Test Gemini Key Blocker",
        needed=[NeededItem(key="GEMINI_API_KEY", where=".env", how_to_get="AI Studio")],
        why="Unit test",
        root_dir=tmp_path,
    )
    assert b.id == "BLK-0001"
    assert (tmp_path / "BLOCKERS.md").exists()

    # Test secret leak guard rejects raw key in email body
    leaked = parse_reply_command(
        f"Re: [CLIPPER][{b.id}] [{b.correlation_token}]",
        "AIzaSyD-1234567890abcdefghijklmnopqrstuvwx",
    )
    assert leaked["valid"] is False
    assert leaked["error"] == "SECRET_LEAK_DETECTED"

    # Safe DONE reply parses cleanly
    clean = parse_reply_command(
        f"Re: [CLIPPER][{b.id}] [{b.correlation_token}]",
        "DONE\n\n> On Tue, CLIPPER wrote...",
    )
    assert clean["valid"] is True
    assert clean["command"] == "DONE"
    assert clean["token"] == b.correlation_token

    resolved = resolve_blocker(b.id, note="Unit test resolved", root_dir=tmp_path)
    assert resolved.status.value == "RESOLVED"


def test_end_to_end_api_pipeline_all_languages():
    client = TestClient(app)
    h = client.get("/api/v1/health")
    assert h.status_code == 200
    assert h.json()["status"] == "ok"

    for sample_id, lang in [
        ("sample_hinglish_podcast", "hinglish"),
        ("sample_bengali_tech", "bn"),
    ]:
        resp = client.post(
            "/api/v1/jobs",
            json={
                "url": f"sample://{sample_id}",
                "language": lang,
                "aspect": "9:16",
                "template_id": "bold_pop_yellow",
                "auto_mode": True,
                "run_sync": True,
            },
        )
        assert resp.status_code == 200
        job = resp.json()
        assert job["status"] == "COMPLETED"
        assert len(job["clips"]) >= 2
        top_clip = job["clips"][0]
        assert top_clip["status"] == "RENDERED"

        # Verify MP4 and SRT endpoints return real media bytes
        v_resp = client.get(top_clip["video_url"])
        assert v_resp.status_code == 200
        assert len(v_resp.content) > 5000

        s_resp = client.get(top_clip["srt_url"])
        assert s_resp.status_code == 200
        assert len(s_resp.text) > 10
