from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from backend.pipeline.render.clip_plan_ops import (
    build_cut_list,
    compute_clip_plan_hash,
    extract_overlay_descriptors,
    map_source_to_output_time,
    remap_words_to_output_timeline,
)
from backend.pipeline.render.ffmpeg_cmd import (
    build_ffmpeg_render_command,
    detect_nvenc_available,
    get_render_profile,
    load_render_config,
    parse_ffmpeg_progress_line,
)
from backend.pipeline.render.renderer import render_clip, render_clip_v2


def test_map_source_to_output_time_single_timeline_invariants() -> None:
    # Clip starts at 10.0s, two cut intervals: [12.0, 13.0] (1.0s cut) and [15.0, 16.5] (1.5s cut)
    cuts = [(12.0, 13.0), (15.0, 16.5)]
    clip_start = 10.0

    # Before clip_start -> clamped to 0.0
    assert map_source_to_output_time(8.0, cuts, clip_start=clip_start) == 0.0
    assert map_source_to_output_time(10.0, cuts, clip_start=clip_start) == 0.0

    # Before first cut: t_out = t_src - 10.0
    assert map_source_to_output_time(11.5, cuts, clip_start=clip_start) == 1.5

    # Inside first cut [12.0, 13.0]: collapses to 2.0s
    assert map_source_to_output_time(12.0, cuts, clip_start=clip_start) == 2.0
    assert map_source_to_output_time(12.6, cuts, clip_start=clip_start) == 2.0
    assert map_source_to_output_time(13.0, cuts, clip_start=clip_start) == 2.0

    # Between first and second cut: shifted by -1.0s
    assert map_source_to_output_time(14.0, cuts, clip_start=clip_start) == 3.0

    # After second cut: shifted by -(1.0 + 1.5) = -2.5s
    assert map_source_to_output_time(17.5, cuts, clip_start=clip_start) == 5.0

    # Monotonicity property across 100 timestamps
    pts = [10.0 + i * 0.1 for i in range(100)]
    mapped = [map_source_to_output_time(t, cuts, clip_start=clip_start) for t in pts]
    for i in range(1, len(mapped)):
        assert mapped[i] >= mapped[i - 1]


def test_build_cut_list_remove_fillers_and_trim_silence_keeps_captions_in_sync() -> None:
    clip_plan: dict[str, Any] = {
        "schema_version": 1,
        "clip_id": "clip_sync_01",
        "source": {"media": "media/mezzanine.mp4"},
        "segments": [{"start": 10.0, "end": 18.0}],
        "ops": [
            {"op": "remove_fillers", "enabled": True},
            {"op": "trim_silence", "enabled": True, "max_gap_s": 0.6},
        ],
        "layout": {"template": "fill_default@1", "force": None},
        "captions": {"template": "hormozi_yellow@1", "script": "latn", "overrides": {}},
        "overlays": [
            {"template": "hook_banner@1", "text": "Wait for {{topic}}", "t": [10.0, 14.5], "source_time": True},
            {"template": "progress_bar@1"},
            {"template": "watermark@1", "asset": "logo.png"},
            {"template": "lower_third@1", "text": "Host"},
        ],
        "output": {"aspect": "9:16", "profile": "youtube_shorts"},
    }

    # Words with a 0.50s filler "um" at [11.0, 11.5] and a 1.25s silence pause between [12.5, 13.75]
    words = [
        {"w": "Welcome", "s": 10.0, "e": 10.9},
        {"w": "um", "s": 11.0, "e": 11.5, "flags": ["filler"]},  # 0.50s filler -> cut [11.04, 11.46] (0.42s cut)
        {"w": "dosto", "s": 11.6, "e": 12.5},
        # 1.25s pause [12.5, 13.75] > 0.6s -> collapsed to 0.25s -> 1.00s cut [12.625, 13.625]
        {"w": "viral", "s": 13.75, "e": 14.5},
        {"w": "secret!", "s": 14.55, "e": 15.2},
    ]

    cut_res = build_cut_list(clip_plan, transcript_words=words)
    assert cut_res.removed_fillers_count == 1
    assert cut_res.trimmed_silences_count == 1
    assert cut_res.crossfade_ms == 40
    assert len(cut_res.cut_list) == 2
    assert len(cut_res.keep_intervals) == 3

    # Total removed = 0.42s (filler with 40ms pads) + 1.00s (silence trim) = 1.42s
    total_removed = sum(e - s for s, e in cut_res.cut_list)
    assert abs(total_removed - 1.42) < 1e-2
    assert abs(cut_res.output_duration - (8.0 - 1.42)) < 1e-2

    remapped = remap_words_to_output_timeline(
        words,
        cut_res.cut_list,
        clip_start=cut_res.clip_start,
        clip_end=cut_res.clip_end,
    )
    # Filler "um" is dropped; remaining 4 words are shifted into sync with video keep intervals
    assert [w["w"] for w in remapped] == ["Welcome", "dosto", "viral", "secret!"]
    assert remapped[0]["s"] == 0.0
    # "dosto" starts at 11.6 (after 0.42s filler cut) -> 1.6 - 0.42 = 1.18s
    assert abs(remapped[1]["s"] - 1.18) < 1e-2
    # "viral" starts at 13.75 (after 0.42s filler + 1.00s silence = 1.42s removed) -> 3.75 - 1.42 = 2.33s
    assert abs(remapped[2]["s"] - 2.33) < 1e-2

    # Overlay descriptors also map through the exact same timeline mapping function
    overlays = extract_overlay_descriptors(
        clip_plan,
        cut_list=cut_res.cut_list,
        clip_start=cut_res.clip_start,
        output_duration=cut_res.output_duration,
        variables={"topic": "Growth"},
    )
    assert len(overlays) == 4
    by_kind = {ov.kind: ov for ov in overlays}
    assert set(by_kind.keys()) == {"hook_banner", "progress_bar", "watermark", "lower_third"}
    assert by_kind["hook_banner"].text == "Wait for Growth"
    # Source time 14.5 mapped via cut_list -> 4.5 - 1.42 = 3.08s
    assert abs(by_kind["hook_banner"].end - 3.08) < 1e-2
    assert by_kind["progress_bar"].end == cut_res.output_duration


