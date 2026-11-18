from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from backend.app.config import ROOT_DIR, get_settings
from backend.pipeline.reframe.layouts import (
    OUTPUT_DIMENSIONS,
    ReframeFilterSpec,
    build_fit_blur_filtergraph,
    build_reframe_filter_spec,
    resolve_output_dimensions,
)
from backend.pipeline.render.clip_plan_ops import (
    CutListResult,
    OverlayDescriptor,
    compute_keep_intervals,
)

DEFAULT_RENDER_PROFILES: dict[str, dict[str, Any]] = {
    "youtube_shorts": {
        "aspect": "9:16",
        "w": 1080,
        "h": 1920,
        "fps": 30,
        "video_codec": "libx264",
        "gpu_codec": "h264_nvenc",
        "crf": 20,
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "160k",
        "audio_kbps": 160,
        "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
        "movflags": "+faststart",
        "max_duration_s": 180,
    },
    "reels": {
        "aspect": "9:16",
        "w": 1080,
        "h": 1920,
        "fps": 30,
        "video_codec": "libx264",
        "gpu_codec": "h264_nvenc",
        "crf": 20,
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "160k",
        "audio_kbps": 160,
        "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
        "movflags": "+faststart",
        "max_duration_s": 180,
    },
    "tiktok": {
        "aspect": "9:16",
        "w": 1080,
        "h": 1920,
        "fps": 30,
        "video_codec": "libx264",
        "gpu_codec": "h264_nvenc",
        "crf": 20,
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "160k",
        "audio_kbps": 160,
        "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
        "movflags": "+faststart",
        "max_duration_s": 180,
    },
    "square_1x1": {
        "aspect": "1:1",
        "w": 1080,
        "h": 1080,
        "fps": 30,
        "video_codec": "libx264",
        "gpu_codec": "h264_nvenc",
        "crf": 20,
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "160k",
        "audio_kbps": 160,
        "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
        "movflags": "+faststart",
        "max_duration_s": 180,
    },
    "portrait_4x5": {
        "aspect": "4:5",
        "w": 1080,
        "h": 1350,
        "fps": 30,
        "video_codec": "libx264",
        "gpu_codec": "h264_nvenc",
        "crf": 20,
        "preset": "medium",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "160k",
        "audio_kbps": 160,
        "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
        "movflags": "+faststart",
        "max_duration_s": 180,
    },
    "draft_fast": {
        "aspect": "9:16",
        "w": 540,
        "h": 960,
        "fps": 30,
        "video_codec": "libx264",
        "gpu_codec": "h264_nvenc",
        "crf": 28,
        "preset": "ultrafast",
        "pix_fmt": "yuv420p",
        "audio_codec": "aac",
        "audio_bitrate": "96k",
        "audio_kbps": 96,
        "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
        "movflags": "+faststart",
        "max_duration_s": 180,
    },
}

_NVENC_CACHE: bool | None = None
_CLOCK_RE = re.compile(r"^(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$")


def load_render_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load `config/render.yaml` and merge with built-in Spec v2 profile defaults."""
    path = Path(config_path) if config_path is not None else (ROOT_DIR / "config" / "render.yaml")
    loaded: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            loaded = dict(raw)
    else:
        try:
            loaded = dict(get_settings().load_yaml_config("render"))
        except Exception:
            loaded = {}

    profiles = dict(DEFAULT_RENDER_PROFILES)
    raw_profiles = loaded.get("profiles")
    if isinstance(raw_profiles, Mapping):
        for p_name, p_cfg in raw_profiles.items():
            if isinstance(p_cfg, Mapping):
                base = dict(profiles.get(str(p_name), DEFAULT_RENDER_PROFILES["youtube_shorts"]))
                base.update(dict(p_cfg))
                if "audio_bitrate" not in base and "audio_kbps" in base:
                    base["audio_bitrate"] = f"{int(base['audio_kbps'])}k"
                profiles[str(p_name)] = base

    loaded["profiles"] = profiles
    return loaded


def get_render_profile(
    profile_name: str = "youtube_shorts",
    *,
    aspect_override: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Resolve a named render profile (`youtube_shorts`, `reels`, `tiktok`, `square_1x1`, `portrait_4x5`, `draft_fast`)."""
    cfg = load_render_config(config_path)
    profiles = cfg.get("profiles") or DEFAULT_RENDER_PROFILES
    profile = dict(profiles.get(profile_name) or profiles.get("youtube_shorts") or DEFAULT_RENDER_PROFILES["youtube_shorts"])
    if aspect_override and aspect_override in OUTPUT_DIMENSIONS:
        profile["aspect"] = aspect_override
        if profile_name != "draft_fast":
            w, h = OUTPUT_DIMENSIONS[aspect_override]
            profile["w"] = w
            profile["h"] = h
    return profile


