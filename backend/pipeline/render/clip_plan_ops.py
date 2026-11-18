from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CROSSFADE_PAD_S = 0.040
DEFAULT_MAX_PAUSE_S = 0.600
DEFAULT_TARGET_PAUSE_S = 0.250

FILLER_WORDS: frozenset[str] = frozenset(
    {
        # English
        "um",
        "uh",
        "umm",
        "uhh",
        "er",
        "erm",
        "ah",
        "hmm",
        # Hinglish / Roman Hindi
        "matlab",
        "yaani",
        "yani",
        "accha",
        "acha",
        "haan",
        "ummm",
        # Hindi Devanagari
        "मतलब",
        "यानी",
        "अच्छा",
        "उम्म",
        "अं",
        "हम्म",
        # Bengali
        "মানে",
        "আচ্ছা",
        "উম",
        "অ্যাঁ",
        "হুম",
    }
)

VALID_OVERLAY_TYPES: tuple[str, ...] = (
    "progress_bar",
    "hook_banner",
    "watermark",
    "lower_third",
    "broll_image",
)


@dataclass(slots=True)
class CutListResult:
    """Result of resolving a `ClipPlan` into removed intervals (`cut_list`) and kept intervals (`keep_intervals`)."""

    clip_start: float
    clip_end: float
    cut_list: list[tuple[float, float]]
    keep_intervals: list[tuple[float, float]]
    crossfade_ms: int = 40
    removed_fillers_count: int = 0
    trimmed_silences_count: int = 0

    @property
    def output_duration(self) -> float:
        return round(sum(max(0.0, e - s) for s, e in self.keep_intervals), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_start": round(self.clip_start, 3),
            "clip_end": round(self.clip_end, 3),
            "cut_list": [(round(s, 3), round(e, 3)) for s, e in self.cut_list],
            "keep_intervals": [(round(s, 3), round(e, 3)) for s, e in self.keep_intervals],
            "crossfade_ms": self.crossfade_ms,
            "output_duration": self.output_duration,
            "removed_fillers_count": self.removed_fillers_count,
            "trimmed_silences_count": self.trimmed_silences_count,
        }


@dataclass(slots=True)
class OverlayDescriptor:
    """Normalized overlay descriptor extracted from `ClipPlan.overlays` and optional `brand_kit`."""

    kind: str
    template_id: str
    start: float
    end: float
    text: str = ""
    asset: str | None = None
    position: str = "top_center"
    y_percent: float = 14.0
    color: str = "#FFE600"
    bg_color: str = "#000000CC"
    height_px: int = 10
    opacity: float = 0.9
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "template_id": self.template_id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "asset": self.asset,
            "position": self.position,
            "y_percent": self.y_percent,
            "color": self.color,
            "bg_color": self.bg_color,
            "height_px": self.height_px,
            "opacity": self.opacity,
            "params": dict(self.params),
        }


