from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend.pipeline.reframe import (
    OUTPUT_DIMENSIONS,
    ReframeError,
    ReframeFilterSpec,
    ReframeKeyframe,
    ReframePath,
    build_fill_filtergraph,
    build_fit_blur_filtergraph,
    build_reframe_filter_spec,
    build_segment_crops,
    build_sendcmd_script,
    compute_clamped_crop_rect,
    expand_sendcmd_entries,
    resolve_output_dimensions,
)


def _make_three_band_video(
    dest: Path,
    *,
    width: int = 640,
    height: int = 360,
    duration: float = 1.2,
    fps: int = 10,
) -> Path:
    """Create a synthetic 16:9 video with 3 distinct vertical color bands + checkerboard texture.

    - Left third [0, W/3): Bright Red (R~230, G~20, B~20)
    - Center third [W/3, 2W/3): High-contrast Green/White vertical stripes for blur vs sharpness check
    - Right third [2W/3, W): Bright Blue (R~20, G~30, B~235)
    """
    band_w = width // 3
    right_w = width - 2 * band_w
    stripe_w = max(8, band_w // 8)
    filter_complex = (
        f"[1:v]drawgrid=w={stripe_w}:h={stripe_w}:t=4:c=white[center_textured];"
        f"[0:v][center_textured][2:v]hstack=inputs=3[v]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0xe61414:s={band_w}x{height}:r={fps}:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x10b981:s={band_w}x{height}:r={fps}:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x141eeb:s={right_w}x{height}:r={fps}:d={duration}",
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _probe_stream(video_path: Path) -> dict[str, object]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,pix_fmt,sample_aspect_ratio",
            "-print_format",
            "json",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(proc.stdout)
    return dict(data["streams"][0])


def _decode_frame_rgb(video_path: Path, timestamp: float, width: int, height: int) -> np.ndarray:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    expected = width * height * 3
    assert len(proc.stdout) >= expected
    return np.frombuffer(proc.stdout[:expected], dtype=np.uint8).reshape((height, width, 3)).copy()


def test_output_dimensions_and_resolution_resolver() -> None:
    assert OUTPUT_DIMENSIONS == {
        "9:16": (1080, 1920),
        "1:1": (1080, 1080),
        "4:5": (1080, 1350),
    }
    for aspect, expected in OUTPUT_DIMENSIONS.items():
        assert resolve_output_dimensions(aspect) == expected

    # Custom target sizes are quantized down to even dimensions
    assert resolve_output_dimensions("9:16", target_size=(541, 961)) == (540, 960)

    with pytest.raises(ReframeError):
        resolve_output_dimensions("16:9")

    with pytest.raises(ReframeError):
        resolve_output_dimensions("9:16", target_size=(1, 100))


def test_golden_fit_blur_filtergraphs_across_aspects() -> None:
    fg_9_16 = build_fit_blur_filtergraph(out_aspect="9:16", blur_filter="boxblur")
    assert fg_9_16 == (
        "split=2[bg][fg];"
        "[bg]scale=1166:2074:force_original_aspect_ratio=increase:flags=bicubic,"
        "crop=1080:1920:(iw-ow)/2:(ih-oh)/2:exact=1,"
        "boxblur=20:5[bg_blur];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic[fg_fit];"
        "[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )

    fg_1_1 = build_fit_blur_filtergraph(out_aspect="1:1", blur_filter="gblur", gblur_sigma=20.0)
    assert fg_1_1 == (
        "split=2[bg][fg];"
        "[bg]scale=1166:1166:force_original_aspect_ratio=increase:flags=bicubic,"
        "crop=1080:1080:(iw-ow)/2:(ih-oh)/2:exact=1,"
        "gblur=sigma=20.0[bg_blur];"
        "[fg]scale=1080:1080:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic[fg_fit];"
        "[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )

    fg_4_5 = build_fit_blur_filtergraph(out_aspect="4:5", blur_filter="boxblur")
    assert fg_4_5 == (
        "split=2[bg][fg];"
        "[bg]scale=1166:1458:force_original_aspect_ratio=increase:flags=bicubic,"
        "crop=1080:1350:(iw-ow)/2:(ih-oh)/2:exact=1,"
        "boxblur=20:5[bg_blur];"
        "[fg]scale=1080:1350:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic[fg_fit];"
        "[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def test_golden_fill_filtergraph_and_sendcmd_script_with_scene_cut(tmp_path: Path) -> None:
    path = ReframePath(
        schema_version=1,
        fps=30,
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
        keyframes=[
            ReframeKeyframe(t=0.0, cx=0.20, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=0.2, cx=0.20, cy=0.50, zoom=1.0, layout="fill"),
            # Cut at t=0.3s: jumps to right side cx=0.80 without panning across the cut
            ReframeKeyframe(t=0.3, cx=0.80, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=0.5, cx=0.80, cy=0.50, zoom=1.0, layout="fill"),
        ],
        cuts=[0.3],
    )

    script = build_sendcmd_script(path, sample_hz=10.0)
    expected_lines = [
        "0.000 [enter] crop@reframe_crop w 608, crop@reframe_crop h 1080, crop@reframe_crop x 80, crop@reframe_crop y 0;",
        "0.100 [enter] crop@reframe_crop w 608, crop@reframe_crop h 1080, crop@reframe_crop x 80, crop@reframe_crop y 0;",
        "0.200 [enter] crop@reframe_crop w 608, crop@reframe_crop h 1080, crop@reframe_crop x 80, crop@reframe_crop y 0;",
        "0.300 [enter] crop@reframe_crop w 608, crop@reframe_crop h 1080, crop@reframe_crop x 1232, crop@reframe_crop y 0;",
        "0.400 [enter] crop@reframe_crop w 608, crop@reframe_crop h 1080, crop@reframe_crop x 1232, crop@reframe_crop y 0;",
        "0.500 [enter] crop@reframe_crop w 608, crop@reframe_crop h 1080, crop@reframe_crop x 1232, crop@reframe_crop y 0;",
    ]
    assert script.strip().splitlines() == expected_lines

    # Also test cut falling *between* two keyframes (e.g. cut at 0.25 between 0.2 and 0.4)
    mid_cut_path = ReframePath(
        schema_version=1,
        fps=20,
        source_w=1920,
        source_h=1080,
        out_aspect="1:1",
        keyframes=[
            ReframeKeyframe(t=0.0, cx=0.10, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=0.4, cx=0.90, cy=0.50, zoom=1.0, layout="fill"),
        ],
        cuts=[0.2],
    )
    entries = expand_sendcmd_entries(mid_cut_path, sample_hz=10.0)
    by_t = {e.t: e for e in entries}
    # Before cut (t=0.1): strictly holds pre-cut keyframe (x=0), never interpolated toward x=840
    assert by_t[0.0].x == 0
    assert by_t[0.1].x == 0
    # At/after cut (t=0.2, 0.3, 0.4): jumps immediately to post-cut position (x=840)
    assert by_t[0.2].x == 840
    assert by_t[0.3].x == 840
    assert by_t[0.4].x == 840

    static_fill = build_fill_filtergraph(path)
    assert static_fill == (
        "crop@reframe_crop=w=608:h=1080:x=80:y=0:exact=1,"
        "scale=1080:1920:flags=bicubic,"
        "setsar=1"
    )

    spec = build_reframe_filter_spec(path, work_dir=tmp_path)
    assert isinstance(spec, ReframeFilterSpec)
    assert spec.layout == "fill"
    assert spec.out_w == 1080
    assert spec.out_h == 1920
    assert spec.sendcmd_path is not None and spec.sendcmd_path.exists()
    assert spec.filtergraph == (
        f"sendcmd=f='{spec.sendcmd_path.as_posix()}',"
        "crop@reframe_crop=w=608:h=1080:x=80:y=0:exact=1,"
        "scale=1080:1920:flags=bicubic,"
        "setsar=1"
    )


def test_segment_crops_and_mixed_span_layouts(tmp_path: Path) -> None:
    mixed_path = ReframePath(
        schema_version=1,
        fps=30,
        source_w=1920,
        source_h=1080,
        out_aspect="4:5",
        keyframes=[
            ReframeKeyframe(t=0.0, cx=0.25, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=1.0, cx=0.25, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=1.2, cx=0.50, cy=0.50, zoom=1.0, layout="fit_blur"),
            ReframeKeyframe(t=2.4, cx=0.50, cy=0.50, zoom=1.0, layout="fit_blur"),
        ],
        cuts=[1.2],
    )

    segments = build_segment_crops(mixed_path, max_segment_duration_s=0.6, clip_duration=2.4)
    assert len(segments) == 4
    assert [s.layout for s in segments] == ["fill", "fill", "fit_blur", "fit_blur"]
    assert segments[0].start == 0.0 and segments[1].end == 1.2
    assert segments[2].start == 1.2 and segments[3].end == 2.4
    assert all((s.out_w, s.out_h) == (1080, 1350) for s in segments)
    assert all(0 <= s.x and s.x + s.w <= 1920 and 0 <= s.y and s.y + s.h <= 1080 for s in segments)

    spec = build_reframe_filter_spec(mixed_path, work_dir=tmp_path)
    assert spec.layout == "mixed"
    assert spec.out_w == 1080 and spec.out_h == 1350
    assert "overlay=0:0:enable='gte(t,1.200)',setsar=1" in spec.filtergraph

    # Forced override to fit_blur
    forced_blur = build_reframe_filter_spec(mixed_path, layout_override="fit_blur", work_dir=tmp_path)
    assert forced_blur.layout == "fit_blur"
    assert forced_blur.sendcmd_path is None
    assert forced_blur.filtergraph == build_fit_blur_filtergraph(out_aspect="4:5")

    # Low-res source triggers upscaling warning
    low_res_path = ReframePath(
        schema_version=1,
        fps=30,
        source_w=640,
        source_h=360,
        out_aspect="9:16",
        keyframes=[ReframeKeyframe(t=0.0, cx=0.5, cy=0.5, zoom=1.0, layout="fill")],
    )
    low_spec = build_reframe_filter_spec(low_res_path)
    assert any("upscal" in w.lower() and "720" in w for w in low_spec.warnings)


def test_crop_clamping_and_even_quantization_bounds() -> None:
    for aspect in OUTPUT_DIMENSIONS:
        for cx in (-1.0, 0.0, 0.15, 0.5, 0.85, 1.0, 2.0):
            for cy in (-1.0, 0.0, 0.5, 1.0, 2.0):
                for zoom in (0.8, 1.0, 1.12, 1.5):
                    x, y, w, h = compute_clamped_crop_rect(cx, cy, zoom, 1920, 1080, aspect)
                    assert w % 2 == 0 and h % 2 == 0
                    assert 0 <= x <= 1920 - w
                    assert 0 <= y <= 1080 - h
                    assert w >= 2 and h >= 2


def test_real_ffmpeg_visual_snapshot_fill_and_fit_blur(tmp_path: Path) -> None:
    src_video = _make_three_band_video(tmp_path / "bands_16x9.mp4", width=640, height=360, duration=1.0, fps=10)
    target_size = (270, 480)  # Fast 9:16 render size (exact 9:16 even dims)

    # 1. Render `fill` tracking from Left (Red band, t=0.0..0.4) -> cut at t=0.5 -> Right (Blue band, t=0.5..1.0)
    fill_path = ReframePath(
        schema_version=1,
        fps=10,
        source_w=640,
        source_h=360,
        out_aspect="9:16",
        keyframes=[
            ReframeKeyframe(t=0.0, cx=0.12, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=0.4, cx=0.12, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=0.5, cx=0.88, cy=0.50, zoom=1.0, layout="fill"),
            ReframeKeyframe(t=1.0, cx=0.88, cy=0.50, zoom=1.0, layout="fill"),
        ],
        cuts=[0.5],
    )
    fill_spec = build_reframe_filter_spec(
        fill_path,
        target_size=target_size,
        work_dir=tmp_path,
        sendcmd_filename="fill_test.sendcmd",
    )
    fill_out = tmp_path / "out_fill.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src_video),
            "-vf",
            fill_spec.filtergraph,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(fill_out),
        ],
        check=True,
        capture_output=True,
    )

    fill_meta = _probe_stream(fill_out)
    assert int(fill_meta["width"]) == 270
    assert int(fill_meta["height"]) == 480
    assert fill_meta["pix_fmt"] == "yuv420p"

    # At t=0.1s (before cut), frame should be dominated by the Left RED band
    frame_early = _decode_frame_rgb(fill_out, 0.1, 270, 480)
    center_early = frame_early[200:280, 95:175].mean(axis=(0, 1))
    assert center_early[0] > 170  # High Red
    assert center_early[0] > center_early[2] + 100  # Red >> Blue

    # At t=0.7s (after cut), frame should be dominated by the Right BLUE band
    frame_late = _decode_frame_rgb(fill_out, 0.7, 270, 480)
    center_late = frame_late[200:280, 95:175].mean(axis=(0, 1))
    assert center_late[2] > 170  # High Blue
    assert center_late[2] > center_late[0] + 100  # Blue >> Red

    # 2. Render `fit_blur` on the same 16:9 striped source video
    blur_spec = build_reframe_filter_spec(
        fill_path,
        layout_override="fit_blur",
        target_size=target_size,
        work_dir=tmp_path,
    )
    blur_out = tmp_path / "out_fit_blur.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src_video),
            "-vf",
            blur_spec.filtergraph,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(blur_out),
        ],
        check=True,
        capture_output=True,
    )

    blur_meta = _probe_stream(blur_out)
    assert int(blur_meta["width"]) == 270
    assert int(blur_meta["height"]) == 480
    assert blur_meta["pix_fmt"] == "yuv420p"

    # For 16:9 source fitted into 270x480:
    # Foreground is 270x152 centered vertically at y in [164, 316].
    # Top/bottom regions (y in [20, 120] and [360, 460]) are blurred zoomed background!
    blur_frame = _decode_frame_rgb(blur_out, 0.2, 270, 480).astype(np.float32)

    # Check foreground left/right color bands at vertical center (y=240):
    fg_left = blur_frame[220:260, 15:65].mean(axis=(0, 1))
    fg_right = blur_frame[220:260, 205:255].mean(axis=(0, 1))
    assert fg_left[0] > 170 and fg_left[0] > fg_left[2] + 100  # Left of fitted 16:9 is Red
    assert fg_right[2] > 170 and fg_right[2] > fg_right[0] + 100  # Right of fitted 16:9 is Blue

    # Compare horizontal high-frequency gradient in the textured center column [105:165]:
    # Foreground (y in [190, 290]) has sharp vertical grid lines -> high horizontal gradient
    # Blurred background (y in [30, 130]) has boxblur=20:5 -> much lower horizontal gradient
    fg_center_patch = blur_frame[190:290, 105:165, 1]
    bg_top_patch = blur_frame[30:130, 105:165, 1]
    fg_grad = float(np.mean(np.abs(np.diff(fg_center_patch, axis=1))))
    bg_grad = float(np.mean(np.abs(np.diff(bg_top_patch, axis=1))))

    assert fg_grad > 12.0
    assert bg_grad < fg_grad * 0.45
    # Also verify background is non-black (contains blurred color energy, not black letterbox bars)
    assert float(np.mean(blur_frame[30:130, :, :])) > 40.0