def detect_nvenc_available(*, force_refresh: bool = False) -> bool:
    """Auto-detect whether NVIDIA hardware H.264 encoder (`h264_nvenc`) is functional on this host."""
    global _NVENC_CACHE
    if _NVENC_CACHE is not None and not force_refresh:
        return _NVENC_CACHE

    try:
        enc_proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if "h264_nvenc" not in enc_proc.stdout:
            _NVENC_CACHE = False
            return False

        # Probe actual NVENC device initialization with a 1-frame null encode
        probe_proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=128x128:d=0.04",
                "-frames:v",
                "1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        _NVENC_CACHE = probe_proc.returncode == 0
    except Exception:
        _NVENC_CACHE = False

    return bool(_NVENC_CACHE)


def _escape_filter_path(path: Path | str) -> str:
    raw = str(path).replace("\\", "/")
    raw = raw.replace(":", "\\:")
    raw = raw.replace("'", "\\'")
    return raw


def _hex_to_ffmpeg_color(hex_str: str) -> str:
    clean = (hex_str or "#FFE600").lstrip("#")
    if len(clean) == 8:
        rgb = clean[:6]
        alpha = int(clean[6:8], 16) / 255.0
        return f"0x{rgb}@{alpha:.2f}"
    if len(clean) == 6:
        return f"0x{clean}"
    return "0xFFE600"


def build_reframe_filter(
    aspect: str = "9:16",
    layout: str = "fill",
    ass_path: Path | None = None,
    *,
    fonts_dir: Path | None = None,
) -> str:
    """Build v1/v2 pure FFmpeg video filter chain for 9:16 / 1:1 / 4:5 crop or blurred-background fit + ASS burn-in."""
    target_w, target_h = OUTPUT_DIMENSIONS.get(aspect, (1080, 1920))
    ratio_str = f"{target_w}/{target_h}"

    if layout == "fit_blur":
        base_chain = (
            f"split=2[bg][fg];"
            f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},boxblur=20:5[bg_blur];"
            f"[fg]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg_fit];"
            f"[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        base_chain = (
            f"crop='min(iw,ih*({ratio_str}))':'min(ih,iw/({ratio_str}))':'(iw-ow)/2':'(ih-oh)/2',"
            f"scale={target_w}:{target_h}"
        )

    if ass_path is not None:
        escaped_ass = _escape_filter_path(ass_path)
        if fonts_dir is not None and Path(fonts_dir).exists():
            escaped_fonts = _escape_filter_path(fonts_dir)
            return f"{base_chain},ass='{escaped_ass}':fontsdir='{escaped_fonts}'"
        return f"{base_chain},ass='{escaped_ass}'"
    return base_chain


