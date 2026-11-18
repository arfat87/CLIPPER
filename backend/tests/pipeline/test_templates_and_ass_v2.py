from __future__ import annotations

from pathlib import Path
from typing import Any

import pysubs2

from backend.pipeline.captions.ass_builder import (
    build_ass_from_chunks,
    build_ass_subtitle,
    burn_in_captions,
    export_subtitles_v2,
    resolve_safe_area_box,
)
from backend.pipeline.captions.chunker import chunk_words_script_aware
from backend.pipeline.captions.template_loader import (
    STARTER_V2_TEMPLATE_IDS,
    VALID_TEMPLATE_KINDS,
    CaptionTemplate,
    compute_contrast_ratio,
    detect_tofu_glyphs,
    ensure_starter_templates,
    get_template,
    get_template_by_id,
    load_templates,
    load_templates_v2,
    render_template_test_card,
    validate_template_dict,
    validate_template_v2,
)


def test_v1_template_loader_backward_compatibility() -> None:
    v1_list = ensure_starter_templates()
    assert len(v1_list) >= 6
    loaded = load_templates()
    assert len(loaded) >= 6

    tpl_dict = get_template_by_id("bold_pop_yellow")
    assert tpl_dict["id"] == "bold_pop_yellow"
    ok, err = validate_template_dict(tpl_dict)
    assert ok is True, err

    tpl_obj = get_template("bold_pop_yellow")
    assert isinstance(tpl_obj, CaptionTemplate)
    assert tpl_obj.id == "bold_pop_yellow"
    assert "latn" in tpl_obj.font_fallbacks


def test_v2_starter_template_packs_load_and_validate() -> None:
    packs = load_templates_v2()
    pack_by_id = {p.id: p for p in packs}

    for req_id in STARTER_V2_TEMPLATE_IDS:
        assert req_id in pack_by_id, f"Missing required v2 starter template: {req_id}"
        pack = pack_by_id[req_id]
        assert pack.valid is True, f"{req_id} failed validation: {pack.errors}"
        assert pack.preview_path is not None and pack.preview_path.exists()

    assert pack_by_id["hormozi_yellow"].kind == "caption"
    assert pack_by_id["clean_minimal"].kind == "caption"
    assert pack_by_id["bangla_bold"].kind == "caption"
    assert pack_by_id["bangla_bold"].scripts == ["beng", "latn"]
    assert pack_by_id["hindi_creator"].kind == "caption"
    assert pack_by_id["hindi_creator"].scripts == ["deva", "latn"]
    assert pack_by_id["podcast_2up"].kind == "layout"
    assert pack_by_id["neon_pop"].kind == "caption"