def normalize_cut_list(
    cut_list: Sequence[tuple[float, float] | list[float]],
    *,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> list[tuple[float, float]]:
    """Sort, clamp, and merge overlapping or adjacent cut intervals `[(s, e), ...]`."""
    raw: list[tuple[float, float]] = []
    for item in cut_list:
        if len(item) < 2:
            continue
        s = float(item[0])
        e = float(item[1])
        if clip_start is not None:
            s = max(float(clip_start), s)
        if clip_end is not None:
            e = min(float(clip_end), e)
        if e > s + 1e-6:
            raw.append((s, e))

    if not raw:
        return []

    raw.sort(key=lambda iv: (iv[0], iv[1]))
    merged: list[tuple[float, float]] = [raw[0]]
    for s, e in raw[1:]:
        prev_s, prev_e = merged[-1]
        if s <= prev_e + 1e-6:
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return [(round(s, 3), round(e, 3)) for s, e in merged]


def removed_duration_before(
    t_src: float,
    cut_list: Sequence[tuple[float, float] | list[float]],
    clip_start: float = 0.0,
) -> float:
    """Compute cumulative removed duration inside `[clip_start, t_src]` from `cut_list`."""
    if t_src <= clip_start:
        return 0.0
    merged = normalize_cut_list(cut_list, clip_start=clip_start)
    removed = 0.0
    for cut_s, cut_e in merged:
        if cut_s >= t_src:
            break
        eff_s = max(clip_start, cut_s)
        eff_e = min(t_src, cut_e)
        if eff_e > eff_s:
            removed += eff_e - eff_s
    return removed


def map_source_to_output_time(
    t_src: float,
    cut_list: list[tuple[float, float]] | Sequence[tuple[float, float] | list[float]],
    clip_start: float = 0.0,
) -> float:
    """Single timeline mapping function: `t_out = (t_src - clip_start) - removed_duration_before(t_src)`.

    Used consistently across video keep-intervals, caption word timestamps, reframe keyframes, and overlay triggers.
    """
    t = float(t_src)
    base_start = float(clip_start)
    if t <= base_start:
        return 0.0
    rem = removed_duration_before(t, cut_list, clip_start=base_start)
    return round(max(0.0, (t - base_start) - rem), 3)


def compute_keep_intervals(
    clip_start: float,
    clip_end: float,
    cut_list: Sequence[tuple[float, float] | list[float]],
) -> list[tuple[float, float]]:
    """Compute complementary kept intervals inside `[clip_start, clip_end]` after subtracting `cut_list`."""
    s_bound = float(clip_start)
    e_bound = max(s_bound, float(clip_end))
    merged_cuts = normalize_cut_list(cut_list, clip_start=s_bound, clip_end=e_bound)
    keep: list[tuple[float, float]] = []
    cursor = s_bound
    for cut_s, cut_e in merged_cuts:
        if cut_s > cursor + 1e-4:
            keep.append((round(cursor, 3), round(cut_s, 3)))
        cursor = max(cursor, cut_e)
    if e_bound > cursor + 1e-4:
        keep.append((round(cursor, 3), round(e_bound, 3)))
    return keep


def _clean_word_token(raw: str) -> str:
    return raw.strip().strip(".,!?;:\"'()[]{}।॥—–-").lower()


def is_filler_word(word_obj: Mapping[str, Any] | str) -> bool:
    """Return True if `word_obj` is flagged as `"filler"` or matches the multilingual filler vocabulary."""
    if isinstance(word_obj, str):
        return _clean_word_token(word_obj) in FILLER_WORDS
    flags = word_obj.get("flags")
    if isinstance(flags, Sequence) and not isinstance(flags, (str, bytes)):
        if any(str(f).lower() == "filler" for f in flags):
            return True
    if word_obj.get("is_filler") is True:
        return True
    txt = str(word_obj.get("w") if "w" in word_obj else word_obj.get("word") or "")
    return _clean_word_token(txt) in FILLER_WORDS


def build_cut_list(
    clip_plan: Mapping[str, Any],
    transcript_words: Sequence[Mapping[str, Any]] | None = None,
    silence_spans: Sequence[tuple[float, float] | list[float]] | None = None,
    *,
    crossfade_pad_s: float = DEFAULT_CROSSFADE_PAD_S,
    default_max_pause_s: float = DEFAULT_MAX_PAUSE_S,
    target_pause_s: float = DEFAULT_TARGET_PAUSE_S,
) -> CutListResult:
    """Build concrete `cut_list` and `keep_intervals` from `clip_plan` segments, `remove_fillers`, and `trim_silence`.

    - Multi-segment jump cuts: any gap between `segments[i].end` and `segments[i+1].start` is added to `cut_list`.
    - `remove_fillers`: cuts filler tokens while leaving `crossfade_pad_s` (40ms default) audio crossfade padding.
    - `trim_silence`: collapses pauses > `max_pause_s` (default 0.6s) down to `target_pause_s` (0.25s).
    """
    raw_segments = clip_plan.get("segments")
    if isinstance(raw_segments, Sequence) and len(raw_segments) > 0:
        segs = sorted(
            [
                (float(s.get("start", 0.0)), float(s.get("end", 0.0)))
                for s in raw_segments
                if isinstance(s, Mapping) and float(s.get("end", 0.0)) > float(s.get("start", 0.0))
            ],
            key=lambda x: x[0],
        )
    else:
        s_val = float(clip_plan.get("start", 0.0))
        e_val = float(clip_plan.get("end", max(s_val + 1.0, 10.0)))
        segs = [(s_val, e_val)]

    if not segs:
        segs = [(0.0, 1.0)]

    clip_start = segs[0][0]
    clip_end = segs[-1][1]

    raw_cuts: list[tuple[float, float]] = []
    # 1. Gaps between explicit jump-cut segments in clip_plan.segments
    for idx in range(len(segs) - 1):
        gap_s = segs[idx][1]
        gap_e = segs[idx + 1][0]
        if gap_e > gap_s + 1e-4:
            raw_cuts.append((gap_s, gap_e))

    # Parse enabled ops from clip_plan.ops
    remove_fillers_enabled = False
    trim_silence_enabled = False
    max_pause_s = float(default_max_pause_s)

    ops = clip_plan.get("ops")
    if isinstance(ops, Sequence):
        for op_entry in ops:
            if not isinstance(op_entry, Mapping):
                continue
            op_name = str(op_entry.get("op") or "").strip().lower()
            enabled = bool(op_entry.get("enabled", True))
            if op_name == "remove_fillers":
                remove_fillers_enabled = enabled
            elif op_name == "trim_silence":
                trim_silence_enabled = enabled
                if "max_gap_s" in op_entry and op_entry["max_gap_s"] is not None:
                    max_pause_s = max(float(target_pause_s), float(op_entry["max_gap_s"]))
                elif "max_pause_s" in op_entry and op_entry["max_pause_s"] is not None:
                    max_pause_s = max(float(target_pause_s), float(op_entry["max_pause_s"]))

    words_in_clip: list[dict[str, Any]] = []
    if transcript_words:
        for w in transcript_words:
            if not isinstance(w, Mapping):
                continue
            raw_ws = w.get("s") if "s" in w else w.get("start")
            ws = float(raw_ws if raw_ws is not None else 0.0)
            raw_we = w.get("e") if "e" in w else w.get("end")
            we = float(raw_we if raw_we is not None else ws)
            if we <= clip_start or ws >= clip_end:
                continue
            words_in_clip.append(
                {
                    "w": str(w.get("w") if "w" in w else w.get("word") or ""),
                    "s": max(clip_start, ws),
                    "e": min(clip_end, we),
                    "flags": list(w.get("flags") or []),
                    "is_filler": bool(w.get("is_filler", False)),
                }
            )
        words_in_clip.sort(key=lambda x: (x["s"], x["e"]))

    removed_fillers_count = 0
    if remove_fillers_enabled and words_in_clip:
        pad = max(0.0, float(crossfade_pad_s))
        for w in words_in_clip:
            if is_filler_word(w):
                ws = float(w["s"])
                we = float(w["e"])
                dur = we - ws
                # Apply 40ms audio crossfade padding inside or around the filler cut
                if dur > 2.0 * pad + 0.02:
                    cut_s = ws + pad
                    cut_e = we - pad
                else:
                    cut_s = ws
                    cut_e = we
                if cut_e > cut_s + 1e-3:
                    raw_cuts.append((cut_s, cut_e))
                    removed_fillers_count += 1

    trimmed_silences_count = 0
    if trim_silence_enabled:
        candidate_pauses: list[tuple[float, float]] = []
        if silence_spans:
            for sp in silence_spans:
                if len(sp) >= 2:
                    ps = max(clip_start, float(sp[0]))
                    pe = min(clip_end, float(sp[1]))
                    if pe > ps:
                        candidate_pauses.append((ps, pe))
        elif words_in_clip:
            for idx in range(len(words_in_clip) - 1):
                gap_s = float(words_in_clip[idx]["e"])
                gap_e = float(words_in_clip[idx + 1]["s"])
                if gap_e > gap_s:
                    candidate_pauses.append((gap_s, gap_e))

        keep_pause = max(0.05, float(target_pause_s))
        half_keep = keep_pause * 0.5
        for ps, pe in candidate_pauses:
            pause_dur = pe - ps
            if pause_dur > max_pause_s + 1e-4:
                # Collapse pause > max_pause_s down to target_pause_s (0.25s), keeping symmetric natural context
                cut_s = ps + half_keep
                cut_e = pe - half_keep
                if cut_e > cut_s + 1e-3:
                    raw_cuts.append((cut_s, cut_e))
                    trimmed_silences_count += 1

    merged_cuts = normalize_cut_list(raw_cuts, clip_start=clip_start, clip_end=clip_end)
    keep_intervals = compute_keep_intervals(clip_start, clip_end, merged_cuts)

    return CutListResult(
        clip_start=round(clip_start, 3),
        clip_end=round(clip_end, 3),
        cut_list=merged_cuts,
        keep_intervals=keep_intervals,
        crossfade_ms=int(round(crossfade_pad_s * 1000)),
        removed_fillers_count=removed_fillers_count,
        trimmed_silences_count=trimmed_silences_count,
    )


def remap_words_to_output_timeline(
    words: Sequence[Mapping[str, Any]],
    cut_list: Sequence[tuple[float, float] | list[float]],
    clip_start: float = 0.0,
    clip_end: float | None = None,
    *,
    drop_cut_words: bool = True,
    word_overrides: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Remap transcript word timestamps from source timeline to edited clip output timeline using `map_source_to_output_time`."""
    merged_cuts = normalize_cut_list(cut_list, clip_start=clip_start, clip_end=clip_end)
    remapped: list[dict[str, Any]] = []

    for idx, w in enumerate(words):
        raw_ws = w.get("s") if "s" in w else w.get("start")
        ws = float(raw_ws if raw_ws is not None else 0.0)
        raw_we = w.get("e") if "e" in w else w.get("end")
        we = float(raw_we if raw_we is not None else ws)
        if we <= clip_start + 1e-4:
            continue
        if clip_end is not None and ws >= clip_end - 1e-4:
            continue

        ws_clamped = max(clip_start, ws)
        we_clamped = min(clip_end, we) if clip_end is not None else we
        if we_clamped <= ws_clamped:
            continue

        # Check if word is completely or mostly inside a cut interval
        overlap_cut = 0.0
        for cs, ce in merged_cuts:
            ov_s = max(ws_clamped, cs)
            ov_e = min(we_clamped, ce)
            if ov_e > ov_s:
                overlap_cut += ov_e - ov_s
        if drop_cut_words and overlap_cut >= (we_clamped - ws_clamped) * 0.5:
            continue

        out_s = map_source_to_output_time(ws_clamped, merged_cuts, clip_start=clip_start)
        out_e = map_source_to_output_time(we_clamped, merged_cuts, clip_start=clip_start)
        if out_e <= out_s:
            out_e = round(out_s + 0.04, 3)

        txt = str(w.get("w") if "w" in w else w.get("word") or "").strip()
        if word_overrides and str(idx) in word_overrides:
            txt = str(word_overrides[str(idx)]).strip()
        if not txt:
            continue

        entry: dict[str, Any] = dict(w)
        entry["w"] = txt
        entry["s"] = out_s
        entry["e"] = out_e
        remapped.append(entry)

    return remapped


def _classify_overlay_kind(raw_template_id: str, overlay_entry: Mapping[str, Any]) -> str:
    explicit = str(overlay_entry.get("kind") or overlay_entry.get("type") or "").strip().lower()
    if explicit in VALID_OVERLAY_TYPES:
        return explicit
    slug = raw_template_id.split("@", 1)[0].strip().lower()
    if "progress" in slug:
        return "progress_bar"
    if "hook" in slug or "banner" in slug or "title" in slug:
        return "hook_banner"
    if "watermark" in slug or "logo" in slug:
        return "watermark"
    if "lower_third" in slug or "speaker" in slug:
        return "lower_third"
    if "broll" in slug or "image" in slug or "sticker" in slug:
        return "broll_image"
    return "hook_banner"


def extract_overlay_descriptors(
    clip_plan: Mapping[str, Any],
    *,
    cut_list: Sequence[tuple[float, float] | list[float]] | None = None,
    clip_start: float | None = None,
    output_duration: float | None = None,
    variables: Mapping[str, str] | None = None,
    brand_kit_data: Mapping[str, Any] | None = None,
) -> list[OverlayDescriptor]:
    """Extract and timeline-map overlay descriptors (`progress_bar`, `hook_banner`, `watermark`, `lower_third`, `broll_image`).

    Applies `map_source_to_output_time` to source-relative overlay triggers when `times_are_source=True` or when
    timestamps lie inside `[clip_start, clip_end]`.
    """
    raw_segments = clip_plan.get("segments")
    if clip_start is None:
        if isinstance(raw_segments, Sequence) and raw_segments and isinstance(raw_segments[0], Mapping):
            base_start = float(raw_segments[0].get("start", 0.0))
            base_end = float(raw_segments[-1].get("end", base_start + 15.0))
        else:
            base_start = float(clip_plan.get("start", 0.0))
            base_end = float(clip_plan.get("end", base_start + 15.0))
    else:
        base_start = float(clip_start)
        base_end = base_start + float(output_duration or 15.0)

    cuts = normalize_cut_list(cut_list or [], clip_start=base_start)
    if output_duration is None:
        keep = compute_keep_intervals(base_start, base_end, cuts)
        total_out_dur = round(sum(max(0.0, e - s) for s, e in keep), 3) or max(1.0, base_end - base_start)
    else:
        total_out_dur = max(0.1, float(output_duration))

    var_map = dict(variables or {})
    descriptors: list[OverlayDescriptor] = []

    raw_overlays = clip_plan.get("overlays")
    if isinstance(raw_overlays, Sequence):
        for item in raw_overlays[:4]:
            if not isinstance(item, Mapping):
                continue
            tpl_id = str(item.get("template") or "hook_banner@1")
            kind = _classify_overlay_kind(tpl_id, item)
            raw_text = str(item.get("text") or "")
            for k, v in var_map.items():
                raw_text = raw_text.replace(f"{{{{{k}}}}}", str(v))

            t_range = item.get("t")
            if isinstance(t_range, Sequence) and len(t_range) >= 2:
                raw_s = float(t_range[0])
                raw_e = float(t_range[1])
                # If timestamps are in source media coordinates (>= base_start and base_start > 0), map via cut_list
                if item.get("source_time") is True or (base_start > 0.0 and raw_s >= base_start):
                    ov_s = map_source_to_output_time(raw_s, cuts, clip_start=base_start)
                    ov_e = map_source_to_output_time(raw_e, cuts, clip_start=base_start)
                else:
                    # Relative to clip start (0..duration), still account for any cuts before base_start + raw_t
                    ov_s = map_source_to_output_time(base_start + raw_s, cuts, clip_start=base_start)
                    ov_e = map_source_to_output_time(base_start + raw_e, cuts, clip_start=base_start)
                if ov_e <= ov_s:
                    ov_e = min(total_out_dur, ov_s + max(0.25, raw_e - raw_s))
            else:
                if kind == "progress_bar" or kind == "watermark":
                    ov_s, ov_e = 0.0, total_out_dur
                elif kind == "lower_third":
                    ov_s, ov_e = 0.5, min(total_out_dur, 4.5)
                else:
                    ov_s, ov_e = 0.0, min(total_out_dur, 2.5)

            default_pos = {
                "progress_bar": ("bottom_center", 98.0),
                "hook_banner": ("top_center", 14.0),
                "watermark": ("top_right", 8.0),
                "lower_third": ("bottom_left", 78.0),
                "broll_image": ("center", 45.0),
            }.get(kind, ("top_center", 14.0))

            descriptors.append(
                OverlayDescriptor(
                    kind=kind,
                    template_id=tpl_id,
                    start=round(max(0.0, ov_s), 3),
                    end=round(min(total_out_dur, max(ov_s + 0.1, ov_e)), 3),
                    text=raw_text,
                    asset=str(item["asset"]) if item.get("asset") else None,
                    position=str(item.get("position") or default_pos[0]),
                    y_percent=float(item.get("y_percent", default_pos[1])),
                    color=str(item.get("color") or "#FFE600"),
                    bg_color=str(item.get("bg_color") or "#000000CC"),
                    height_px=int(item.get("height_px", 10)),
                    opacity=float(item.get("opacity", 0.9)),
                    params=dict(item.get("params") or {}),
                )
            )

    # Also check brand_kit watermark/logo if provided and not already present
    if isinstance(brand_kit_data, Mapping) and not any(d.kind == "watermark" for d in descriptors):
        wm = brand_kit_data.get("watermark") or brand_kit_data.get("logo")
        if isinstance(wm, Mapping) and wm.get("asset"):
            descriptors.append(
                OverlayDescriptor(
                    kind="watermark",
                    template_id=str((brand_kit_data.get("meta") or {}).get("id") or "brand_watermark@1"),
                    start=0.0,
                    end=total_out_dur,
                    text="",
                    asset=str(wm.get("asset")),
                    position=str(wm.get("anchor") or "top_right"),
                    y_percent=8.0,
                    opacity=float(wm.get("opacity", 0.8)),
                    params=dict(wm),
                )
            )

    return descriptors


def compute_clip_plan_hash(
    clip_plan: Mapping[str, Any],
    *,
    template_versions: Mapping[str, Any] | None = None,
) -> str:
    """Compute a deterministic SHA-256 hash of a `ClipPlan` and resolved template versions."""
    canonical = {
        "plan": dict(clip_plan),
        "templates": dict(template_versions or {}),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