def _build_trim_concat_chains(
    keep_intervals: Sequence[tuple[float, float]],
    *,
    crossfade_ms: int = 40,
    use_acrossfade: bool = False,
) -> tuple[str, str, str, str]:
    """Build `[0:v]` and `[0:a]` trim/atrim + concat/acrossfade filter chains for multi-interval cut lists."""
    intervals = list(keep_intervals)
    if not intervals:
        intervals = [(0.0, 1.0)]

    if len(intervals) == 1:
        s, e = intervals[0]
        v_chain = f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v_cut]"
        a_chain = f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a_cut]"
        return v_chain, "[v_cut]", a_chain, "[a_cut]"

    v_parts: list[str] = []
    a_parts: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []

    for idx, (s, e) in enumerate(intervals):
        v_lbl = f"[vseg{idx}]"
        a_lbl = f"[aseg{idx}]"
        v_parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS{v_lbl}")
        a_parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS{a_lbl}")
        v_labels.append(v_lbl)
        a_labels.append(a_lbl)

    n = len(intervals)
    v_concat = f"{''.join(v_labels)}concat=n={n}:v=1:a=0[v_cut]"
    v_full = ";".join([*v_parts, v_concat])

    cf_sec = max(0.01, float(crossfade_ms) / 1000.0)
    can_acrossfade = use_acrossfade and all((e - s) > cf_sec * 2.5 for s, e in intervals)
    if can_acrossfade:
        # Chain acrossfade across adjacent audio segments
        a_steps = list(a_parts)
        cur_lbl = a_labels[0]
        for idx in range(1, n):
            next_lbl = a_labels[idx]
            out_lbl = "[a_cut]" if idx == n - 1 else f"[acf{idx}]"
            a_steps.append(f"{cur_lbl}{next_lbl}acrossfade=d={cf_sec:.3f}:c1=tri:c2=tri{out_lbl}")
            cur_lbl = out_lbl
        a_full = ";".join(a_steps)
    else:
        a_concat = f"{''.join(a_labels)}concat=n={n}:v=0:a=1[a_cut]"
        a_full = ";".join([*a_parts, a_concat])

    return v_full, "[v_cut]", a_full, "[a_cut]"


def _build_overlay_filters(
    overlays: Sequence[OverlayDescriptor | Mapping[str, Any]],
    *,
    out_w: int,
    out_h: int,
    total_duration: float,
) -> tuple[list[str], list[tuple[int, OverlayDescriptor]]]:
    """Build inline video filter stages (`drawbox` for `progress_bar`, `hook_banner`, `lower_third`) and secondary image input specs."""
    inline_filters: list[str] = []
    image_overlays: list[tuple[int, OverlayDescriptor]] = []
    next_input_idx = 1

    for raw_ov in overlays:
        ov = (
            raw_ov
            if isinstance(raw_ov, OverlayDescriptor)
            else OverlayDescriptor(
                kind=str(raw_ov.get("kind") or raw_ov.get("type") or "hook_banner"),
                template_id=str(raw_ov.get("template_id") or raw_ov.get("template") or "overlay@1"),
                start=float(raw_ov.get("start", 0.0)),
                end=float(raw_ov.get("end", total_duration)),
                text=str(raw_ov.get("text") or ""),
                asset=str(raw_ov["asset"]) if raw_ov.get("asset") else None,
                position=str(raw_ov.get("position") or "top_center"),
                y_percent=float(raw_ov.get("y_percent", 14.0)),
                color=str(raw_ov.get("color") or "#FFE600"),
                bg_color=str(raw_ov.get("bg_color") or "#000000CC"),
                height_px=int(raw_ov.get("height_px", 10)),
                opacity=float(raw_ov.get("opacity", 0.9)),
            )
        )

        if ov.kind == "progress_bar":
            bar_h = max(4, int(ov.height_px))
            bar_y = out_h - bar_h if ov.y_percent >= 90.0 else int(round(out_h * (ov.y_percent / 100.0)))
            color_ff = _hex_to_ffmpeg_color(ov.color)
            inline_filters.append(
                f"drawbox=x=0:y={bar_y}:w={out_w}:h={bar_h}:color={color_ff}:t=fill:enable='between(t,{ov.start:.3f},{ov.end:.3f})'"
            )
        elif ov.kind in ("watermark", "broll_image") and ov.asset and Path(ov.asset).exists():
            image_overlays.append((next_input_idx, ov))
            next_input_idx += 1
        elif ov.kind in ("hook_banner", "lower_third"):
            box_h = 84 if ov.kind == "hook_banner" else 96
            box_y = int(round(out_h * (ov.y_percent / 100.0)))
            bg_ff = _hex_to_ffmpeg_color(ov.bg_color)
            inline_filters.append(
                f"drawbox=x=40:y={box_y}:w={max(100, out_w - 80)}:h={box_h}:color={bg_ff}:t=fill:enable='between(t,{ov.start:.3f},{ov.end:.3f})'"
            )

    return inline_filters, image_overlays