def test_render_profiles_and_ffmpeg_command_builder(tmp_path: Path) -> None:
    cfg = load_render_config()
    profiles = cfg["profiles"]
    for req_profile in ("youtube_shorts", "reels", "tiktok", "square_1x1", "portrait_4x5", "draft_fast"):
        assert req_profile in profiles
        p = get_render_profile(req_profile)
        assert p["loudnorm"] == "loudnorm=I=-16:TP=-1.5:LRA=11"
        assert p["movflags"] == "+faststart"

    assert get_render_profile("square_1x1")["w"] == 1080
    assert get_render_profile("square_1x1")["h"] == 1080
    assert get_render_profile("portrait_4x5")["h"] == 1350
    assert get_render_profile("draft_fast")["w"] == 540
    assert get_render_profile("draft_fast")["crf"] == 28

    # CPU command with multi-interval cut list, progress_bar overlay, watermark, loudnorm, and -progress pipe:1
    wm_png = tmp_path / "wm.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=64x64:d=0.1",
            "-frames:v",
            "1",
            str(wm_png),
        ],
        check=True,
        capture_output=True,
    )

    ass_file = tmp_path / "captions.ass"
    ass_file.write_text("[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n", encoding="utf-8")

    cmd_cpu = build_ffmpeg_render_command(
        source_mp4=tmp_path / "mezzanine.mp4",
        start=0.0,
        end=10.0,
        aspect="9:16",
        layout="fill",
        ass_path=ass_file,
        out_mp4=tmp_path / "out_cpu.mp4",
        profile="youtube_shorts",
        keep_intervals=[(0.0, 3.0), (4.0, 8.0)],
        overlays=[
            {"kind": "progress_bar", "start": 0.0, "end": 7.0, "color": "#FFE600", "height_px": 10},
            {"kind": "watermark", "start": 0.0, "end": 7.0, "asset": str(wm_png), "position": "top_right"},
        ],
        loudnorm=True,
        use_nvenc=False,
        progress_pipe=True,
    )
    assert cmd_cpu[0] == "ffmpeg"
    assert "-filter_complex" in cmd_cpu
    fc_str = cmd_cpu[cmd_cpu.index("-filter_complex") + 1]
    assert "trim=start=0.000:end=3.000" in fc_str
    assert "atrim=start=4.000:end=8.000" in fc_str
    assert "concat=n=2:v=1:a=0" in fc_str
    assert "drawbox=" in fc_str
    assert "overlay=W-w-36:36" in fc_str
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in fc_str
    assert "libx264" in cmd_cpu
    assert "+faststart" in cmd_cpu
    assert "-progress" in cmd_cpu and "pipe:1" in cmd_cpu

    # NVENC GPU command check
    cmd_gpu = build_ffmpeg_render_command(
        source_mp4=tmp_path / "mezzanine.mp4",
        start=0.0,
        end=5.0,
        aspect="9:16",
        layout="fit_blur",
        ass_path=ass_file,
        out_mp4=tmp_path / "out_gpu.mp4",
        profile="reels",
        use_nvenc=True,
        loudnorm=True,
    )
    assert "h264_nvenc" in cmd_gpu
    assert isinstance(detect_nvenc_available(), bool)


