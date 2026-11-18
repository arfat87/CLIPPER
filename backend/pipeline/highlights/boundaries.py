from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]

from backend.app.config import ROOT_DIR
from backend.pipeline.highlights.segmenter import (
    SENTENCE_END_PUNCT,
    ThoughtUnit,
    flatten_transcript_words,
    load_highlights_v2_config,
    split_into_thought_units,
)


@dataclass(slots=True)
class SnappedBoundary:
    """Result of snapping a candidate clip's start and end boundaries."""

    start: float
    end: float
    duration: float
    start_in_silence: bool
    end_in_silence: bool
    avoided_cuts: int
    has_opening_speech: bool
    clean_trailing_tail: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "start_in_silence": self.start_in_silence,
            "end_in_silence": self.end_in_silence,
            "avoided_cuts": self.avoided_cuts,
            "has_opening_speech": self.has_opening_speech,
            "clean_trailing_tail": self.clean_trailing_tail,
            "warnings": list(self.warnings),
        }


def _extract_silence_intervals(
    words: list[dict[str, Any]],
    silences: list[list[float] | tuple[float, float]] | None = None,
    video_duration: float | None = None,
) -> list[tuple[float, float]]:
    """Build sorted list of silence intervals [(s, e), ...] from VAD silences or inter-word gaps."""
    intervals: list[tuple[float, float]] = []
    if silences:
        for item in silences:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                s = float(item[0])
                e = float(item[1])
                if e >= s:
                    intervals.append((s, e))

    # Also derive inter-word gaps >= 0.05s from word timestamps so silence checks work even without VAD file
    if words:
        first_s = float(words[0]["s"])
        if first_s > 0.0:
            intervals.append((0.0, first_s))
        for i in range(len(words) - 1):
            w_end = float(words[i]["e"])
            next_start = float(words[i + 1]["s"])
            if next_start > w_end:
                intervals.append((w_end, next_start))
        last_e = float(words[-1]["e"])
        max_t = max(last_e + 10.0, float(video_duration or (last_e + 10.0)))
        intervals.append((last_e, max_t))
    elif not intervals:
        intervals.append((0.0, float(video_duration or 3600.0)))

    intervals.sort(key=lambda x: (x[0], x[1]))
    merged: list[tuple[float, float]] = []
    for s, e in intervals:
        if not merged or s > merged[-1][1] + 1e-4:
            merged.append((s, e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
    return merged


def is_timestamp_in_silence(
    t: float,
    silences: list[tuple[float, float]],
    tolerance_s: float = 0.050,
) -> bool:
    """Return True if timestamp `t` lies inside any silence interval within `+-tolerance_s`."""
    for s, e in silences:
        if (s - tolerance_s) <= t <= (e + tolerance_s):
            return True
    return False


def _clamp_to_nearest_silence(
    t: float,
    silences: list[tuple[float, float]],
    max_shift_s: float = 2.0,
) -> float:
    """If `t` is not already in silence, move `t` to the nearest point inside a silence interval."""
    if not silences:
        return t
    best_t = t
    best_dist = math.inf
    for s, e in silences:
        if s <= t <= e:
            return t
        cand = s if abs(t - s) <= abs(t - e) else e
        dist = abs(t - cand)
        if dist < best_dist and dist <= max_shift_s:
            best_dist = dist
            best_t = cand
    return best_t


def _extract_scene_cut_times(
    scenes: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[float]:
    if scenes is None:
        return []
    raw_list = scenes.get("scenes", []) if isinstance(scenes, dict) else scenes
    cuts: list[float] = []
    if isinstance(raw_list, list):
        for sc in raw_list:
            if isinstance(sc, dict):
                if "start" in sc and float(sc["start"]) > 0.0:
                    cuts.append(float(sc["start"]))
                if "end" in sc and float(sc["end"]) > 0.0:
                    cuts.append(float(sc["end"]))
            elif isinstance(sc, (int, float)):
                cuts.append(float(sc))
    return sorted(set(cuts))


def _avoid_scene_cuts(
    t: float,
    cut_times: list[float],
    cut_avoid_s: float,
    silences: list[tuple[float, float]],
    tolerance_s: float,
    *,
    is_start: bool,
) -> tuple[float, int]:
    """Shift boundary `t` outside `+-cut_avoid_s` of any scene cut while staying in silence."""
    avoided = 0
    for c in cut_times:
        if abs(t - c) < cut_avoid_s:
            avoided += 1
            # Prefer shifting away from the cut into silence
            cand_before = max(0.0, c - cut_avoid_s - 0.01)
            cand_after = c + cut_avoid_s + 0.01
            options = [cand_before, cand_after] if is_start else [cand_after, cand_before]
            chosen = t
            for opt in options:
                snapped_opt = _clamp_to_nearest_silence(opt, silences, max_shift_s=1.0)
                if abs(snapped_opt - c) >= cut_avoid_s and is_timestamp_in_silence(
                    snapped_opt, silences, tolerance_s
                ):
                    chosen = snapped_opt
                    break
            else:
                chosen = options[0]
            t = chosen
    return t, avoided


def snap_clip_boundaries(
    raw_start: float,
    raw_end: float,
    transcript: dict[str, Any],
    *,
    thought_units: list[ThoughtUnit] | None = None,
    silences: list[list[float] | tuple[float, float]] | None = None,
    scenes: list[dict[str, Any]] | dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    min_len_s: float = 15.0,
    max_len_s: float = 90.0,
) -> SnappedBoundary:
    """Snap candidate clip start/end boundaries to natural thought units & silence per Spec v2 Step E.

    Rules:
    - Start: nearest thought-unit start within +-2.0s lying in a VAD silence (+-50 ms tolerance).
    - End: sentence end + 0.3s tail lying in silence.
    - Avoid scene cuts within 0.4s of a boundary.
    - Ensure opening 3.0s contains speech (hook).
    - Ensure trailing 0.3s has no new word starting inside [end - 0.3s, end].
    """
    cfg = config if config is not None else load_highlights_v2_config()
    b_cfg = dict(cfg.get("boundaries", {}))
    start_search_s = float(b_cfg.get("start_search_s", 2.0))
    end_tail_s = float(b_cfg.get("end_tail_s", 0.3))
    cut_avoid_s = float(b_cfg.get("cut_avoid_s", 0.4))
    tolerance_s = float(b_cfg.get("silence_tolerance_ms", 50.0)) / 1000.0
    require_speech_s = float(b_cfg.get("require_speech_in_first_s", 3.0))

    words = flatten_transcript_words(transcript)
    units = (
        thought_units
        if thought_units is not None
        else split_into_thought_units(transcript)
    )
    video_dur = float(transcript.get("duration", 0.0) or 0.0)
    if words and video_dur <= 0.0:
        video_dur = float(words[-1]["e"]) + 5.0

    silence_intervals = _extract_silence_intervals(
        words, silences=silences, video_duration=video_dur
    )
    cut_times = _extract_scene_cut_times(scenes)
    warnings: list[str] = []

    # 1. Snap start to nearest thought-unit start within +-start_search_s lying in VAD silence
    snapped_start = raw_start
    if units:
        candidate_starts: list[tuple[float, float]] = []
        for u in units:
            dist = abs(u.start - raw_start)
            if dist <= start_search_s:
                if is_timestamp_in_silence(u.start, silence_intervals, tolerance_s):
                    candidate_starts.append((dist, u.start))
        if candidate_starts:
            candidate_starts.sort(key=lambda x: x[0])
            snapped_start = candidate_starts[0][1]
        else:
            # Nearest thought-unit start even if we must clamp to silence
            nearest_u = min(units, key=lambda u: abs(u.start - raw_start))
            if abs(nearest_u.start - raw_start) <= start_search_s:
                snapped_start = nearest_u.start

    snapped_start = _clamp_to_nearest_silence(
        snapped_start, silence_intervals, max_shift_s=start_search_s
    )

    # 2. Ensure opening `require_speech_s` (3.0s) contains speech
    if words:
        words_after_start = [w for w in words if float(w["e"]) > snapped_start]
        if words_after_start:
            first_w_start = float(words_after_start[0]["s"])
            if first_w_start - snapped_start > require_speech_s:
                # Advance start to just before first word (still in silence at word boundary)
                snapped_start = max(0.0, first_w_start - 0.05)
                snapped_start = _clamp_to_nearest_silence(
                    snapped_start, silence_intervals, max_shift_s=1.0
                )

    # 3. Snap end to sentence/thought-unit end + 0.3s tail
    snapped_end = raw_end
    if units:
        # Prefer units that end with sentence punctuation within +-start_search_s of raw_end
        punct_units = [
            u
            for u in units
            if abs(u.end - raw_end) <= start_search_s
            and u.text.strip().rstrip("\"'”’)]}").endswith(SENTENCE_END_PUNCT)
        ]
        if punct_units:
            best_end_u = min(punct_units, key=lambda u: abs(u.end - raw_end))
            word_end_t = best_end_u.end
        else:
            best_end_u = min(units, key=lambda u: abs(u.end - raw_end))
            word_end_t = (
                best_end_u.end
                if abs(best_end_u.end - raw_end) <= start_search_s
                else raw_end
            )
    elif words:
        nearest_w = min(words, key=lambda w: abs(float(w["e"]) - raw_end))
        word_end_t = float(nearest_w["e"])
    else:
        word_end_t = raw_end

    desired_end = word_end_t + end_tail_s

    # 4. Ensure trailing 0.3s has no new word starting inside (word_end_t, desired_end)
    if words:
        intruding = [
            w
            for w in words
            if word_end_t + 1e-4 < float(w["s"]) < desired_end - 1e-4
        ]
        if intruding:
            next_w_start = min(float(w["s"]) for w in intruding)
            # Cap before next word starts so we don't clip into a new word
            desired_end = max(word_end_t, next_w_start - 0.02)
            warnings.append("Shortened trailing tail to avoid starting next word.")

    snapped_end = _clamp_to_nearest_silence(
        desired_end, silence_intervals, max_shift_s=start_search_s
    )

    # Re-verify trailing 0.3s has no new word after silence clamping
    if words:
        last_included = [w for w in words if float(w["e"]) <= snapped_end + 1e-4]
        if last_included:
            last_w_e = float(last_included[-1]["e"])
            # Check if any word starts after last_w_e and before snapped_end
            bad_words = [
                w
                for w in words
                if float(w["s"]) >= last_w_e - 1e-4 and float(w["s"]) < snapped_end - 1e-4
                and float(w["e"]) > snapped_end
            ]
            if bad_words:
                snapped_end = float(bad_words[0]["s"]) - 0.02

    # 5. Avoid scene cuts within cut_avoid_s (0.4s)
    snapped_start, avoided_s = _avoid_scene_cuts(
        snapped_start,
        cut_times,
        cut_avoid_s,
        silence_intervals,
        tolerance_s,
        is_start=True,
    )
    snapped_end, avoided_e = _avoid_scene_cuts(
        snapped_end,
        cut_times,
        cut_avoid_s,
        silence_intervals,
        tolerance_s,
        is_start=False,
    )

    if snapped_end <= snapped_start + 1.0:
        snapped_end = max(snapped_start + min_len_s, raw_end)

    # Final check properties
    start_in_sil = is_timestamp_in_silence(snapped_start, silence_intervals, tolerance_s)
    end_in_sil = is_timestamp_in_silence(snapped_end, silence_intervals, tolerance_s)

    has_opening_speech = True
    clean_trailing_tail = True
    if words:
        has_opening_speech = any(
            float(w["s"]) <= snapped_start + require_speech_s and float(w["e"]) > snapped_start
            for w in words
        )
        # No new word starts in trailing (snapped_end - end_tail_s, snapped_end)
        clean_trailing_tail = not any(
            (snapped_end - end_tail_s + 1e-3) < float(w["s"]) < (snapped_end - 1e-3)
            for w in words
        )

    return SnappedBoundary(
        start=round(max(0.0, snapped_start), 3),
        end=round(snapped_end, 3),
        duration=round(snapped_end - max(0.0, snapped_start), 3),
        start_in_silence=start_in_sil,
        end_in_silence=end_in_sil,
        avoided_cuts=avoided_s + avoided_e,
        has_opening_speech=has_opening_speech,
        clean_trailing_tail=clean_trailing_tail,
        warnings=warnings,
    )


def load_clip_plan_schema() -> dict[str, Any]:
    """Load `contracts/schemas/clip_plan.schema.json`."""
    schema_path = ROOT_DIR / "contracts" / "schemas" / "clip_plan.schema.json"
    res: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))
    return res


def validate_clip_plan(plan: dict[str, Any]) -> None:
    """Validate a `clip_plan.json` dictionary against `contracts/schemas/clip_plan.schema.json`."""
    schema = load_clip_plan_schema()
    jsonschema.validate(instance=plan, schema=schema)


def build_clip_plan(
    clip_id: str,
    start: float,
    end: float,
    *,
    source_media: str = "media/mezzanine.mp4",
    layout_template: str = "auto_reframe@1",
    layout_force: str | None = None,
    caption_template: str = "bold_pop@1",
    script: str = "latn",
    caption_overrides: dict[str, Any] | None = None,
    aspect: str = "9:16",
    profile: str = "reels_1080p",
    reframe_path: str = "reframe_path.json",
    brand_kit: str | None = None,
    loudness_lufs: float = -14.0,
    fade_in_ms: int = 80,
    fade_out_ms: int = 120,
    remove_fillers: bool = False,
    trim_silence: bool = False,
    max_gap_s: float = 0.6,
    overlays: list[dict[str, Any]] | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Build and schema-validate a `clip_plan.json` dict (and optionally write to `output_path`)."""
    # Normalize template version tags to match `^[a-z][a-z0-9_]*@(latest|[0-9]+)$`
    if "@" not in layout_template:
        layout_template = f"{layout_template}@1"
    if "@" not in caption_template:
        caption_template = f"{caption_template}@1"

    valid_script = script if script in {"latn", "deva", "beng"} else "latn"
    valid_aspect = aspect if aspect in {"9:16", "1:1", "4:5"} else "9:16"

    plan: dict[str, Any] = {
        "schema_version": 1,
        "clip_id": str(clip_id),
        "source": {"media": str(source_media)},
        "segments": [
            {
                "start": round(max(0.0, float(start)), 3),
                "end": round(max(float(start) + 0.1, float(end)), 3),
            }
        ],
        "ops": [
            {"op": "remove_fillers", "enabled": bool(remove_fillers)},
            {
                "op": "trim_silence",
                "enabled": bool(trim_silence),
                "max_gap_s": round(max(0.1, float(max_gap_s)), 2),
            },
        ],
        "layout": {
            "template": layout_template,
            "force": layout_force,
        },
        "reframe": {"path": reframe_path},
        "captions": {
            "template": caption_template,
            "script": valid_script,
            "overrides": dict(caption_overrides or {}),
        },
        "overlays": list(overlays or [])[:4],
        "brand_kit": brand_kit,
        "audio": {
            "loudness_lufs": float(loudness_lufs),
            "fade_in_ms": int(fade_in_ms),
            "fade_out_ms": int(fade_out_ms),
        },
        "output": {
            "aspect": valid_aspect,
            "profile": str(profile),
        },
    }

    validate_clip_plan(plan)

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return plan
