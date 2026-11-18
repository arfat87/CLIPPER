from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from backend.app.config import get_settings

SENTENCE_END_PUNCT = (".", "!", "?", "।", "॥", "؟")


@dataclass(slots=True)
class ThoughtUnit:
    """A self-contained phrase or sentence bounded by pause > pause_split_s or punctuation."""

    id: int
    start: float
    end: float
    w0: int
    w1: int
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)
    energy: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "w0": self.w0,
            "w1": self.w1,
            "text": self.text,
            "words": self.words,
            "energy": self.energy,
        }


@dataclass(slots=True)
class CandidateWindow:
    """A candidate clip window aligned strictly to word/thought-unit boundaries."""

    id: str
    start: float
    end: float
    duration: float
    w0: int
    w1: int
    unit_ids: list[int]
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)
    signals: dict[str, float | None] = field(default_factory=dict)
    topic_id: str | int | None = None
    topic_title: str | None = None
    energy: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "duration": round(self.duration, 3),
            "w0": self.w0,
            "w1": self.w1,
            "unit_ids": list(self.unit_ids),
            "text": self.text,
            "words": list(self.words),
            "signals": dict(self.signals),
            "topic_id": self.topic_id,
            "topic_title": self.topic_title,
            "energy": self.energy if self.energy is not None else 0.75,
        }


def load_highlights_v2_config() -> dict[str, Any]:
    """Load Spec v2 highlight engine configuration (`config/spec_v2/highlights.yaml`)."""
    settings = get_settings()
    return settings.load_spec_v2_yaml_config("highlights")


def resolve_window_bounds(
    genre: str | None = None,
    config: dict[str, Any] | None = None,
    *,
    min_len_s: float | None = None,
    max_len_s: float | None = None,
    target_len_s: float | None = None,
    pause_split_s: float | None = None,
) -> dict[str, float]:
    """Resolve window duration bounds with genre overrides (e.g., lecture 40-90s, gaming 15-45s)."""
    cfg = config if config is not None else load_highlights_v2_config()
    win_cfg = dict(cfg.get("windows", {}))
    base_min = float(win_cfg.get("min_len_s", cfg.get("min_clip_seconds", 20.0)))
    base_max = float(win_cfg.get("max_len_s", cfg.get("max_clip_seconds", 60.0)))
    base_target = float(win_cfg.get("target_len_s", 35.0))
    base_pause = float(win_cfg.get("pause_split_s", 0.6))

    if genre:
        g_key = genre.strip().lower()
        overrides = cfg.get("genre_overrides", {}).get(g_key, {})
        if isinstance(overrides, dict):
            base_min = float(overrides.get("min_len_s", base_min))
            base_max = float(overrides.get("max_len_s", base_max))
            base_target = float(overrides.get("target_len_s", base_target))

    resolved_min = float(min_len_s) if min_len_s is not None else base_min
    resolved_max = float(max_len_s) if max_len_s is not None else base_max
    resolved_target = float(target_len_s) if target_len_s is not None else base_target
    resolved_pause = float(pause_split_s) if pause_split_s is not None else base_pause

    return {
        "min_len_s": resolved_min,
        "max_len_s": resolved_max,
        "target_len_s": resolved_target,
        "pause_split_s": resolved_pause,
    }


