from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pysubs2

from backend.pipeline.captions.ass_builder import (
    build_ass_subtitles,
    export_subtitles_v2,
)
from backend.pipeline.captions.template_loader import get_template_by_id
from backend.pipeline.reframe.layouts import (
    ReframeFilterSpec,
    build_reframe_filter_spec,
    resolve_output_dimensions,
)
from backend.pipeline.render.clip_plan_ops import (
    build_cut_list,
    compute_clip_plan_hash,
    extract_overlay_descriptors,
    remap_words_to_output_timeline,
)
from backend.pipeline.render.ffmpeg_cmd import (
    build_ffmpeg_render_command,
    build_reframe_filter,
    detect_nvenc_available,
    get_render_profile,
    parse_ffmpeg_progress_line,
)

__all__ = [
    "ASPECT_RESOLUTIONS",
    "build_ffmpeg_render_command",
    "build_reframe_filter",
    "render_clip",
    "render_clip_media",
    "render_clip_v2",
]

ASPECT_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def render_clip_media(
    *,
    job_dir: Path,
    clip_id: str,
    start: float,
    end: float,
    words: list[dict[str, Any]],
    template_id: str,
    aspect: str = "9:16",
    layout: str = "fill",
) -> dict[str, str]:
    """Generate .ass, .srt, thumbnail .jpg, and burned-in vertical .mp4 for a clip."""
    source_mp4 = job_dir / "source" / "source.mp4"
    renders_dir = job_dir / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)

    template = get_template_by_id(template_id)
    res = ASPECT_RESOLUTIONS.get(aspect, (1080, 1920))

    ass_path = renders_dir / f"{clip_id}_{template_id}.ass"
    srt_path = renders_dir / f"{clip_id}_{template_id}.srt"
    out_mp4 = renders_dir / f"{clip_id}_{template_id}.mp4"
    thumb_jpg = renders_dir / f"{clip_id}_thumb.jpg"

    build_ass_subtitles(
        words=words,
        template=template,
        out_ass_path=ass_path,
        clip_start_offset=start,
        play_res=res,
    )
    # Also save SRT sidecar
    subs = pysubs2.load(str(ass_path))
    subs.save(str(srt_path))

    cmd = build_ffmpeg_render_command(
        source_mp4=source_mp4,
        start=start,
        end=end,
        aspect=aspect,
        layout=layout,
        ass_path=ass_path,
        out_mp4=out_mp4,
    )
    subprocess.run(cmd, check=True, capture_output=True)

    # Extract thumbnail frame at 1.0s into the rendered clip
    if not thumb_jpg.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                "0.5",
                "-i",
                str(out_mp4),
                "-frames:v",
                "1",
                "-q:v",
                "4",
                str(thumb_jpg),
            ],
            check=False,
            capture_output=True,
        )

    return {
        "mp4_path": str(out_mp4),
        "ass_path": str(ass_path),
        "srt_path": str(srt_path),
        "thumb_path": str(thumb_jpg) if thumb_jpg.exists() else "",
    }


def render_clip(
    *,
    job_dir: Path,
    clip_id: str,
    start: float,
    end: float,
    words: list[dict[str, Any]],
    template_id: str,
    aspect: str = "9:16",
    layout: str = "fill",
) -> dict[str, str]:
    """Alias for `render_clip_media`."""
    return render_clip_media(
        job_dir=job_dir,
        clip_id=clip_id,
        start=start,
        end=end,
        words=words,
        template_id=template_id,
        aspect=aspect,
        layout=layout,
    )