def build_ffmpeg_render_command(
    *,
    source_mp4: Path,
    start: float = 0.0,
    end: float = 0.0,
    aspect: str = "9:16",
    layout: str = "fill",
    ass_path: Path | None = None,
    out_mp4: Path,
    profile: str | None = None,
    cut_list: CutListResult | Sequence[tuple[float, float]] | None = None,
    keep_intervals: Sequence[tuple[float, float]] | None = None,
    reframe_spec: ReframeFilterSpec | Mapping[str, Any] | Path | None = None,
    overlays: Sequence[OverlayDescriptor | Mapping[str, Any]] | None = None,
    fonts_dir: Path | None = None,
    loudnorm: bool | str = False,
    loudness_lufs: float | None = None,
    use_nvenc: bool | None = None,
    progress_pipe: bool = False,
    use_acrossfade: bool = False,
) -> list[str]:
    """Pure function returning the exact FFmpeg command list for single-pass clip rendering.

    Supports both legacy v1 calls (`source_mp4, start, end, aspect, layout, ass_path, out_mp4`)
    and Spec v2 features (profiles from `config/render.yaml`, CPU `libx264` / GPU `h264_nvenc`
    auto-detection, `ReframeFilterSpec` layout filtergraphs, multi-interval `cut_list` with
    `trim`/`atrim`/`concat`/`acrossfade`, `ass=` burn-in with `fontsdir`, overlays including
    `progress_bar` via `drawbox` and `watermark` image overlay, `loudnorm=I=-16:TP=-1.5:LRA=11`,
    and `-progress pipe:1`).
    """
    is_v2_mode = any(
        x is not None
        for x in (
            profile,
            cut_list,
            keep_intervals,
            reframe_spec,
            overlays,
            fonts_dir,
            loudness_lufs,
            use_nvenc,
        )
    ) or bool(loudnorm) or bool(progress_pipe)

    if not is_v2_mode:
        vf = build_reframe_filter(aspect=aspect, layout=layout, ass_path=ass_path)
        return [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.2f}",
            "-to",
            f"{end:.2f}",
            "-i",
            str(source_mp4),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]

    prof = get_render_profile(profile or "youtube_shorts", aspect_override=aspect)
    out_w, out_h = resolve_output_dimensions(
        prof.get("aspect", aspect),
        target_size=(int(prof.get("w", 1080)), int(prof.get("h", 1920))),
    )

    # Resolve keep_intervals from CutListResult, cut_list tuples, or (start, end)
    crossfade_ms = 40
    if isinstance(cut_list, CutListResult):
        resolved_keep = list(cut_list.keep_intervals)
        crossfade_ms = cut_list.crossfade_ms
    elif keep_intervals is not None:
        resolved_keep = [(float(s), float(e)) for s, e in keep_intervals]
    elif cut_list is not None:
        eff_end = float(end) if float(end) > float(start) else float(start) + 15.0
        resolved_keep = compute_keep_intervals(float(start), eff_end, cut_list)
    elif float(end) > float(start):
        resolved_keep = [(float(start), float(end))]
    else:
        resolved_keep = []

    total_dur = (
        sum(max(0.0, e - s) for s, e in resolved_keep)
        if resolved_keep
        else max(1.0, float(end) - float(start))
    )

    # Resolve reframe filtergraph
    if isinstance(reframe_spec, ReframeFilterSpec):
        reframe_fg = reframe_spec.filtergraph
    elif isinstance(reframe_spec, (Mapping, Path)):
        built_spec = build_reframe_filter_spec(
            reframe_spec,
            layout_override="fit_blur" if layout == "fit_blur" else ("fill" if layout == "fill" else None),
            target_size=(out_w, out_h),
        )
        reframe_fg = built_spec.filtergraph
    else:
        if layout == "fit_blur":
            reframe_fg = build_fit_blur_filtergraph(out_aspect=aspect, target_size=(out_w, out_h))
        else:
            ratio_str = f"{out_w}/{out_h}"
            reframe_fg = (
                f"crop='min(iw,ih*({ratio_str}))':'min(ih,iw/({ratio_str}))':'(iw-ow)/2':'(ih-oh)/2',"
                f"scale={out_w}:{out_h}:flags=bicubic,setsar=1"
            )

    inline_ov_filters, image_ov_specs = _build_overlay_filters(
        overlays or [],
        out_w=out_w,
        out_h=out_h,
        total_duration=total_dur,
    )

    post_reframe_stages: list[str] = list(inline_ov_filters)
    if ass_path is not None:
        escaped_ass = _escape_filter_path(ass_path)
        eff_fonts_dir = fonts_dir
        if eff_fonts_dir is None:
            cand_fonts = ROOT_DIR / "assets" / "fonts"
            if cand_fonts.exists():
                eff_fonts_dir = cand_fonts
        if eff_fonts_dir is not None and Path(eff_fonts_dir).exists():
            escaped_fonts = _escape_filter_path(eff_fonts_dir)
            post_reframe_stages.append(f"ass='{escaped_ass}':fontsdir='{escaped_fonts}'")
        else:
            post_reframe_stages.append(f"ass='{escaped_ass}'")

    # Resolve loudnorm filter string
    if isinstance(loudnorm, str) and loudnorm.strip():
        loudnorm_expr = loudnorm.strip()
    elif loudness_lufs is not None:
        loudnorm_expr = f"loudnorm=I={float(loudness_lufs):g}:TP=-1.5:LRA=11"
    elif loudnorm is True or profile is not None:
        loudnorm_expr = str(prof.get("loudnorm") or "loudnorm=I=-16:TP=-1.5:LRA=11")
    else:
        loudnorm_expr = ""

    cmd: list[str] = ["ffmpeg", "-y"]
    use_filter_complex = len(resolved_keep) > 1 or len(image_ov_specs) > 0

    if not use_filter_complex and len(resolved_keep) == 1:
        seg_s, seg_e = resolved_keep[0]
        cmd.extend(["-ss", f"{seg_s:.3f}", "-to", f"{seg_e:.3f}"])

    cmd.extend(["-i", str(source_mp4)])
    for _, img_ov in image_ov_specs:
        cmd.extend(["-i", str(img_ov.asset)])

    if use_filter_complex:
        fc_parts: list[str] = []
        if resolved_keep:
            v_trim_chain, v_cur, a_trim_chain, a_cur = _build_trim_concat_chains(
                resolved_keep,
                crossfade_ms=crossfade_ms,
                use_acrossfade=use_acrossfade,
            )
            fc_parts.append(v_trim_chain)
            fc_parts.append(a_trim_chain)
        else:
            v_cur = "[0:v]"
            a_cur = "[0:a]"

        fc_parts.append(f"{v_cur}{reframe_fg}[v_ref]")
        v_cur = "[v_ref]"

        for input_idx, img_ov in image_ov_specs:
            wm_w = max(32, int(round(out_w * 0.12)))
            wm_lbl = f"[wm{input_idx}]"
            out_lbl = f"[v_wm{input_idx}]"
            fc_parts.append(f"[{input_idx}:v]scale={wm_w}:-1{wm_lbl}")
            pos_expr = "W-w-36:36" if "right" in img_ov.position else "36:36"
            fc_parts.append(
                f"{v_cur}{wm_lbl}overlay={pos_expr}:enable='between(t,{img_ov.start:.3f},{img_ov.end:.3f})'{out_lbl}"
            )
            v_cur = out_lbl

        if post_reframe_stages:
            fc_parts.append(f"{v_cur}{','.join(post_reframe_stages)}[vout]")
            v_cur = "[vout]"

        if loudnorm_expr:
            fc_parts.append(f"{a_cur}{loudnorm_expr}[aout]")
            a_cur = "[aout]"

        cmd.extend(["-filter_complex", ";".join(fc_parts), "-map", v_cur, "-map", a_cur])
    else:
        vf_chain = ",".join([reframe_fg, *post_reframe_stages])
        cmd.extend(["-vf", vf_chain])
        if loudnorm_expr:
            cmd.extend(["-af", loudnorm_expr])

    nvenc_enabled = detect_nvenc_available() if use_nvenc is None else bool(use_nvenc)
    crf_val = str(prof.get("crf", 20))
    preset_val = str(prof.get("preset", "medium"))
    fps_val = str(prof.get("fps", 30))
    pix_fmt = str(prof.get("pix_fmt", "yuv420p"))
    audio_codec = str(prof.get("audio_codec", "aac"))
    audio_bitrate = str(prof.get("audio_bitrate", "160k"))

    if nvenc_enabled:
        nvenc_preset = "p1" if preset_val == "ultrafast" else "p4"
        cmd.extend(
            [
                "-c:v",
                str(prof.get("gpu_codec", "h264_nvenc")),
                "-preset",
                nvenc_preset,
                "-cq",
                crf_val,
            ]
        )
    else:
        cmd.extend(
            [
                "-c:v",
                str(prof.get("video_codec", "libx264")),
                "-preset",
                preset_val,
                "-crf",
                crf_val,
            ]
        )

    cmd.extend(
        [
            "-r",
            fps_val,
            "-pix_fmt",
            pix_fmt,
            "-c:a",
            audio_codec,
            "-b:a",
            audio_bitrate,
            "-movflags",
            str(prof.get("movflags", "+faststart")),
        ]
    )

    if progress_pipe:
        cmd.extend(["-progress", "pipe:1"])

    cmd.append(str(out_mp4))
    return cmd