def test_parse_ffmpeg_progress_line() -> None:
    assert parse_ffmpeg_progress_line("frame=15", 10.0) is None
    assert parse_ffmpeg_progress_line("out_time_us=2500000", 10.0) == 0.25
    assert parse_ffmpeg_progress_line("out_time_ms=5000000", 10.0) == 0.5
    assert parse_ffmpeg_progress_line("out_time=00:00:07.500000", 10.0) == 0.75
    assert parse_ffmpeg_progress_line("out_time=00:00:15.000000", 10.0) == 1.0
    assert parse_ffmpeg_progress_line("progress=continue", 10.0) is None
    assert parse_ffmpeg_progress_line("progress=end", 10.0) == 1.0


def test_render_clip_v2_end_to_end_with_cuts_and_progress(tmp_path: Path) -> None:
    # Create a 3.0s 16:9 synthetic source video + audio
    src_mp4 = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x1e293b:s=640x360:r=20:d=3.0",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:sample_rate=16000:duration=3.0",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src_mp4),
        ],
        check=True,
        capture_output=True,
    )

    clip_plan: dict[str, Any] = {
        "schema_version": 1,
        "clip_id": "clip_v2_test",
        "source": {"media": str(src_mp4)},
        "segments": [{"start": 0.2, "end": 2.8}],
        "ops": [
            {"op": "remove_fillers", "enabled": True},
            {"op": "trim_silence", "enabled": True, "max_gap_s": 0.6},
        ],
        "layout": {"template": "fill_default@1", "force": "fill"},
        "captions": {"template": "hormozi_yellow@1", "script": "latn", "overrides": {}},
        "overlays": [
            {"template": "progress_bar@1"},
            {"template": "hook_banner@1", "text": "Fast Hook", "t": [0.0, 1.0]},
        ],
        "audio": {"loudness_lufs": -16.0},
        "output": {"aspect": "9:16", "profile": "draft_fast"},
    }

    words = [
        {"w": "Ship", "s": 0.25, "e": 0.7},
        {"w": "um", "s": 0.8, "e": 1.2, "flags": ["filler"]},
        {"w": "fast", "s": 1.3, "e": 1.8},
        {"w": "today!", "s": 1.85, "e": 2.6},
    ]

    progress_ratios: list[float] = []
    out_dir = tmp_path / "render_v2_out"
    res = render_clip_v2(
        clip_plan,
        source_mp4=src_mp4,
        out_dir=out_dir,
        transcript_words=words,
        progress_callback=progress_ratios.append,
        use_nvenc=False,
    )

    assert Path(res["mp4_path"]).exists() and Path(res["mp4_path"]).stat().st_size > 1000
    assert Path(res["ass_path"]).exists()
    assert Path(res["srt_path"]).exists()
    assert Path(res["vtt_path"]).exists()
    assert Path(res["thumb_path"]).exists()
    assert Path(res["meta_path"]).exists()
    assert progress_ratios[0] == 0.0
    assert progress_ratios[-1] == 1.0
    assert res["plan_hash"] == compute_clip_plan_hash(
        clip_plan,
        template_versions={"caption": "hormozi_yellow@1", "layout": "fill_default@1"},
    )

    meta = json.loads(Path(res["meta_path"]).read_text(encoding="utf-8"))
    assert meta["resolution"] == [540, 960]
    assert meta["cut_list"]["removed_fillers_count"] == 1

    # Also test v1 render_clip alias backward compatibility
    v1_job_dir = tmp_path / "v1_job"
    (v1_job_dir / "source").mkdir(parents=True)
    (v1_job_dir / "source" / "source.mp4").write_bytes(src_mp4.read_bytes())
    v1_res = render_clip(
        job_dir=v1_job_dir,
        clip_id="c1",
        start=0.0,
        end=1.5,
        words=words,
        template_id="bold_pop_yellow",
    )
    assert Path(v1_res["mp4_path"]).exists()
    assert Path(v1_res["srt_path"]).exists()