def render_clip_v2(
    clip_plan: Mapping[str, Any],
    *,
    source_mp4: Path,
    out_dir: Path,
    transcript_words: Sequence[Mapping[str, Any]] | None = None,
    silence_spans: Sequence[tuple[float, float] | list[float]] | None = None,
    reframe_path: ReframeFilterSpec | Mapping[str, Any] | Path | None = None,
    brand_kit_data: Mapping[str, Any] | None = None,
    variables: Mapping[str, str] | None = None,
    progress_callback: Callable[[float], None] | None = None,
    use_nvenc: bool | None = None,
    use_acrossfade: bool = False,
) -> dict[str, Any]:
    """Spec v2 declarative clip renderer driven by `clip_plan.json`.

    1. Resolves `cut_list` and `keep_intervals` (supporting `remove_fillers` and `trim_silence`).
    2. Remaps transcript word timestamps and overlay triggers via `map_source_to_output_time`.
    3. Exports `.ass`, `.srt`, and `.vtt` subtitle sidecars.
    4. Builds and runs single-pass FFmpeg command with `-progress pipe:1` progress tracking.
    5. Extracts hook thumbnail `thumb.jpg` and writes `meta.json` (`plan_hash`, timings, profile).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clip_id = str(clip_plan.get("clip_id") or "clip")
    raw_output_cfg = clip_plan.get("output")
    output_cfg: dict[str, Any] = dict(raw_output_cfg) if isinstance(raw_output_cfg, Mapping) else {}
    aspect = str(output_cfg.get("aspect") or "9:16")
    profile_name = str(output_cfg.get("profile") or "youtube_shorts")
    prof = get_render_profile(profile_name, aspect_override=aspect)
    out_w, out_h = resolve_output_dimensions(
        prof.get("aspect", aspect),
        target_size=(int(prof.get("w", 1080)), int(prof.get("h", 1920))),
    )

    raw_layout_cfg = clip_plan.get("layout")
    layout_cfg: dict[str, Any] = dict(raw_layout_cfg) if isinstance(raw_layout_cfg, Mapping) else {}
    layout_tpl = str(layout_cfg.get("template") or "fill_default@1")
    layout_force = layout_cfg.get("force")
    if layout_force in ("fill", "fit_blur"):
        layout_mode = str(layout_force)
    elif "blur" in layout_tpl:
        layout_mode = "fit_blur"
    else:
        layout_mode = "fill"

    raw_captions_cfg = clip_plan.get("captions")
    captions_cfg: dict[str, Any] = dict(raw_captions_cfg) if isinstance(raw_captions_cfg, Mapping) else {}
    caption_tpl_id = str(captions_cfg.get("template") or "bold_pop_yellow@1")
    raw_overrides = captions_cfg.get("overrides")
    overrides_cfg: dict[str, Any] = dict(raw_overrides) if isinstance(raw_overrides, Mapping) else {}
    raw_word_ov = overrides_cfg.get("words")
    word_overrides: dict[str, Any] | None = dict(raw_word_ov) if isinstance(raw_word_ov, Mapping) else None

    cut_res = build_cut_list(
        clip_plan,
        transcript_words=transcript_words,
        silence_spans=silence_spans,
    )

    remapped_words = remap_words_to_output_timeline(
        transcript_words or [],
        cut_res.cut_list,
        clip_start=cut_res.clip_start,
        clip_end=cut_res.clip_end,
        drop_cut_words=True,
        word_overrides=word_overrides,
    )

    sidecars = export_subtitles_v2(
        remapped_words,
        caption_tpl_id,
        out_dir,
        stem="captions",
        aspect=aspect,
        play_res=(out_w, out_h),
        clip_start_offset=0.0,
    )

    overlay_descs = extract_overlay_descriptors(
        clip_plan,
        cut_list=cut_res.cut_list,
        clip_start=cut_res.clip_start,
        output_duration=cut_res.output_duration,
        variables=variables,
        brand_kit_data=brand_kit_data,
    )

    resolved_reframe_spec: ReframeFilterSpec | None = None
    if isinstance(reframe_path, ReframeFilterSpec):
        resolved_reframe_spec = reframe_path
    elif reframe_path is not None:
        resolved_reframe_spec = build_reframe_filter_spec(
            reframe_path,
            layout_override="fit_blur" if layout_mode == "fit_blur" else "fill",
            target_size=(out_w, out_h),
            work_dir=out_dir,
        )

    raw_audio_cfg = clip_plan.get("audio")
    audio_cfg: dict[str, Any] = dict(raw_audio_cfg) if isinstance(raw_audio_cfg, Mapping) else {}
    loudness_lufs = float(audio_cfg.get("loudness_lufs", -16.0))

    out_mp4 = out_dir / "out.mp4"
    thumb_jpg = out_dir / "thumb.jpg"
    meta_json = out_dir / "meta.json"

    cmd = build_ffmpeg_render_command(
        source_mp4=Path(source_mp4),
        start=cut_res.clip_start,
        end=cut_res.clip_end,
        aspect=aspect,
        layout=layout_mode,
        ass_path=sidecars["ass"],
        out_mp4=out_mp4,
        profile=profile_name,
        cut_list=cut_res,
        reframe_spec=resolved_reframe_spec,
        overlays=overlay_descs,
        loudnorm=True,
        loudness_lufs=loudness_lufs,
        use_nvenc=use_nvenc,
        progress_pipe=True,
        use_acrossfade=use_acrossfade,
    )

    total_dur = max(0.1, cut_res.output_duration)
    if progress_callback is not None:
        progress_callback(0.0)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        ratio = parse_ffmpeg_progress_line(line, total_dur)
        if ratio is not None and progress_callback is not None:
            progress_callback(ratio)
    stderr_out = proc.stderr.read() if proc.stderr is not None else ""
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"FFmpeg render_clip_v2 failed (exit {ret}): {stderr_out[:400]}")

    if progress_callback is not None:
        progress_callback(1.0)

    # Extract hook thumbnail frame
    thumb_ts = min(0.5, max(0.0, total_dur * 0.25))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{thumb_ts:.2f}",
            "-i",
            str(out_mp4),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(thumb_jpg),
        ],
        check=False,
        capture_output=True,
    )

    plan_hash = compute_clip_plan_hash(
        clip_plan,
        template_versions={
            "caption": caption_tpl_id,
            "layout": layout_tpl,
        },
    )
    meta_payload: dict[str, Any] = {
        "schema_version": 1,
        "clip_id": clip_id,
        "plan_hash": plan_hash,
        "profile": profile_name,
        "aspect": aspect,
        "resolution": [out_w, out_h],
        "encoder": "h264_nvenc" if (detect_nvenc_available() if use_nvenc is None else use_nvenc) else "libx264",
        "output_duration_s": cut_res.output_duration,
        "cut_list": cut_res.to_dict(),
        "overlays": [ov.to_dict() for ov in overlay_descs],
    }
    meta_json.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

    return {
        "mp4_path": str(out_mp4),
        "ass_path": str(sidecars["ass"]),
        "srt_path": str(sidecars["srt"]),
        "vtt_path": str(sidecars["vtt"]),
        "thumb_path": str(thumb_jpg) if thumb_jpg.exists() else "",
        "meta_path": str(meta_json),
        "plan_hash": plan_hash,
        "output_duration_s": cut_res.output_duration,
        "command": cmd,
    }