def test_validate_all_6_template_kinds_and_reject_invalid() -> None:
    assert set(VALID_TEMPLATE_KINDS) == {
        "caption",
        "layout",
        "publish",
        "brand_kit",
        "overlay",
        "preset",
    }

    # 1. caption
    cap_tpl = get_template("hormozi_yellow").raw
    ok, errs, warns = validate_template_v2(cap_tpl, kind="caption")
    assert ok is True and not errs and not warns

    # 2. layout
    lay_tpl = {
        "schema_version": 2,
        "kind": "layout",
        "meta": {"id": "fill_default", "name": "Fill Tracked", "version": 1, "license": "MIT"},
        "type": "fill",
        "params": {"headroom_pct": 40, "ema_alpha": 0.15},
    }
    ok, errs, _ = validate_template_v2(lay_tpl, kind="layout")
    assert ok is True and not errs

    # 3. overlay
    ov_tpl = {
        "schema_version": 2,
        "kind": "overlay",
        "meta": {"id": "hook_title", "name": "Hook Banner", "version": 1, "license": "MIT"},
        "type": "hook_banner",
        "text": {"source": "hook_line", "max_chars": 60},
        "position": {"anchor": "top_center", "y_percent": 14},
        "timing": {"from_s": 0.0, "to_s": 2.5},
    }
    ok, errs, _ = validate_template_v2(ov_tpl, kind="overlay")
    assert ok is True and not errs

    # 4. publish
    pub_tpl = {
        "schema_version": 2,
        "kind": "publish",
        "meta": {"id": "yt_shorts_hinglish", "name": "YT Shorts Hinglish", "version": 1, "license": "MIT"},
        "platform": "youtube_shorts",
        "language": "hinglish",
        "script": "latn",
        "title": {"template": "{{title}}"},
        "description": {"template": "{{hook}}\n{{hashtags}}"},
    }
    ok, errs, _ = validate_template_v2(pub_tpl, kind="publish")
    assert ok is True and not errs

    # 5. brand_kit
    bk_tpl = {
        "schema_version": 2,
        "kind": "brand_kit",
        "meta": {"id": "my_channel", "name": "My Channel", "version": 1, "license": "owner"},
        "palette": {"primary": "#FFE600", "secondary": "#111111", "accent": "#00E5FF"},
        "watermark": {"asset": "wm.png", "anchor": "top_right", "size_pct": 8, "opacity": 0.7},
    }
    ok, errs, _ = validate_template_v2(bk_tpl, kind="brand_kit")
    assert ok is True and not errs

    # Unsafe traversal in brand_kit asset path must fail validation
    bad_bk = dict(bk_tpl, watermark={"asset": "../secrets/drive_sa.json"})
    ok_bad, errs_bad, _ = validate_template_v2(bad_bk, kind="brand_kit")
    assert ok_bad is False and any("Unsafe asset path" in e for e in errs_bad)

    # 6. preset
    preset_tpl = {
        "schema_version": 2,
        "kind": "preset",
        "id": "hinglish_podcast_bold",
        "name": "Hinglish Podcast Bold",
        "version": 1,
        "caption": "hormozi_yellow@1",
        "layout": "podcast_2up@1",
        "brand_kit": "my_channel@1",
        "overlays": ["hook_title@1", "progress_bar@1"],
        "publish": "yt_shorts_hinglish@1",
        "render_profile": "youtube_shorts",
    }
    ok, errs, _ = validate_template_v2(preset_tpl, kind="preset")
    assert ok is True and not errs

    # Invalid template missing required fields
    ok_inv, errs_inv, _ = validate_template_v2({"schema_version": 2, "kind": "caption"})
    assert ok_inv is False and len(errs_inv) >= 1

    # Low contrast ratio warning check
    assert compute_contrast_ratio("#FFFFFF", "#000000") >= 20.0
    assert compute_contrast_ratio("#777777", "#888888") < 4.5


def test_detect_tofu_glyphs_and_render_template_test_card(tmp_path: Path) -> None:
    # Valid Latin, Devanagari, and Bengali strings have zero tofu
    assert detect_tofu_glyphs("Shipping fast wins!", "Montserrat", "latn")["has_tofu"] is False
    assert detect_tofu_glyphs("सफलता और क्षमता", "Mukta", "deva")["has_tofu"] is False
    assert detect_tofu_glyphs("অস্ত্রশস্ত্র ও শিক্ষা", "Hind Siliguri", "beng")["has_tofu"] is False

    # Replacement char \ufffd, null \x00, or Private Use Area \ue000 must be flagged as tofu
    rep_res = detect_tofu_glyphs("Broken \ufffd glyph", "Sans", "latn")
    assert rep_res["has_tofu"] is True
    assert "\ufffd" in rep_res["bad_chars"]

    pua_res = detect_tofu_glyphs("PUA \ue001 icon", "Sans", "latn")
    assert pua_res["has_tofu"] is True
    assert "\ue001" in pua_res["bad_chars"]

    null_res = detect_tofu_glyphs("Null\x00char", "Sans", "latn")
    assert null_res["has_tofu"] is True

    # Test card rendering across scripts
    for tid, script, sample in [
        ("hormozi_yellow", "latn", "SHIPPING FAST WINS"),
        ("hindi_creator", "deva", "सफलता एक दिन में"),
        ("bangla_bold", "beng", "বাংলা ক্যাপশন টেস্ট"),
    ]:
        out_png = tmp_path / f"card_{tid}_{script}.png"
        card = render_template_test_card(
            tid,
            sample_text=sample,
            script=script,
            output_path=out_png,
            resolution=(360, 640),
        )
        assert card["has_tofu"] is False
        assert card["rendered_ok"] is True
        assert out_png.exists() and out_png.stat().st_size > 200


