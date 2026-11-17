from __future__ import annotations

import subprocess
from pathlib import Path

import pysubs2

from backend.pipeline.captions.ass_builder import build_ass_subtitles
from backend.pipeline.captions.template_loader import get_template_by_id

ASPECT_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}


def build_reframe_filter(aspect: str = "9:16", layout: str = "fill", ass_path: Path | None = None) -> str:
    """Build pure FFmpeg video filter chain for 9:16 / 1:1 / 4:5 crop or blurred-background fit + ASS burn-in."""
    target_w, target_h = ASPECT_RESOLUTIONS.get(aspect, (1080, 1920))
    ratio_str = f"{target_w}/{target_h}"

    if layout == "fit_blur":
        # Blurred background canvas + sharp foreground fitted in center
        base_chain = (
            f"split=2[bg][fg];"
            f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},boxblur=20:5[bg_blur];"
            f"[fg]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg_fit];"
            f"[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        # Center/speaker tracked crop to target aspect ratio, then scale to output resolution
        base_chain = (
            f"crop='min(iw,ih*({ratio_str}))':'min(ih,iw/({ratio_str}))':'(iw-ow)/2':'(ih-oh)/2',"
            f"scale={target_w}:{target_h}"
        )

    if ass_path is not None:
        escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
        return f"{base_chain},ass='{escaped_ass}'"
    return base_chain


def build_ffmpeg_render_command(
    *,
    source_mp4: Path,
    start: float,
    end: float,
    aspect: str,
    layout: str,
    ass_path: Path,
    out_mp4: Path,
) -> list[str]:
    """Pure function returning the exact FFmpeg command list for single-pass clip rendering."""
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


def render_clip_media(
    *,
    job_dir: Path,
    clip_id: str,
    start: float,
    end: float,
    words: list[dict],
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
