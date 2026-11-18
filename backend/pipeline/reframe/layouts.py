from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from backend.pipeline.reframe.tracker import (
    CropBox,
    ReframeError,
    ReframeKeyframe,
    ReframePath,
    clamp_center,
    compute_crop_box,
)

OUTPUT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}

DEFAULT_SENDCMD_HZ = 10.0
DEFAULT_CROP_TARGET = "crop@reframe_crop"
DEFAULT_SEGMENT_DURATION_S = 1.0


@dataclass(slots=True)
class SendcmdEntry:
    """Single timestamped FFmpeg `sendcmd` crop command."""

    t: float
    x: int
    y: int
    w: int
    h: int
    layout: str = "fill"

    def format_line(self, target: str = DEFAULT_CROP_TARGET) -> str:
        return (
            f"{self.t:.3f} [enter] "
            f"{target} w {self.w}, "
            f"{target} h {self.h}, "
            f"{target} x {self.x}, "
            f"{target} y {self.y};"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": round(float(self.t), 3),
            "x": int(self.x),
            "y": int(self.y),
            "w": int(self.w),
            "h": int(self.h),
            "layout": self.layout,
        }


@dataclass(slots=True)
class SegmentCrop:
    """Fallback crop/layout segment for long clips or span-level layout transitions."""

    start: float
    end: float
    x: int
    y: int
    w: int
    h: int
    out_w: int
    out_h: int
    layout: str = "fill"
    filtergraph: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(float(self.start), 3),
            "end": round(float(self.end), 3),
            "x": int(self.x),
            "y": int(self.y),
            "w": int(self.w),
            "h": int(self.h),
            "out_w": int(self.out_w),
            "out_h": int(self.out_h),
            "layout": self.layout,
            "filtergraph": self.filtergraph,
        }


@dataclass(slots=True)
class ReframeFilterSpec:
    """Unified filter specification returned by `build_reframe_filter_spec`."""

    layout: str
    out_aspect: str
    out_w: int
    out_h: int
    filtergraph: str
    sendcmd_script: str | None = None
    sendcmd_path: Path | None = None
    segment_crops: list[SegmentCrop] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "out_aspect": self.out_aspect,
            "out_w": int(self.out_w),
            "out_h": int(self.out_h),
            "filtergraph": self.filtergraph,
            "sendcmd_script": self.sendcmd_script,
            "sendcmd_path": str(self.sendcmd_path) if self.sendcmd_path is not None else None,
            "segment_crops": [seg.to_dict() for seg in self.segment_crops],
            "warnings": list(self.warnings),
        }