def test_ass_builder_v2_all_6_animations_mixed_script_and_sidecars(tmp_path: Path) -> None:
    words: list[dict[str, Any]] = [
        {"w": "Dosto", "s": 0.0, "e": 0.4, "script": "latn"},
        {"w": "सफलता", "s": 0.42, "e": 0.9, "script": "deva"},
        {"w": "ভাইরাল", "s": 0.92, "e": 1.45, "script": "beng"},
    ]
    tpl = get_template("hormozi_yellow")
    chunks = chunk_words_script_aware(words, tpl.to_dict())
    assert len(chunks) == 1

    expected_tag_Markers = {
        "karaoke_fill": "\\kf",
        "highlight_word": "\\fscx115\\fscy115",
        "pop": "\\t(0,80,\\fscx115\\fscy115)\\t(80,160,\\fscx100\\fscy100)",
        "typewriter": "\\fn",
        "fade_slide": "\\fad(120,120)\\move(",
        "bounce": "\\t(0,90,\\fscy120)\\t(90,180,\\fscy100)",
    }

    for anim_style, marker in expected_tag_Markers.items():
        ass_path = tmp_path / f"anim_{anim_style}.ass"
        subs = build_ass_from_chunks(
            chunks,
            tpl,
            out_ass_path=ass_path,
            aspect="9:16",
            animation_style=anim_style,
        )
        assert ass_path.exists()
        raw_ass = ass_path.read_text(encoding="utf-8")
        assert marker in raw_ass, f"Expected marker {marker!r} in {anim_style} ASS output"
        # Verify mixed-script inline \fn switches for latn, deva, beng
        assert "{\\fnMontserrat}" in raw_ass
        assert "{\\fnMukta}" in raw_ass
        assert "{\\fnHind Siliguri}" in raw_ass
        assert subs.info["PlayResX"] == "1080"
        assert subs.info["PlayResY"] == "1920"

    # Safe area clamping check (y_percent=95 must clamp into safe box <= 75% for shorts_default)
    safe_box = resolve_safe_area_box(aspect="9:16", safe_area_profile="shorts_default", y_percent=95.0)
    assert safe_box["clamped_y_percent"] <= 75
    assert safe_box["MarginV"] >= int(1920 * 0.25)

    # Sidecar export (.ass, .srt, .vtt)
    sidecars = export_subtitles_v2(
        chunks,
        "hormozi_yellow",
        tmp_path / "sidecars",
        stem="clip_01",
        aspect="9:16",
    )
    assert sidecars["ass"].exists()
    assert sidecars["srt"].exists()
    assert sidecars["vtt"].exists()
    srt_txt = sidecars["srt"].read_text(encoding="utf-8")
    vtt_txt = sidecars["vtt"].read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in srt_txt
    assert "DOSTO सफलता ভাইরাল" in srt_txt
    assert vtt_txt.startswith("WEBVTT")
    assert "00:00:00.000 -->" in vtt_txt

    # Verify v1 build_ass_subtitle alias and burn_in_captions helper
    v1_ass = tmp_path / "v1_alias.ass"
    build_ass_subtitle(words, get_template_by_id("bold_pop_yellow"), v1_ass)
    assert v1_ass.exists()
    loaded_v1 = pysubs2.load(str(v1_ass))
    assert len(loaded_v1.events) == 3

    # Test burn_in_captions on a tiny synthetic video
    import subprocess

    tiny_src = tmp_path / "tiny_src.mp4"
    tiny_out = tmp_path / "tiny_burned.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x0f172a:s=360x640:r=10:d=0.6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=0.6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(tiny_src),
        ],
        check=True,
        capture_output=True,
    )
    burned = burn_in_captions(tiny_src, v1_ass, tiny_out)
    assert burned.exists() and burned.stat().st_size > 500