def flatten_transcript_words(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a flat, globally indexed list of word dicts from a transcript."""
    flat: list[dict[str, Any]] = []
    segments = transcript.get("segments", [])
    for seg_idx, seg in enumerate(segments):
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        seg_energy = seg.get("energy")
        seg_words = seg.get("words") or []
        if seg_words:
            for w in seg_words:
                w_text = str(w.get("w") or w.get("word") or "").strip()
                if not w_text:
                    continue
                s = float(w.get("s", w.get("start", seg_start)))
                e = float(w.get("e", w.get("end", max(s + 0.05, seg_end))))
                item: dict[str, Any] = {
                    "idx": len(flat),
                    "w": w_text,
                    "s": round(s, 3),
                    "e": round(max(s, e), 3),
                    "seg_id": seg.get("id", seg_idx),
                }
                if "p" in w and w["p"] is not None:
                    item["p"] = float(w["p"])
                if seg_energy is not None:
                    item["energy"] = float(seg_energy)
                flat.append(item)
        else:
            # Synthesize word tokens from segment text if word timestamps are omitted
            tokens = str(seg.get("text", "")).strip().split()
            if not tokens:
                continue
            dur = max(0.05, seg_end - seg_start)
            step = dur / len(tokens)
            for k, tok in enumerate(tokens):
                ws = seg_start + k * step
                we = seg_start + (k + 1) * step
                item = {
                    "idx": len(flat),
                    "w": tok,
                    "s": round(ws, 3),
                    "e": round(we, 3),
                    "seg_id": seg.get("id", seg_idx),
                }
                if seg_energy is not None:
                    item["energy"] = float(seg_energy)
                flat.append(item)
    return flat


def _ends_with_sentence_punct(word_text: str) -> bool:
    stripped = word_text.strip().rstrip("\"'”’)]}")
    return stripped.endswith(SENTENCE_END_PUNCT)


def split_into_thought_units(
    transcript: dict[str, Any],
    pause_split_s: float = 0.6,
) -> list[ThoughtUnit]:
    """Group transcript words into self-contained thought units split on pause > pause_split_s or sentence end."""
    words = flatten_transcript_words(transcript)
    if not words:
        return []

    units: list[ThoughtUnit] = []
    cur_words: list[dict[str, Any]] = []

    for i, w in enumerate(words):
        cur_words.append(w)
        is_last = i == len(words) - 1
        has_punct = _ends_with_sentence_punct(str(w["w"]))
        pause_after = (
            float(words[i + 1]["s"]) - float(w["e"])
            if not is_last
            else math.inf
        )
        if is_last or has_punct or pause_after > pause_split_s:
            w0 = int(cur_words[0]["idx"])
            w1 = int(cur_words[-1]["idx"])
            u_start = float(cur_words[0]["s"])
            u_end = float(cur_words[-1]["e"])
            u_text = " ".join(str(x["w"]) for x in cur_words).strip()
            energies = [float(x["energy"]) for x in cur_words if x.get("energy") is not None]
            u_energy = sum(energies) / len(energies) if energies else None
            units.append(
                ThoughtUnit(
                    id=len(units),
                    start=u_start,
                    end=u_end,
                    w0=w0,
                    w1=w1,
                    text=u_text,
                    words=list(cur_words),
                    energy=u_energy,
                )
            )
            cur_words = []

    return units


def _slice_1hz_series(
    series: list[float] | None,
    start: float,
    end: float,
    hz: float = 1.0,
) -> float | None:
    """Compute mean of a time-indexed series over [start, end], returning None if missing."""
    if series is None:
        return None
    if not isinstance(series, list) or len(series) == 0:
        return None
    i0 = max(0, int(math.floor(start * hz)))
    i1 = min(len(series), max(i0 + 1, int(math.ceil(end * hz))))
    if i0 >= len(series):
        i0 = len(series) - 1
        i1 = len(series)
    vals = [float(v) for v in series[i0:i1] if v is not None]
    if not vals:
        return None
    mean_val = sum(vals) / len(vals)
    return round(min(1.0, max(0.0, mean_val)), 4)


def extract_candidate_signal_features(
    candidate: CandidateWindow | dict[str, Any],
    *,
    signals_artefact: dict[str, Any] | None = None,
    scenes_artefact: list[dict[str, Any]] | dict[str, Any] | None = None,
    faces_artefact: dict[str, Any] | None = None,
    median_wps: float = 2.5,
) -> dict[str, float | None]:
    """Compute per-candidate [0, 1] signal features, preserving None when a signal is absent."""
    if isinstance(candidate, CandidateWindow):
        start = candidate.start
        end = candidate.end
        dur = max(0.1, candidate.duration)
        words = candidate.words
        cand_energy = candidate.energy
    else:
        start = float(candidate.get("start", 0.0))
        end = float(candidate.get("end", start + 20.0))
        dur = max(0.1, float(candidate.get("duration", end - start)))
        words = list(candidate.get("words", []))
        cand_energy = (
            float(candidate["energy"])
            if candidate.get("energy") is not None
            else None
        )

    hz = float((signals_artefact or {}).get("hz", 1.0) or 1.0)
    series_map: dict[str, Any] = (
        dict(signals_artefact.get("series", {}))
        if isinstance(signals_artefact, dict) and isinstance(signals_artefact.get("series"), dict)
        else {}
    )

    # 1. Energy
    energy: float | None = None
    if "energy" in series_map:
        energy = _slice_1hz_series(series_map.get("energy"), start, end, hz=hz)
    elif cand_energy is not None:
        energy = round(min(1.0, max(0.0, float(cand_energy))), 4)

    # 2. Speech rate
    speech_rate: float | None = None
    if "speech_rate" in series_map:
        speech_rate = _slice_1hz_series(series_map.get("speech_rate"), start, end, hz=hz)
    elif words:
        wps = len(words) / dur
        denom = max(0.5, median_wps * 1.5)
        speech_rate = round(min(1.0, max(0.0, wps / denom)), 4)

    # 3. Audience reaction (laughter / applause)
    reaction: float | None = None
    if "laughter" in series_map or "reaction" in series_map:
        raw_react = series_map.get("reaction")
        if raw_react is None:
            raw_react = series_map.get("laughter")
        reaction = _slice_1hz_series(raw_react, start, end, hz=hz)
    elif words:
        text_joined = " ".join(str(w.get("w", "")) for w in words)
        if "!" in text_joined or "?" in text_joined:
            reaction = 0.75
        elif energy is not None:
            reaction = round(min(1.0, max(0.0, energy * 0.8)), 4)

    # 4. Scene dynamics
    scene: float | None = None
    if "scene_density" in series_map or "scene" in series_map:
        raw_scene = series_map.get("scene")
        if raw_scene is None:
            raw_scene = series_map.get("scene_density")
        scene = _slice_1hz_series(raw_scene, start, end, hz=hz)
    elif scenes_artefact is not None:
        scene_list = (
            scenes_artefact.get("scenes", [])
            if isinstance(scenes_artefact, dict)
            else scenes_artefact
        )
        if isinstance(scene_list, list):
            cuts = sum(
                1
                for sc in scene_list
                if isinstance(sc, dict) and start < float(sc.get("start", -1.0)) < end
            )
            # Normalize ~0..6 cuts per 30s window into [0, 1]
            cuts_per_30s = (cuts / dur) * 30.0
            scene = round(min(1.0, max(0.0, cuts_per_30s / 6.0)), 4)

    # 5. Heatmap (often absent -> None so weight redistributes to LLM)
    heatmap: float | None = None
    if "heatmap" in series_map:
        heatmap = _slice_1hz_series(series_map.get("heatmap"), start, end, hz=hz)

    # 6. ASR confidence
    asr_conf: float | None = None
    if "asr_conf" in series_map or "asr_confidence" in series_map:
        raw_asr = series_map.get("asr_conf")
        if raw_asr is None:
            raw_asr = series_map.get("asr_confidence")
        asr_conf = _slice_1hz_series(raw_asr, start, end, hz=hz)
    elif words:
        probs = [float(w["p"]) for w in words if w.get("p") is not None]
        if probs:
            mean_p = sum(probs) / len(probs)
            asr_conf = round(min(1.0, max(0.0, mean_p)), 4)

    # 7. Face presence
    face_presence: float | None = None
    if "face_presence" in series_map:
        face_presence = _slice_1hz_series(series_map.get("face_presence"), start, end, hz=hz)
    elif isinstance(faces_artefact, dict) and faces_artefact.get("face_presence_ratio") is not None:
        face_presence = round(
            min(1.0, max(0.0, float(faces_artefact["face_presence_ratio"]))), 4
        )

    # 8. Music penalty
    music_penalty: float | None = None
    if "music" in series_map or "music_penalty" in series_map:
        raw_music = series_map.get("music_penalty")
        if raw_music is None:
            raw_music = series_map.get("music")
        music_penalty = _slice_1hz_series(raw_music, start, end, hz=hz)

    return {
        "energy": energy,
        "speech_rate": speech_rate,
        "reaction": reaction,
        "scene": scene,
        "heatmap": heatmap,
        "asr_conf": asr_conf,
        "face_presence": face_presence,
        "music_penalty": music_penalty,
    }


def _assign_topic(
    start: float,
    end: float,
    topics: list[dict[str, Any]] | None,
) -> tuple[str | int | None, str | None]:
    if not topics:
        return None, None
    best_id: str | int | None = None
    best_title: str | None = None
    best_overlap = 0.0
    for idx, t in enumerate(topics):
        if not isinstance(t, dict):
            continue
        ts = float(t.get("start", 0.0))
        te = float(t.get("end", 0.0))
        ov = max(0.0, min(end, te) - max(start, ts))
        if ov > best_overlap:
            best_overlap = ov
            best_id = t.get("id", idx)
            best_title = str(t.get("title", f"topic_{idx}"))
    return best_id, best_title


def generate_candidate_windows(
    transcript: dict[str, Any],
    *,
    genre: str | None = None,
    config: dict[str, Any] | None = None,
    min_len_s: float | None = None,
    max_len_s: float | None = None,
    target_len_s: float | None = None,
    pause_split_s: float | None = None,
    signals_artefact: dict[str, Any] | None = None,
    scenes_artefact: list[dict[str, Any]] | dict[str, Any] | None = None,
    faces_artefact: dict[str, Any] | None = None,
    topics: list[dict[str, Any]] | None = None,
) -> list[CandidateWindow]:
    """Slide over thought units to generate candidate windows within [min_len_s, max_len_s] without mid-word splits."""
    bounds = resolve_window_bounds(
        genre=genre,
        config=config,
        min_len_s=min_len_s,
        max_len_s=max_len_s,
        target_len_s=target_len_s,
        pause_split_s=pause_split_s,
    )
    min_s = bounds["min_len_s"]
    max_s = bounds["max_len_s"]
    target_s = bounds["target_len_s"]
    pause_s = bounds["pause_split_s"]

    units = split_into_thought_units(transcript, pause_split_s=pause_s)
    if not units:
        return []

    all_words = flatten_transcript_words(transcript)
    total_dur = max(1.0, float(units[-1].end) - float(units[0].start))
    median_wps = max(1.0, len(all_words) / total_dur)

    raw_windows: list[CandidateWindow] = []
    seen_spans: set[tuple[int, int]] = set()

    for i, u_start in enumerate(units):
        start_t = u_start.start
        acc_words: list[dict[str, Any]] = []
        acc_ids: list[int] = []
        acc_texts: list[str] = []
        acc_energies: list[float] = []

        best_for_start: CandidateWindow | None = None
        best_dist = math.inf

        for j in range(i, len(units)):
            u_cur = units[j]
            end_t = u_cur.end
            dur = end_t - start_t
            if dur > max_s:
                break

            acc_words.extend(u_cur.words)
            acc_ids.append(u_cur.id)
            acc_texts.append(u_cur.text)
            if u_cur.energy is not None:
                acc_energies.append(u_cur.energy)

            if min_s <= dur <= max_s and acc_words:
                w0 = int(acc_words[0]["idx"])
                w1 = int(acc_words[-1]["idx"])
                span_key = (w0, w1)
                if span_key in seen_spans:
                    continue
                topic_id, topic_title = _assign_topic(start_t, end_t, topics)
                mean_energy = (
                    sum(acc_energies) / len(acc_energies) if acc_energies else None
                )
                cw = CandidateWindow(
                    id=f"cand_{u_start.id}_{u_cur.id}",
                    start=round(start_t, 3),
                    end=round(end_t, 3),
                    duration=round(dur, 3),
                    w0=w0,
                    w1=w1,
                    unit_ids=list(acc_ids),
                    text=" ".join(acc_texts).strip(),
                    words=list(acc_words),
                    topic_id=topic_id,
                    topic_title=topic_title,
                    energy=mean_energy,
                )
                cw.signals = extract_candidate_signal_features(
                    cw,
                    signals_artefact=signals_artefact,
                    scenes_artefact=scenes_artefact,
                    faces_artefact=faces_artefact,
                    median_wps=median_wps,
                )
                dist = abs(dur - target_s)
                if dist < best_dist:
                    best_dist = dist
                    best_for_start = cw

        if best_for_start is not None:
            seen_spans.add((best_for_start.w0, best_for_start.w1))
            raw_windows.append(best_for_start)

    return raw_windows
