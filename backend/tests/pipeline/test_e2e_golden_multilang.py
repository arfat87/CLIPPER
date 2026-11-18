from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from backend.app.config import ROOT_DIR
from backend.pipeline.analysis import (
    build_thought_units,
    build_video_profile,
    compute_speech_rate_1hz,
    segment_topics,
)
from backend.pipeline.asr.aligner import align_transcript, extract_words
from backend.pipeline.asr.language import detect_language_multiwindow
from backend.pipeline.asr.script import (
    normalize_script_tokens,
    restore_punctuation_and_casing,
)
from backend.pipeline.captions.chunker import chunk_words_script_aware
from backend.pipeline.captions.drive_template_importer import (
    DriveTemplateImportError,
    import_owner_templates_from_drive,
)
from backend.pipeline.highlights.boundaries import validate_clip_plan
from backend.pipeline.highlights.eval_harness import load_golden_highlights
from backend.pipeline.highlights.ranker import run_highlight_engine_v2
from backend.pipeline.publish.builder import (
    build_publish_pack,
    validate_publish_pack,
)
from backend.pipeline.reframe.layouts import build_reframe_filter_spec
from backend.pipeline.reframe.tracker import FaceDetection, build_reframe_path

SAMPLE_FILES: dict[str, tuple[str, str]] = {
    "en": ("sample_english_keynote.transcript.json", "latn"),
    "hi": ("sample_hindi_motivation.transcript.json", "deva"),
    "hinglish": ("sample_hinglish_podcast.transcript.json", "latn"),
    "bn": ("sample_bengali_tech.transcript.json", "beng"),
}


def _valid_v2_caption_template(tpl_id: str = "owner_custom_yellow") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "meta": {
            "id": tpl_id,
            "name": "Owner Custom Yellow",
            "version": 1,
            "author": "Owner",
            "license": "Proprietary",
            "tags": ["custom", "drive"],
        },
        "aspects": ["9:16", "1:1"],
        "fonts": {
            "latn": {"family": "Montserrat", "weight": 800},
            "deva": {"family": "Noto Sans Devanagari", "weight": 700},
            "beng": {"family": "Noto Sans Bengali", "weight": 700},
        },
        "text": {"size_px": 72, "case": "upper"},
        "colors": {
            "primary": "#FFFFFF",
            "active": "#FFE600",
            "outline": "#000000",
            "shadow": "#000000CC",
            "box": None,
        },
        "stroke": {"outline_px": 5, "shadow_px": 2},
        "layout": {
            "anchor": "lower_third",
            "y_percent": 74,
            "align": "center",
            "max_words_per_line": 4,
            "max_lines": 2,
        },
        "animation": {"mode": "pages", "active_word": "highlight", "in": "pop", "out": "none"},
    }