def parse_ffmpeg_progress_line(
    line: str,
    total_duration_s: float,
) -> float | None:
    """Parse an FFmpeg `-progress pipe:1` line and return completion ratio in `[0.0, 1.0]` (or `None` if unrelated).

    Handles:
    - `out_time_us=<microseconds>`
    - `out_time_ms=<microseconds>` (FFmpeg reports microseconds under `out_time_ms` as well)
    - `out_time=HH:MM:SS.mmmmmm`
    - `progress=end` -> `1.0`
    """
    raw = (line or "").strip()
    if not raw or "=" not in raw:
        return None

    key, val = raw.split("=", 1)
    key = key.strip().lower()
    val = val.strip()
    dur = max(0.001, float(total_duration_s))

    if key == "progress":
        if val.lower() == "end":
            return 1.0
        return None

    if key in ("out_time_us", "out_time_ms"):
        try:
            us = int(val)
            if us < 0:
                return 0.0
            sec = us / 1_000_000.0
            return round(min(1.0, max(0.0, sec / dur)), 4)
        except ValueError:
            return None

    if key == "out_time":
        m = _CLOCK_RE.match(val)
        if not m:
            return None
        hh = int(m.group(1))
        mm = int(m.group(2))
        ss = float(m.group(3))
        sec = hh * 3600.0 + mm * 60.0 + ss
        return round(min(1.0, max(0.0, sec / dur)), 4)

    return None