def resolve_output_dimensions(
    out_aspect: str = "9:16",
    target_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Resolve even target `(out_w, out_h)` from aspect ratio or explicit `target_size`."""
    if target_size is not None:
        raw_w, raw_h = int(target_size[0]), int(target_size[1])
        if raw_w < 2 or raw_h < 2:
            raise ReframeError(
                "INVALID_DIMENSIONS",
                f"Target dimensions must be >= 2x2, got {raw_w}x{raw_h}",
            )
        out_w = (raw_w // 2) * 2
        out_h = (raw_h // 2) * 2
        return out_w, out_h

    if out_aspect not in OUTPUT_DIMENSIONS:
        raise ReframeError(
            "INVALID_ASPECT",
            f"Unsupported aspect ratio {out_aspect!r}; expected one of {tuple(OUTPUT_DIMENSIONS)}",
        )
    return OUTPUT_DIMENSIONS[out_aspect]


def _coerce_reframe_path(reframe_path: ReframePath | Mapping[str, Any] | Path | str) -> ReframePath:
    if isinstance(reframe_path, ReframePath):
        return reframe_path
    if isinstance(reframe_path, Mapping):
        return ReframePath.from_dict(reframe_path)
    if isinstance(reframe_path, Path):
        return ReframePath.load_json(reframe_path)
    if isinstance(reframe_path, str):
        stripped = reframe_path.strip()
        if stripped.startswith("{"):
            return ReframePath.from_json(stripped)
        return ReframePath.load_json(Path(stripped))
    raise ReframeError("INVALID_REFRAME_PATH", f"Unsupported reframe_path type: {type(reframe_path)!r}")


def _escape_ffmpeg_filter_path(path: Path | str) -> str:
    """Escape filesystem path for inside an FFmpeg filter single-quoted option."""
    raw = str(path).replace("\\", "/")
    raw = raw.replace(":", "\\:")
    raw = raw.replace("'", "\\'")
    return raw


def _quantize_crop_rect(
    box: CropBox,
    source_w: int,
    source_h: int,
) -> tuple[int, int, int, int]:
    """Quantize a `CropBox` to even integer pixel bounds strictly inside `[0, source_w] x [0, source_h]`."""
    src_w = max(2, int(source_w))
    src_h = max(2, int(source_h))
    max_even_w = max(2, (src_w // 2) * 2)
    max_even_h = max(2, (src_h // 2) * 2)

    w = int(round(box.w / 2.0) * 2)
    h = int(round(box.h / 2.0) * 2)
    w = max(2, min(max_even_w, w))
    h = max(2, min(max_even_h, h))

    x = int(round(box.x))
    y = int(round(box.y))
    x = max(0, min(src_w - w, x))
    y = max(0, min(src_h - h, y))
    return x, y, w, h


def compute_clamped_crop_rect(
    cx: float,
    cy: float,
    zoom: float,
    source_w: int,
    source_h: int,
    out_aspect: str = "9:16",
    *,
    target_size: tuple[int, int] | None = None,
) -> tuple[int, int, int, int]:
    """Compute even-sized integer `(x, y, w, h)` crop rectangle clamped inside source frame."""
    if source_w <= 0 or source_h <= 0:
        raise ReframeError("INVALID_DIMENSIONS", f"Invalid source size: {source_w}x{source_h}")

    if target_size is None or out_aspect in OUTPUT_DIMENSIONS:
        aspect_for_box = out_aspect if out_aspect in OUTPUT_DIMENSIONS else "9:16"
        box = compute_crop_box(cx, cy, zoom, source_w, source_h, aspect_for_box)
        if target_size is not None:
            out_w, out_h = resolve_output_dimensions(aspect_for_box, target_size)
            if (out_w, out_h) != OUTPUT_DIMENSIONS[aspect_for_box]:
                box = _compute_custom_aspect_box(cx, cy, zoom, source_w, source_h, out_w, out_h)
    else:
        out_w, out_h = resolve_output_dimensions(out_aspect, target_size)
        box = _compute_custom_aspect_box(cx, cy, zoom, source_w, source_h, out_w, out_h)

    return _quantize_crop_rect(box, source_w, source_h)


def _compute_custom_aspect_box(
    cx: float,
    cy: float,
    zoom: float,
    source_w: int,
    source_h: int,
    out_w: int,
    out_h: int,
) -> CropBox:
    safe_zoom = max(1.0, float(zoom)) if math.isfinite(zoom) else 1.0
    target_ratio = out_w / out_h
    src_ratio = source_w / source_h
    if src_ratio >= target_ratio:
        base_h = float(source_h)
        base_w = base_h * target_ratio
    else:
        base_w = float(source_w)
        base_h = base_w / target_ratio

    crop_w = min(float(source_w), base_w / safe_zoom)
    crop_h = min(float(source_h), base_h / safe_zoom)
    half_w_norm = (crop_w / source_w) * 0.5
    half_h_norm = (crop_h / source_h) * 0.5

    safe_cx = 0.5 if not math.isfinite(cx) else float(cx)
    safe_cy = 0.5 if not math.isfinite(cy) else float(cy)
    clamped_cx = min(max(safe_cx, min(0.5, half_w_norm)), max(0.5, 1.0 - half_w_norm))
    clamped_cy = min(max(safe_cy, min(0.5, half_h_norm)), max(0.5, 1.0 - half_h_norm))

    x = max(0.0, min(clamped_cx * source_w - crop_w * 0.5, float(source_w) - crop_w))
    y = max(0.0, min(clamped_cy * source_h - crop_h * 0.5, float(source_h) - crop_h))
    return CropBox(
        x=x,
        y=y,
        w=crop_w,
        h=crop_h,
        x_norm=x / source_w,
        y_norm=y / source_h,
        w_norm=crop_w / source_w,
        h_norm=crop_h / source_h,
        cx=clamped_cx,
        cy=clamped_cy,
        zoom=safe_zoom,
        out_w=out_w,
        out_h=out_h,
    )


def _sorted_keyframes(path: ReframePath) -> list[ReframeKeyframe]:
    if not path.keyframes:
        cx, cy = clamp_center(
            0.5,
            0.5,
            1.0,
            path.source_w,
            path.source_h,
            path.out_aspect if path.out_aspect in OUTPUT_DIMENSIONS else "9:16",
        )
        return [ReframeKeyframe(t=0.0, cx=cx, cy=cy, zoom=1.0, layout="fill")]
    return sorted(path.keyframes, key=lambda kf: float(kf.t))


def _interpolate_keyframe_at(
    t: float,
    keyframes: Sequence[ReframeKeyframe],
    cuts: Sequence[float],
    *,
    layout_override: str | None = None,
) -> tuple[float, float, float, str]:
    """Evaluate `(cx, cy, zoom, layout)` at timestamp `t`, never interpolating across scene cuts."""
    if not keyframes:
        return 0.5, 0.5, 1.0, layout_override or "fill"

    if t <= keyframes[0].t + 1e-9 or len(keyframes) == 1:
        kf0 = keyframes[0]
        return kf0.cx, kf0.cy, kf0.zoom, layout_override or kf0.layout

    if t >= keyframes[-1].t - 1e-9:
        kfn = keyframes[-1]
        return kfn.cx, kfn.cy, kfn.zoom, layout_override or kfn.layout

    for idx in range(len(keyframes) - 1):
        kf_a = keyframes[idx]
        kf_b = keyframes[idx + 1]
        if kf_a.t - 1e-9 <= t <= kf_b.t + 1e-9:
            if math.isclose(t, kf_a.t, abs_tol=1e-6):
                return kf_a.cx, kf_a.cy, kf_a.zoom, layout_override or kf_a.layout
            if math.isclose(t, kf_b.t, abs_tol=1e-6):
                return kf_b.cx, kf_b.cy, kf_b.zoom, layout_override or kf_b.layout

            # Scene cut check: if any cut falls in (kf_a.t, kf_b.t], hold kf_a before the cut
            # and jump sharply to kf_b at/after the cut (never linearly pan across a cut).
            spanning_cuts = [c for c in cuts if kf_a.t + 1e-6 < c <= kf_b.t + 1e-6]
            if spanning_cuts:
                first_cut = min(spanning_cuts)
                chosen = kf_a if t < first_cut - 1e-6 else kf_b
                return chosen.cx, chosen.cy, chosen.zoom, layout_override or chosen.layout

            # Span-level layout switch: do not interpolate between fill and fit_blur
            eff_a = layout_override or kf_a.layout
            eff_b = layout_override or kf_b.layout
            if eff_a != eff_b:
                return kf_a.cx, kf_a.cy, kf_a.zoom, eff_a

            span_dt = max(1e-6, kf_b.t - kf_a.t)
            alpha = max(0.0, min(1.0, (t - kf_a.t) / span_dt))
            cx = kf_a.cx + alpha * (kf_b.cx - kf_a.cx)
            cy = kf_a.cy + alpha * (kf_b.cy - kf_a.cy)
            zoom = kf_a.zoom + alpha * (kf_b.zoom - kf_a.zoom)
            return cx, cy, zoom, eff_a

    kfn = keyframes[-1]
    return kfn.cx, kfn.cy, kfn.zoom, layout_override or kfn.layout


def expand_sendcmd_entries(
    reframe_path: ReframePath | Mapping[str, Any] | Path | str,
    *,
    sample_hz: float = DEFAULT_SENDCMD_HZ,
    expand_to_fps: bool = False,
    layout_override: str | None = None,
    target_size: tuple[int, int] | None = None,
) -> list[SendcmdEntry]:
    """Expand a `ReframePath` into 10Hz or per-frame `SendcmdEntry` records with sharp cut jumps."""
    path = _coerce_reframe_path(reframe_path)
    if sample_hz <= 0:
        raise ReframeError("INVALID_ARGS", f"sample_hz must be > 0, got {sample_hz}")

    kfs = _sorted_keyframes(path)
    cuts = sorted({round(float(c), 3) for c in path.cuts if float(c) >= 0.0})

    rate = float(path.fps) if expand_to_fps and path.fps > 0 else float(sample_hz)
    dt = 1.0 / rate
    start_t = min(0.0, float(kfs[0].t))
    end_t = max(float(kfs[-1].t), start_t)

    timestamps: set[float] = {round(start_t, 3), round(end_t, 3)}
    n_steps = int(math.floor((end_t - start_t) / dt + 1e-6)) + 1
    for step in range(n_steps):
        t_val = round(start_t + step * dt, 3)
        if start_t - 1e-6 <= t_val <= end_t + 1e-6:
            timestamps.add(t_val)
    for kf in kfs:
        timestamps.add(round(float(kf.t), 3))
    for cut_t in cuts:
        if start_t - 1e-6 <= cut_t <= end_t + 1e-6:
            timestamps.add(round(cut_t, 3))

    entries: list[SendcmdEntry] = []
    for t_val in sorted(timestamps):
        cx, cy, zoom, layout = _interpolate_keyframe_at(
            t_val,
            kfs,
            cuts,
            layout_override=layout_override,
        )
        x, y, w, h = compute_clamped_crop_rect(
            cx,
            cy,
            zoom,
            path.source_w,
            path.source_h,
            path.out_aspect,
            target_size=target_size,
        )
        entries.append(SendcmdEntry(t=t_val, x=x, y=y, w=w, h=h, layout=layout))

    return entries


def build_sendcmd_script(
    reframe_path: ReframePath | Mapping[str, Any] | Path | str,
    *,
    sample_hz: float = DEFAULT_SENDCMD_HZ,
    expand_to_fps: bool = False,
    target_filter: str = DEFAULT_CROP_TARGET,
    layout_override: str | None = None,
    target_size: tuple[int, int] | None = None,
    deduplicate: bool = False,
) -> str:
    """Build an FFmpeg `sendcmd` script driving `crop` coordinates at 10Hz or output FPS."""
    entries = expand_sendcmd_entries(
        reframe_path,
        sample_hz=sample_hz,
        expand_to_fps=expand_to_fps,
        layout_override=layout_override,
        target_size=target_size,
    )
    path = _coerce_reframe_path(reframe_path)
    cut_set = {round(float(c), 3) for c in path.cuts}

    lines: list[str] = []
    prev_rect: tuple[int, int, int, int] | None = None
    for idx, entry in enumerate(entries):
        rect = (entry.x, entry.y, entry.w, entry.h)
        is_boundary = idx == 0 or idx == len(entries) - 1 or round(entry.t, 3) in cut_set
        if deduplicate and not is_boundary and prev_rect == rect:
            continue
        lines.append(entry.format_line(target=target_filter))
        prev_rect = rect

    return "\n".join(lines) + "\n"


def build_fill_filtergraph(
    reframe_path: ReframePath | Mapping[str, Any] | Path | str | None = None,
    *,
    out_aspect: str | None = None,
    target_size: tuple[int, int] | None = None,
    sendcmd_path: Path | str | None = None,
    target_filter: str = DEFAULT_CROP_TARGET,
    use_static_crop_when_constant: bool = False,
) -> str:
    """Build pure FFmpeg `fill` filtergraph (dynamic `sendcmd` + named `crop` + `scale` + `setsar=1`)."""
    if reframe_path is not None:
        path = _coerce_reframe_path(reframe_path)
        aspect = out_aspect or path.out_aspect
        out_w, out_h = resolve_output_dimensions(aspect, target_size)
        entries = expand_sendcmd_entries(
            path,
            layout_override="fill",
            target_size=(out_w, out_h),
        )
        first = entries[0]
        init_x, init_y, init_w, init_h = first.x, first.y, first.w, first.h
        all_same = all(
            (e.x, e.y, e.w, e.h) == (init_x, init_y, init_w, init_h) for e in entries
        )
    else:
        aspect = out_aspect or "9:16"
        out_w, out_h = resolve_output_dimensions(aspect, target_size)
        init_x, init_y, init_w, init_h = compute_clamped_crop_rect(
            0.5,
            0.5,
            1.0,
            1920,
            1080,
            aspect,
            target_size=(out_w, out_h),
        )
        all_same = True

    crop_name = target_filter if "@" in target_filter else f"crop@{target_filter}"

    if sendcmd_path is not None and not (use_static_crop_when_constant and all_same):
        escaped_cmd = _escape_ffmpeg_filter_path(sendcmd_path)
        return (
            f"sendcmd=f='{escaped_cmd}',"
            f"{crop_name}=w={init_w}:h={init_h}:x={init_x}:y={init_y}:exact=1,"
            f"scale={out_w}:{out_h}:flags=bicubic,"
            f"setsar=1"
        )

    return (
        f"{crop_name}=w={init_w}:h={init_h}:x={init_x}:y={init_y}:exact=1,"
        f"scale={out_w}:{out_h}:flags=bicubic,"
        f"setsar=1"
    )


def build_fit_blur_filtergraph(
    *,
    out_aspect: str = "9:16",
    target_size: tuple[int, int] | None = None,
    blur_filter: Literal["boxblur", "gblur"] = "boxblur",
    boxblur_luma_radius: int = 20,
    boxblur_luma_power: int = 5,
    gblur_sigma: float = 24.0,
    bg_zoom: float = 1.08,
) -> str:
    """Build pure FFmpeg `fit_blur` filtergraph: sharp fitted foreground over blurred, zoomed background."""
    out_w, out_h = resolve_output_dimensions(out_aspect, target_size)
    safe_bg_zoom = max(1.0, float(bg_zoom))
    zoom_w = max(out_w, (int(round(out_w * safe_bg_zoom)) // 2) * 2)
    zoom_h = max(out_h, (int(round(out_h * safe_bg_zoom)) // 2) * 2)

    if blur_filter == "gblur":
        blur_expr = f"gblur=sigma={float(gblur_sigma):.1f}"
    elif blur_filter == "boxblur":
        blur_expr = f"boxblur={int(boxblur_luma_radius)}:{int(boxblur_luma_power)}"
    else:
        raise ReframeError("INVALID_BLUR_FILTER", f"Unsupported blur filter: {blur_filter!r}")

    return (
        f"split=2[bg][fg];"
        f"[bg]scale={zoom_w}:{zoom_h}:force_original_aspect_ratio=increase:flags=bicubic,"
        f"crop={out_w}:{out_h}:(iw-ow)/2:(ih-oh)/2:exact=1,"
        f"{blur_expr}[bg_blur];"
        f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:"
        f"force_divisible_by=2:flags=bicubic[fg_fit];"
        f"[bg_blur][fg_fit]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def build_segment_crops(
    reframe_path: ReframePath | Mapping[str, Any] | Path | str,
    *,
    max_segment_duration_s: float = DEFAULT_SEGMENT_DURATION_S,
    clip_duration: float | None = None,
    layout_override: str | None = None,
    target_size: tuple[int, int] | None = None,
) -> list[SegmentCrop]:
    """Build segment-level crop definitions (fallback for very long clips or mixed-layout spans)."""
    if max_segment_duration_s <= 0:
        raise ReframeError("INVALID_ARGS", "max_segment_duration_s must be > 0")

    path = _coerce_reframe_path(reframe_path)
    out_w, out_h = resolve_output_dimensions(path.out_aspect, target_size)
    kfs = _sorted_keyframes(path)
    cuts = sorted({round(float(c), 3) for c in path.cuts if float(c) >= 0.0})

    frame_dt = 1.0 / max(1, int(path.fps))
    total_end = (
        float(clip_duration)
        if clip_duration is not None
        else max(float(kfs[-1].t) + round(frame_dt, 3), round(frame_dt, 3))
    )

    # Identify hard boundaries: start (0.0), scene cuts, and layout transitions
    hard_bounds: set[float] = {0.0, round(total_end, 3)}
    for c in cuts:
        if 0.0 < c < total_end:
            hard_bounds.add(round(c, 3))

    prev_layout = layout_override or kfs[0].layout
    for kf in kfs[1:]:
        eff_layout = layout_override or kf.layout
        if eff_layout != prev_layout and 0.0 < kf.t < total_end:
            hard_bounds.add(round(float(kf.t), 3))
        prev_layout = eff_layout

    sorted_bounds = sorted(hard_bounds)
    segments: list[SegmentCrop] = []

    for b_idx in range(len(sorted_bounds) - 1):
        span_start = sorted_bounds[b_idx]
        span_end = sorted_bounds[b_idx + 1]
        if span_end <= span_start + 1e-6:
            continue

        span_dur = span_end - span_start
        n_chunks = max(1, int(math.ceil(span_dur / max_segment_duration_s - 1e-6)))
        chunk_dt = span_dur / n_chunks

        for c_idx in range(n_chunks):
            seg_start = round(span_start + c_idx * chunk_dt, 3)
            seg_end = (
                round(span_end, 3)
                if c_idx == n_chunks - 1
                else round(span_start + (c_idx + 1) * chunk_dt, 3)
            )
            if seg_end <= seg_start + 1e-6:
                continue

            mid_t = seg_start + (seg_end - seg_start) * 0.5
            cx, cy, zoom, seg_layout = _interpolate_keyframe_at(
                mid_t,
                kfs,
                cuts,
                layout_override=layout_override,
            )
            x, y, w, h = compute_clamped_crop_rect(
                cx,
                cy,
                zoom,
                path.source_w,
                path.source_h,
                path.out_aspect,
                target_size=(out_w, out_h),
            )
            if seg_layout == "fit_blur":
                seg_fg = build_fit_blur_filtergraph(
                    out_aspect=path.out_aspect,
                    target_size=(out_w, out_h),
                )
            else:
                seg_fg = (
                    f"crop=w={w}:h={h}:x={x}:y={y}:exact=1,"
                    f"scale={out_w}:{out_h}:flags=bicubic,"
                    f"setsar=1"
                )

            segments.append(
                SegmentCrop(
                    start=seg_start,
                    end=seg_end,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    out_w=out_w,
                    out_h=out_h,
                    layout=seg_layout,
                    filtergraph=seg_fg,
                )
            )

    return segments


def _build_mixed_span_filtergraph(
    path: ReframePath,
    *,
    out_w: int,
    out_h: int,
    sendcmd_path: Path | str | None,
    blur_filter: Literal["boxblur", "gblur"] = "boxblur",
) -> str:
    """Build a single-pass filtergraph combining `fill` and `fit_blur` spans via `overlay=enable=...`."""
    kfs = _sorted_keyframes(path)
    spans: list[tuple[str, float, float | None]] = []
    for kf in kfs:
        t_val = round(float(kf.t), 3)
        layout = kf.layout
        if not spans:
            spans.append((layout, t_val, None))
        elif spans[-1][0] != layout:
            prev_layout, prev_start, _ = spans[-1]
            spans[-1] = (prev_layout, prev_start, t_val)
            spans.append((layout, t_val, None))

    fit_blur_spans = [(s, e) for lay, s, e in spans if lay == "fit_blur"]
    enable_terms: list[str] = []
    for s_t, e_t in fit_blur_spans:
        if e_t is None:
            enable_terms.append(f"gte(t,{s_t:.3f})")
        else:
            enable_terms.append(f"between(t,{s_t:.3f},{e_t:.3f})")
    enable_expr = "+".join(enable_terms) if enable_terms else "0"

    fill_fg = build_fill_filtergraph(
        path,
        out_aspect=path.out_aspect,
        target_size=(out_w, out_h),
        sendcmd_path=sendcmd_path,
    )
    blur_fg = build_fit_blur_filtergraph(
        out_aspect=path.out_aspect,
        target_size=(out_w, out_h),
        blur_filter=blur_filter,
    )
    return (
        f"split=2[fill_src][blur_src];"
        f"[fill_src]{fill_fg}[fill_out];"
        f"[blur_src]{blur_fg}[blur_out];"
        f"[fill_out][blur_out]overlay=0:0:enable='{enable_expr}',setsar=1"
    )


def build_reframe_filter_spec(
    reframe_path: ReframePath | Mapping[str, Any] | Path | str,
    *,
    layout_override: Literal["fill", "fit_blur"] | None = None,
    target_size: tuple[int, int] | None = None,
    work_dir: Path | str | None = None,
    sendcmd_filename: str = "reframe.sendcmd",
    sample_hz: float = DEFAULT_SENDCMD_HZ,
    expand_to_fps: bool = False,
    blur_filter: Literal["boxblur", "gblur"] = "boxblur",
    max_segment_duration_s: float = DEFAULT_SEGMENT_DURATION_S,
) -> ReframeFilterSpec:
    """Unified builder returning filtergraph, optional `sendcmd_path`, output dimensions, and warnings."""
    if layout_override is not None and layout_override not in ("fill", "fit_blur"):
        raise ReframeError("INVALID_LAYOUT", f"Unsupported layout_override: {layout_override!r}")

    path = _coerce_reframe_path(reframe_path)
    out_w, out_h = resolve_output_dimensions(path.out_aspect, target_size)

    warnings = list(path.warnings)
    if path.source_h < 720 and not any("upscal" in w.lower() for w in warnings):
        warnings.append(
            f"upscaling_warning: source height {path.source_h}px < 720px may degrade {path.out_aspect} output quality"
        )

    kfs = _sorted_keyframes(path)
    layouts_in_path = {layout_override or kf.layout for kf in kfs}
    effective_layout: str
    if layout_override is not None:
        effective_layout = layout_override
    elif layouts_in_path == {"fit_blur"}:
        effective_layout = "fit_blur"
    elif layouts_in_path == {"fill"}:
        effective_layout = "fill"
    else:
        effective_layout = "mixed"

    segment_crops = build_segment_crops(
        path,
        max_segment_duration_s=max_segment_duration_s,
        layout_override=layout_override,
        target_size=(out_w, out_h),
    )

    sendcmd_script: str | None = None
    sendcmd_path: Path | None = None
    if effective_layout in ("fill", "mixed"):
        sendcmd_script = build_sendcmd_script(
            path,
            sample_hz=sample_hz,
            expand_to_fps=expand_to_fps,
            layout_override="fill" if effective_layout == "fill" else None,
            target_size=(out_w, out_h),
        )
        if work_dir is not None:
            out_dir = Path(work_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            sendcmd_path = out_dir / sendcmd_filename
            sendcmd_path.write_text(sendcmd_script, encoding="utf-8")

    if effective_layout == "fit_blur":
        filtergraph = build_fit_blur_filtergraph(
            out_aspect=path.out_aspect,
            target_size=(out_w, out_h),
            blur_filter=blur_filter,
        )
    elif effective_layout == "fill":
        filtergraph = build_fill_filtergraph(
            path,
            out_aspect=path.out_aspect,
            target_size=(out_w, out_h),
            sendcmd_path=sendcmd_path,
        )
    else:
        filtergraph = _build_mixed_span_filtergraph(
            path,
            out_w=out_w,
            out_h=out_h,
            sendcmd_path=sendcmd_path,
            blur_filter=blur_filter,
        )

    return ReframeFilterSpec(
        layout=effective_layout,
        out_aspect=path.out_aspect,
        out_w=out_w,
        out_h=out_h,
        filtergraph=filtergraph,
        sendcmd_script=sendcmd_script,
        sendcmd_path=sendcmd_path,
        segment_crops=segment_crops,
        warnings=warnings,
    )