@pytest.mark.parametrize("lang", ["en", "hi", "hinglish", "bn"])
def test_e2e_golden_pipeline_per_language(lang: str, tmp_path: Path):
    """T-11.1: Full multi-language golden pipeline verification across en, hi, hinglish, and bn."""
    sample_filename, expected_script = SAMPLE_FILES[lang]
    sample_path = ROOT_DIR / "assets" / "samples" / sample_filename
    assert sample_path.exists()
    sample_transcript = json.loads(sample_path.read_text(encoding="utf-8"))

    # Also load corresponding 15-min golden video from golden_highlights.json
    golden_dataset = load_golden_highlights()
    golden_video = next(v for v in golden_dataset["videos"] if v["language"] == lang)

    # 1. Language & Script Tagging (T-3.2, T-3.4)
    lang_res = detect_language_multiwindow(
        sample_transcript["segments"],
        declared_lang="auto",
    )
    assert lang_res.language == lang
    assert lang_res.script == expected_script

    norm_segs, resolved_script = normalize_script_tokens(
        sample_transcript["segments"],
        language=lang,
        script_pref=expected_script,
    )
    assert resolved_script == expected_script
    restored_segs = restore_punctuation_and_casing(norm_segs, language=lang)
    assert len(restored_segs) == len(sample_transcript["segments"])

    # 2. Word Alignment (T-3.3)
    aligned = align_transcript(
        {
            "schema_version": 1,
            "language": lang,
            "script": expected_script,
            "duration": float(sample_transcript.get("duration", 75.0)),
            "engine": "faster-whisper",
            "segments": restored_segs,
        },
        wav_path=tmp_path / "dummy.wav",
        enabled=False,
    )
    aligned_words = extract_words(aligned)
    assert len(aligned_words) >= 15

    # 3. Video Analysis & VideoProfile Assembly (T-4.1..T-4.4)
    thought_units = build_thought_units(golden_video["transcript"])
    assert len(thought_units) >= 5
    sr_1hz = compute_speech_rate_1hz(
        golden_video["transcript"],
        duration_s=float(golden_video["duration"]),
    )
    assert len(sr_1hz) == int(golden_video["duration"])

    topics_doc = segment_topics(golden_video["transcript"], max_topics=6)
    assert len(topics_doc["topics"]) >= 1

    video_profile = build_video_profile(
        golden_video["transcript"],
        signals=golden_video["signals"],
        topics=topics_doc,
    )
    assert video_profile["schema_version"] == 1
    assert video_profile["language"] == lang
    assert video_profile["genre"] in {
        "podcast",
        "interview",
        "solo_talk",
        "lecture",
        "screenshare_tutorial",
        "gaming",
        "vlog",
        "news",
        "other",
    }

    # 4. Highlight Engine v2 & Boundary Snapping / ClipPlan Creation (T-5.2..T-5.5)
    job_dir = tmp_path / f"job_{lang}"
    selection = run_highlight_engine_v2(
        job_dir=job_dir,
        transcript=golden_video["transcript"],
        genre=golden_video["genre"],
        signals_artefact=golden_video["signals"],
        topics=golden_video["topics"],
        score_mode="signals_only",
        aspect="9:16",
    )
    assert len(selection.clips) >= 3
    top_clip = selection.clips[0]
    validate_clip_plan(top_clip["clip_plan"])
    assert top_clip["clip_plan"]["captions"]["script"] == expected_script

    # 5. Smart Reframe Tracker & Layout Engine (T-6.1, T-6.2)
    clip_dur = min(10.0, float(top_clip["duration"]))
    detections_by_frame = [
        (
            round(t * 0.2, 3),
            [
                FaceDetection(
                    bbox_norm=(0.38, 0.22, 0.24, 0.36),
                    confidence=0.95,
                    mouth_motion=0.6,
                    face_id=1,
                )
            ],
        )
        for t in range(int(clip_dur / 0.2) + 1)
    ]
    reframe_path = build_reframe_path(
        detections_by_frame=detections_by_frame,
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
    )
    assert len(reframe_path.keyframes) >= 5
    filter_spec = build_reframe_filter_spec(
        reframe_path,
        work_dir=job_dir / "clips" / top_clip["clip_id"],
    )
    assert filter_spec.out_w == 1080
    assert filter_spec.out_h == 1920
    assert "crop" in filter_spec.filtergraph or "scale" in filter_spec.filtergraph

    # 6. Script-Aware Caption Chunker (T-6.4)
    clip_words = top_clip.get("words") or aligned_words
    caption_pages = chunk_words_script_aware(
        clip_words,
        clip_start_offset=float(top_clip["start"]) if top_clip.get("words") else 0.0,
    )
    assert len(caption_pages) >= 1
    for p_idx in range(1, len(caption_pages)):
        assert caption_pages[p_idx - 1].end <= caption_pages[p_idx].start

    # 7. Publish Pack Builder (T-7.1)
    pack_dir = job_dir / "clips" / top_clip["clip_id"] / "publish"
    pack = build_publish_pack(
        clip_id=top_clip["clip_id"],
        transcript=golden_video["transcript"],
        clip_start=float(top_clip["start"]),
        clip_end=float(top_clip["end"]),
        platform="youtube_shorts",
        language=lang,
        selector_meta={
            "title": top_clip["title"],
            "hook_line": top_clip["hook_line"],
            "hashtags": top_clip["hashtags"],
            "reason": top_clip["reason"],
        },
        out_dir=pack_dir,
    )
    is_valid, _ = validate_publish_pack(pack, platform="youtube_shorts")
    assert is_valid is True
    assert pack["language"] == lang
    assert (pack_dir / "publish_pack.json").exists()
    assert (pack_dir / "publish_pack.md").exists()
    assert (pack_dir / "captions.srt").exists()
    assert (pack_dir / "captions.vtt").exists()
    assert (pack_dir / "transcript.txt").exists()


def test_t68_owner_drive_template_importer_folders_zips_and_security(tmp_path: Path):
    """T-6.8: Import owner templates from Drive 01_Templates (folder, JSON, .cliptpl.zip) with safety checks."""
    drive_dir = tmp_path / "01_Templates"
    target_dir = tmp_path / "imported_templates"
    drive_dir.mkdir(parents=True)

    # 1. Folder template
    folder_tpl = drive_dir / "owner_folder_tpl"
    folder_tpl.mkdir()
    (folder_tpl / "template.json").write_text(
        json.dumps(_valid_v2_caption_template("owner_folder_tpl")),
        encoding="utf-8",
    )
    (folder_tpl / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    # 2. Standalone JSON template
    (drive_dir / "owner_json_tpl.json").write_text(
        json.dumps(_valid_v2_caption_template("owner_json_tpl")),
        encoding="utf-8",
    )

    # 3. Packaged .cliptpl.zip archive
    zip_path = drive_dir / "owner_zip_tpl.cliptpl.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "template.json",
            json.dumps(_valid_v2_caption_template("owner_zip_tpl")),
        )

    imported = import_owner_templates_from_drive(drive_dir, target_dir)
    imported_ids = {t["id"] for t in imported}
    assert imported_ids == {"owner_folder_tpl", "owner_json_tpl", "owner_zip_tpl"}

    for doc in imported:
        assert doc["status"] == "draft"
        assert doc["source"] == "drive"
        assert "imported_at" in doc
        saved_path = target_dir / doc["id"] / "template.json"
        assert saved_path.exists()

    assert (target_dir / "owner_folder_tpl" / "preview.png").exists()

    # 4. Verify malicious zip-slip path traversal archive is rejected
    bad_drive_dir = tmp_path / "bad_drive"
    bad_drive_dir.mkdir()
    bad_zip = bad_drive_dir / "evil.cliptpl.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../../escaped.txt", "pwned")
        zf.writestr(
            "template.json",
            json.dumps(_valid_v2_caption_template("evil_zip_tpl")),
        )

    with pytest.raises(DriveTemplateImportError, match="Path traversal"):
        import_owner_templates_from_drive(bad_drive_dir, target_dir)
