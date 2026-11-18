from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from backend.app.config import get_settings
from backend.pipeline.highlights.boundaries import (
    build_clip_plan,
    snap_clip_boundaries,
)
from backend.pipeline.highlights.scorer import (
    ScoredMoment,
    sanitize_hashtags,
    sanitize_hook_line,
    sanitize_title,
    score_candidates_map_reduce,
)
from backend.pipeline.highlights.segmenter import (
    CandidateWindow,
    generate_candidate_windows,
    load_highlights_v2_config,
)
from backend.pipeline.providers import LLMProvider

VIRAL_HOOK_KEYWORDS = {
    "secret", "secrets", "truth", "brutal", "percent", "99", "fail", "hook",
    "golden", "rule", "viral", "game", "changer", "why", "never", "best",
    "सफलता", "रहस्य", "ज़िंदगी", "विजेता", "लक्ष्य",
    "ভাইরাল", "কৌশল", "মনোযোগ", "দারুণ",
    "sabse", "asli", "turant", "samjhe",
}

SIGNAL_KEYS = ("energy", "speech_rate", "reaction", "scene", "heatmap", "asr_conf")

DEFAULT_GENRE_WEIGHTS: dict[str, float] = {
    "llm": 0.55,
    "energy": 0.10,
    "speech_rate": 0.07,
    "reaction": 0.08,
    "scene": 0.05,
    "heatmap": 0.10,
    "asr_conf": 0.05,
}


def build_candidate_windows(
    transcript: dict[str, Any],
    min_sec: float = 12.0,
    max_sec: float = 60.0,
) -> list[dict[str, Any]]:
    """Build candidate thought-unit windows from transcript segments."""
    segments = transcript.get("segments", [])
    candidates: list[dict[str, Any]] = []

    for i, seg in enumerate(segments):
        start = float(seg["start"])
        end = float(seg["end"])
        words = list(seg.get("words", []))
        texts = [seg.get("text", "")]
        energies = [float(seg.get("energy", 0.75))]

        # Single segment if long enough
        if min_sec <= (end - start) <= max_sec:
            candidates.append(
                {
                    "id": f"cand_{i}_{i}",
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": round(end - start, 2),
                    "text": " ".join(texts).strip(),
                    "words": words,
                    "energy": sum(energies) / len(energies),
                }
            )

        # Also try combining consecutive segments up to max_sec
        j = i + 1
        while j < len(segments):
            next_seg = segments[j]
            new_end = float(next_seg["end"])
            if (new_end - start) > max_sec:
                break
            words = words + list(next_seg.get("words", []))
            texts.append(next_seg.get("text", ""))
            energies.append(float(next_seg.get("energy", 0.75)))
            if (new_end - start) >= min_sec:
                candidates.append(
                    {
                        "id": f"cand_{i}_{j}",
                        "start": round(start, 2),
                        "end": round(new_end, 2),
                        "duration": round(new_end - start, 2),
                        "text": " ".join(texts).strip(),
                        "words": words,
                        "energy": sum(energies) / len(energies),
                    }
                )
            j += 1

    # Fallback if video is very short
    if not candidates and segments:
        for i, seg in enumerate(segments):
            s = float(seg["start"])
            e = float(seg["end"])
            candidates.append(
                {
                    "id": f"cand_short_{i}",
                    "start": round(s, 2),
                    "end": round(e, 2),
                    "duration": round(max(1.0, e - s), 2),
                    "text": seg.get("text", ""),
                    "words": seg.get("words", []),
                    "energy": float(seg.get("energy", 0.8)),
                }
            )

    return candidates


def compute_signal_features(cand: dict[str, Any], median_wps: float) -> dict[str, float]:
    """Compute normalised [0, 1] acoustic/prosodic/confidence features for a candidate clip."""
    words = cand.get("words", [])
    dur = max(1.0, float(cand.get("duration", 20.0)))
    wps = len(words) / dur
    rate_score = min(1.0, max(0.3, wps / max(1.0, median_wps)))

    probs = [float(w.get("p", 0.9)) for w in words] or [0.9]
    conf_score = sum(probs) / len(probs)

    txt = cand.get("text", "")
    has_punct_emphasis = 1.0 if ("!" in txt or "?" in txt) else 0.65
    energy = min(1.0, max(0.2, float(cand.get("energy", 0.75))))

    return {
        "energy": round(energy, 3),
        "speech_rate": round(rate_score, 3),
        "reaction": round(has_punct_emphasis, 3),
        "scene_dynamics": 0.75,
        "heatmap": 0.70,
        "asr_confidence": round(conf_score, 3),
    }


def score_candidate_llm_or_heuristic(
    cand: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Score candidate semantics via Gemini API if configured, Ollama if running, or viral-hook heuristic."""
    settings = get_settings()
    txt = cand.get("text", "")
    tokens = [re.sub(r"[^\w\u0900-\u09FF]", "", t.lower()) for t in txt.split()]
    hook_hits = sum(1 for t in tokens if t in VIRAL_HOOK_KEYWORDS)
    has_question_or_bang = ("!" in txt) or ("?" in txt)
    has_number = bool(re.search(r"\d+", txt))

    # Try Gemini if API key is provided
    if settings.gemini_api_key.strip():
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key.strip()}"
            )
            prompt = (
                f"Rate this {language} short video clip transcript on virality (0.0 to 1.0) "
                f"and return JSON {{\"llm_score\": float, \"title\": str, \"reason\": str, \"hashtags\": [str]}}:\n"
                f"Transcript: {txt}"
            )
            resp = httpx.post(
                url,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=12.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                raw_json = body["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(raw_json)
                return {
                    "llm_score": float(parsed.get("llm_score", 0.82)),
                    "title": parsed.get("title") or _generate_title(txt, language),
                    "reason": parsed.get("reason") or "High-impact standalone hook & clear viewer takeaway.",
                    "hashtags": parsed.get("hashtags") or _generate_hashtags(language),
                    "provider": "gemini",
                }
        except Exception:
            pass
    elif settings.llm_provider == "gemini":
        # Explicitly requested Gemini without key -> record non-fatal HITL blocker and continue with heuristic
        settings.require_secret(
            attr_name="gemini_api_key",
            env_key="GEMINI_API_KEY",
            task_id="T-4.3",
            how_to_get="https://aistudio.google.com/app/apikey",
            why="Requested LLM_PROVIDER=gemini for semantic highlight scoring.",
            strict=False,
            alternatives=["Using local heuristic/Ollama scorer meanwhile."],
        )

    # Offline viral-hook semantic scorer
    base = 0.62
    base += min(0.22, hook_hits * 0.06)
    if has_question_or_bang:
        base += 0.08
    if has_number:
        base += 0.05
    # Prefer clips close to 20-40s sweet spot
    dur = float(cand.get("duration", 25.0))
    if 15.0 <= dur <= 45.0:
        base += 0.05

    llm_score = round(min(0.98, base), 3)
    return {
        "llm_score": llm_score,
        "title": _generate_title(txt, language),
        "reason": _explain_score(hook_hits, has_question_or_bang, has_number),
        "hashtags": _generate_hashtags(language),
        "provider": "heuristic_local",
    }


def _generate_title(text: str, language: str) -> str:
    words = text.strip().split()
    snippet = " ".join(words[:7])
    if len(words) > 7:
        snippet += "..."
    prefix = {
        "hi": "🔥 मुख्य विचार: ",
        "bn": "🔥 সেরা মুহূর্ত: ",
        "hinglish": "🔥 Viral Hook: ",
        "en": "🔥 Key Insight: ",
    }.get(language, "🔥 Highlight: ")
    return f"{prefix}{snippet}"


def _generate_hashtags(language: str) -> list[str]:
    common = ["#shorts", "#viral", "#reels", "#clipper"]
    lang_tags = {
        "hinglish": ["#hinglish", "#indiancreators", "#podcastclips"],
        "hi": ["#hindimotivation", "#hindishorts", "#bharat"],
        "bn": ["#banglashorts", "#bengali", "#bangla"],
        "en": ["#keynote", "#founders", "#growth"],
    }.get(language, ["#trending"])
    return common + lang_tags


def _explain_score(hook_hits: int, has_emph: bool, has_num: bool) -> str:
    reasons = ["Self-contained thought unit with clean entry/exit"]
    if hook_hits > 0:
        reasons.append(f"{hook_hits} high-retention hook keyword(s)")
    if has_emph:
        reasons.append("strong vocal/punctuation emphasis")
    if has_num:
        reasons.append("concrete number/stat hook")
    return " + ".join(reasons) + "."


def _overlap_ratio(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    if inter <= 0:
        return 0.0
    min_dur = max(0.1, min(a_end - a_start, b_end - b_start))
    return inter / min_dur


def snap_boundaries_to_silence(
    cand: dict[str, Any],
    pad_start_s: float = 0.10,
    pad_end_s: float = 0.25,
) -> tuple[float, float]:
    """Snap candidate start/end to first/last word timestamps with tail padding."""
    words = cand.get("words", [])
    if words:
        snapped_start = round(max(0.0, float(words[0]["s"]) - pad_start_s), 2)
        snapped_end = round(float(words[-1]["e"]) + pad_end_s, 2)
    else:
        snapped_start = round(float(cand.get("start", 0.0)), 2)
        snapped_end = round(float(cand.get("end", snapped_start + 20.0)), 2)
    return snapped_start, snapped_end


# ==============================================================================
# Spec v2 Ranker: Fusion, Weight Redistribution, NMS, Dedupe, Diversity, Count
# ==============================================================================


@dataclass(slots=True)
class SelectionResult:
    """Structured result of Spec v2 highlight ranking and selection."""

    clips: list[dict[str, Any]] = field(default_factory=list)
    score_mode: str = "full"
    fewer_clips_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clips": list(self.clips),
            "score_mode": self.score_mode,
            "fewer_clips_reason": self.fewer_clips_reason,
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
        }


def resolve_genre_weights(
    genre: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, float]:
    """Return genre-specific fusion weights from `config/spec_v2/highlights.yaml`."""
    cfg = config if config is not None else load_highlights_v2_config()
    weights_map = cfg.get("weights", {})
    if isinstance(weights_map, dict) and "default" in weights_map:
        g_key = (genre or "default").strip().lower()
        chosen = weights_map.get(g_key) or weights_map.get("default") or DEFAULT_GENRE_WEIGHTS
        return {str(k): float(v) for k, v in chosen.items()}
    return dict(DEFAULT_GENRE_WEIGHTS)


def redistribute_weights(
    base_weights: dict[str, float],
    signals: dict[str, float | None],
    *,
    llm_score: float | None,
    score_mode: str = "full",
) -> tuple[dict[str, float], str]:
    """Redistribute missing feature weights per Spec v2 (`full`, `signals_only`, `llm_only`).

    - In `full` mode: any signal feature whose value is `None` has its weight moved to `llm`.
      If all signals are `None` and `llm_score` is present, mode becomes `llm_only` (`llm: 1.0`).
      If `llm_score` is `None`, mode degrades to `signals_only`.
    - In `signals_only` mode: `llm` weight and missing signal weights redistribute proportionally
      across present signal features.
    - In `llm_only` mode: `llm` gets weight `1.0` and all signal weights are `0.0`.
    """
    present_signals = [
        k for k in SIGNAL_KEYS if signals.get(k) is not None and k in base_weights
    ]
    effective_mode = score_mode
    if effective_mode == "full":
        if llm_score is None:
            effective_mode = "signals_only"
        elif not present_signals:
            effective_mode = "llm_only"

    if effective_mode == "llm_only":
        eff = {k: 0.0 for k in base_weights}
        eff["llm"] = 1.0
        return eff, "llm_only"

    if effective_mode == "signals_only":
        eff = {k: 0.0 for k in base_weights}
        eff["llm"] = 0.0
        if not present_signals:
            return eff, "signals_only"
        present_sum = sum(float(base_weights.get(k, 0.0)) for k in present_signals)
        if present_sum <= 0.0:
            equal_w = 1.0 / len(present_signals)
            for k in present_signals:
                eff[k] = round(equal_w, 6)
        else:
            for k in present_signals:
                eff[k] = round(float(base_weights.get(k, 0.0)) / present_sum, 6)
        return eff, "signals_only"

    # Full mode: redistribute missing signal weights into `llm`
    eff = {}
    missing_weight_sum = 0.0
    for k in SIGNAL_KEYS:
        w = float(base_weights.get(k, 0.0))
        if signals.get(k) is None:
            eff[k] = 0.0
            missing_weight_sum += w
        else:
            eff[k] = w
    eff["llm"] = round(float(base_weights.get("llm", 0.55)) + missing_weight_sum, 6)

    # Normalize to sum to 1.0
    total = sum(eff.values())
    if total > 0:
        eff = {k: round(v / total, 6) for k, v in eff.items()}
    return eff, "full"


def compute_fusion_score(
    signals: dict[str, float | None],
    llm_score: float | None = None,
    *,
    genre: str | None = None,
    score_mode: str = "full",
    config: dict[str, Any] | None = None,
    weights_override: dict[str, float] | None = None,
) -> tuple[float, dict[str, Any], str]:
    """Compute weighted fusion score `S = sum(w_i * f_i)` and explainable breakdown."""
    base_weights = (
        dict(weights_override)
        if weights_override is not None
        else resolve_genre_weights(genre=genre, config=config)
    )
    eff_weights, resolved_mode = redistribute_weights(
        base_weights,
        signals,
        llm_score=llm_score,
        score_mode=score_mode,
    )

    total_s = 0.0
    if resolved_mode != "signals_only" and llm_score is not None:
        total_s += eff_weights.get("llm", 0.0) * float(llm_score)

    for k in SIGNAL_KEYS:
        val = signals.get(k)
        if val is not None and eff_weights.get(k, 0.0) > 0:
            total_s += eff_weights[k] * float(val)

    # Optional music penalty (if intro/background music dominates)
    music_pen = signals.get("music_penalty")
    if music_pen is not None and float(music_pen) > 0.7:
        total_s *= max(0.5, 1.0 - 0.25 * float(music_pen))

    final_score = round(min(1.0, max(0.0, total_s)), 4)
    breakdown: dict[str, Any] = {
        "total": final_score,
        "score_mode": resolved_mode,
        "llm": round(float(llm_score), 4) if llm_score is not None else None,
        "effective_weights": eff_weights,
    }
    for k in SIGNAL_KEYS:
        v = signals.get(k)
        breakdown[k] = round(float(v), 4) if v is not None else None
    if "face_presence" in signals:
        fp = signals.get("face_presence")
        breakdown["face_presence"] = round(float(fp), 4) if fp is not None else None
    return final_score, breakdown, resolved_mode


def non_max_suppression(
    candidates: list[dict[str, Any]],
    max_overlap: float = 0.30,
) -> list[dict[str, Any]]:
    """Drop candidates overlapping any higher-scored selected candidate by > `max_overlap` (30%)."""
    ordered = sorted(
        candidates,
        key=lambda c: float(c.get("score", 0.0)),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    for cand in ordered:
        c_start = float(cand.get("start", 0.0))
        c_end = float(cand.get("end", c_start))
        overlaps = any(
            _overlap_ratio(c_start, c_end, float(k["start"]), float(k["end"])) > max_overlap
            for k in kept
        )
        if not overlaps:
            kept.append(cand)
    return kept


def _text_token_counter(text: str) -> Counter[str]:
    tokens = [
        t
        for t in re.findall(r"[\w\u0900-\u09FF]+", (text or "").lower())
        if len(t) > 1
    ]
    if not tokens:
        return Counter()
    # Include unigrams and bigrams (shingles) for semantic similarity
    bigrams = [f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1)]
    return Counter(tokens + bigrams)


def text_cosine_similarity(text_a: str, text_b: str) -> float:
    """Compute token + shingle cosine similarity in [0, 1] between two candidate texts."""
    ca = _text_token_counter(text_a)
    cb = _text_token_counter(text_b)
    if not ca or not cb:
        return 0.0
    common = set(ca.keys()) & set(cb.keys())
    dot = sum(ca[k] * cb[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in ca.values()))
    norm_b = math.sqrt(sum(v * v for v in cb.values()))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return round(dot / (norm_a * norm_b), 4)


def _embedding_cosine(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(vec_a, vec_b))
    na = math.sqrt(sum(float(a) * float(a) for a in vec_a))
    nb = math.sqrt(sum(float(b) * float(b) for b in vec_b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return round(dot / (na * nb), 4)


def semantic_dedupe(
    candidates: list[dict[str, Any]],
    max_cosine: float = 0.90,
) -> list[dict[str, Any]]:
    """Drop near-duplicate candidates whose embedding or token/shingle cosine exceeds `max_cosine` (0.90)."""
    kept: list[dict[str, Any]] = []
    for cand in candidates:
        c_text = str(cand.get("text") or cand.get("transcript_snippet") or "")
        c_emb = cand.get("embedding")
        is_dup = False
        for prev in kept:
            p_emb = prev.get("embedding")
            if isinstance(c_emb, list) and isinstance(p_emb, list):
                sim = _embedding_cosine(c_emb, p_emb)
            else:
                p_text = str(prev.get("text") or prev.get("transcript_snippet") or "")
                sim = text_cosine_similarity(c_text, p_text)
            if sim > max_cosine:
                is_dup = True
                break
        if not is_dup:
            kept.append(cand)
    return kept


def enforce_diversity_caps(
    candidates: list[dict[str, Any]],
    *,
    region_s: float = 180.0,
    max_per_region: int = 2,
    max_per_topic: int = 2,
) -> list[dict[str, Any]]:
    """Enforce diversity caps: at most `max_per_region` (2) per `region_s` (180s) and `max_per_topic` (2) per topic."""
    region_counts: Counter[int] = Counter()
    topic_counts: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []

    for cand in candidates:
        c_start = float(cand.get("start", 0.0))
        c_end = float(cand.get("end", c_start))
        midpoint = 0.5 * (c_start + c_end)
        region_bucket = int(midpoint // max(1.0, region_s))

        if region_counts[region_bucket] >= max_per_region:
            continue

        topic_key_raw = cand.get("topic_id") if cand.get("topic_id") is not None else cand.get("topic_title")
        if topic_key_raw is not None and str(topic_key_raw).strip() != "":
            topic_key = str(topic_key_raw).strip()
            if topic_counts[topic_key] >= max_per_topic:
                continue
            topic_counts[topic_key] += 1

        region_counts[region_bucket] += 1
        kept.append(cand)

    return kept


def select_adaptive_count(
    candidates: list[dict[str, Any]],
    *,
    threshold: float = 0.62,
    min_clips: int = 3,
    min_candidate_floor: float = 0.45,
    max_clips: int = 12,
    total_raw_candidates: int | None = None,
    dropped_by_nms: int = 0,
    dropped_by_dedupe: int = 0,
    dropped_by_diversity: int = 0,
) -> tuple[list[dict[str, Any]], str | None]:
    """Select final clips using adaptive count rules and return `(selected, fewer_clips_reason)`.

    Rules:
    - Keep all candidates with `S >= threshold` (0.62) up to `max_clips` (12).
    - If fewer than `min_clips` (3) pass `threshold`, fill up to `min_clips` using candidates
      with `S >= min_candidate_floor` (0.45).
    - Always return a structured `fewer_clips_reason` when `len(selected) < max_clips`.
    """
    ordered = sorted(
        candidates,
        key=lambda c: float(c.get("score", 0.0)),
        reverse=True,
    )

    above_threshold = [c for c in ordered if float(c.get("score", 0.0)) >= threshold]
    selected = list(above_threshold[:max_clips])

    used_floor_backfill = False
    if len(selected) < min_clips:
        selected_ids = {id(c) for c in selected}
        floor_pool = [
            c
            for c in ordered
            if id(c) not in selected_ids and float(c.get("score", 0.0)) >= min_candidate_floor
        ]
        needed = min_clips - len(selected)
        if floor_pool:
            selected.extend(floor_pool[:needed])
            used_floor_backfill = True

    selected = selected[:max_clips]
    for idx, item in enumerate(selected, start=1):
        item["rank"] = idx

    if len(selected) >= max_clips:
        return selected, None

    total_pool = total_raw_candidates if total_raw_candidates is not None else len(candidates)
    if total_pool == 0 or len(ordered) == 0:
        reason = "NO_CANDIDATE_WINDOWS: source video/transcript produced 0 candidate windows."
    elif len(selected) == 0:
        reason = (
            f"BELOW_FLOOR_THRESHOLD: 0 of {len(ordered)} surviving candidates met "
            f"min_candidate_floor ({min_candidate_floor:.2f})."
        )
    else:
        parts = [
            f"Returned {len(selected)}/{max_clips} clips: only {len(above_threshold)} candidate(s) "
            f"scored >= threshold ({threshold:.2f})"
        ]
        if used_floor_backfill:
            parts.append(
                f"backfilled to {len(selected)} using floor >= {min_candidate_floor:.2f}"
            )
        filter_notes = []
        if dropped_by_nms > 0:
            filter_notes.append(f"{dropped_by_nms} dropped by NMS overlap > 30%")
        if dropped_by_dedupe > 0:
            filter_notes.append(f"{dropped_by_dedupe} dropped by semantic dedupe > 0.90")
        if dropped_by_diversity > 0:
            filter_notes.append(f"{dropped_by_diversity} dropped by region/topic diversity caps")
        if filter_notes:
            parts.append("(" + ", ".join(filter_notes) + ")")
        reason = "; ".join(parts) + "."

    return selected, reason


def rank_and_select_candidates_v2(
    candidates: list[CandidateWindow] | list[dict[str, Any]] | list[CandidateWindow | dict[str, Any]],
    *,
    llm_moments: dict[str, ScoredMoment] | None = None,
    score_mode: str = "full",
    genre: str | None = None,
    language: str = "en",
    config: dict[str, Any] | None = None,
    max_clips_override: int | None = None,
    threshold_override: float | None = None,
) -> SelectionResult:
    """Fuse signals + LLM scores, run NMS, semantic dedupe, diversity caps, and adaptive count."""
    cfg = config if config is not None else load_highlights_v2_config()
    sel_cfg = dict(cfg.get("selection", {}))
    threshold = (
        float(threshold_override)
        if threshold_override is not None
        else float(sel_cfg.get("threshold", 0.62))
    )
    min_clips = int(sel_cfg.get("min_clips", 3))
    min_floor = float(sel_cfg.get("min_candidate_floor", 0.45))
    max_clips = (
        int(max_clips_override)
        if max_clips_override is not None
        else int(sel_cfg.get("max_clips", 12))
    )
    nms_overlap = float(sel_cfg.get("nms_overlap", 0.30))
    dedupe_cosine = float(sel_cfg.get("semantic_dedupe_cosine", 0.90))
    div_cfg = dict(sel_cfg.get("diversity", {}))
    region_s = float(div_cfg.get("max_per_region_s", 180.0))
    max_per_region = int(div_cfg.get("max_per_region", 2))
    max_per_topic = int(div_cfg.get("max_per_topic", 2))

    warnings: list[str] = []
    if score_mode == "signals_only":
        warnings.append("Running in signals_only degraded mode (LLM unavailable or disabled).")
    elif score_mode == "llm_only":
        warnings.append("Running in llm_only degraded mode (audio/visual signals unavailable).")

    scored_pool: list[dict[str, Any]] = []
    effective_modes: set[str] = set()

    for item in candidates:
        c_dict = item.to_dict() if isinstance(item, CandidateWindow) else dict(item)
        cid = str(c_dict.get("id", ""))
        signals: dict[str, float | None] = dict(c_dict.get("signals", {}))

        # Support flat signal dicts if caller passed legacy candidate dicts
        if not signals:
            for k in SIGNAL_KEYS:
                if k in c_dict:
                    signals[k] = c_dict.get(k)

        moment = (llm_moments or {}).get(cid)
        llm_val: float | None = None
        if moment is not None:
            llm_val = moment.llm_score
        elif c_dict.get("llm_score") is not None:
            llm_val = float(c_dict["llm_score"])

        s_val, breakdown, resolved_mode = compute_fusion_score(
            signals,
            llm_score=llm_val,
            genre=genre,
            score_mode=score_mode,
            config=cfg,
        )
        effective_modes.add(resolved_mode)

        raw_text = str(c_dict.get("text") or c_dict.get("transcript_snippet") or "")
        if moment is not None:
            title = moment.title
            hook_line = moment.hook_line
            hashtags = moment.hashtags
            reason = moment.reason
            breakdown["dimensions"] = dict(moment.dimensions)
            breakdown["brief_match"] = moment.brief_match
            breakdown["provider"] = moment.provider
        else:
            title = sanitize_title(
                str(c_dict.get("title") or _generate_title(raw_text, language))
            )
            hook_line = sanitize_hook_line(
                str(c_dict.get("hook_line") or " ".join(raw_text.split()[:8]))
            )
            hashtags = sanitize_hashtags(
                c_dict.get("hashtags") or _generate_hashtags(language),
                language=language,
            )
            reason = str(
                c_dict.get("reason")
                or f"Selected via {resolved_mode} scoring (score={s_val:.2f})."
            )
            breakdown["provider"] = "signals_only" if resolved_mode == "signals_only" else "custom"

        entry = {
            **c_dict,
            "score": s_val,
            "score_mode": resolved_mode,
            "score_breakdown": breakdown,
            "title": title,
            "hook_line": hook_line,
            "hashtags": hashtags,
            "reason": reason,
            "transcript_snippet": raw_text,
        }
        scored_pool.append(entry)

    scored_pool.sort(key=lambda x: float(x["score"]), reverse=True)

    after_nms = non_max_suppression(scored_pool, max_overlap=nms_overlap)
    dropped_nms = len(scored_pool) - len(after_nms)

    after_dedupe = semantic_dedupe(after_nms, max_cosine=dedupe_cosine)
    dropped_dedupe = len(after_nms) - len(after_dedupe)

    after_diversity = enforce_diversity_caps(
        after_dedupe,
        region_s=region_s,
        max_per_region=max_per_region,
        max_per_topic=max_per_topic,
    )
    dropped_diversity = len(after_dedupe) - len(after_diversity)

    selected, fewer_reason = select_adaptive_count(
        after_diversity,
        threshold=threshold,
        min_clips=min_clips,
        min_candidate_floor=min_floor,
        max_clips=max_clips,
        total_raw_candidates=len(scored_pool),
        dropped_by_nms=dropped_nms,
        dropped_by_dedupe=dropped_dedupe,
        dropped_by_diversity=dropped_diversity,
    )

    final_mode = score_mode
    if len(effective_modes) == 1:
        final_mode = next(iter(effective_modes))

    return SelectionResult(
        clips=selected,
        score_mode=final_mode,
        fewer_clips_reason=fewer_reason,
        warnings=warnings,
        stats={
            "raw_candidates": len(scored_pool),
            "after_nms": len(after_nms),
            "after_dedupe": len(after_dedupe),
            "after_diversity": len(after_diversity),
            "selected": len(selected),
            "dropped_by_nms": dropped_nms,
            "dropped_by_dedupe": dropped_dedupe,
            "dropped_by_diversity": dropped_diversity,
        },
    )


def run_highlight_engine_v2(
    job_dir: Path,
    transcript: dict[str, Any],
    *,
    provider: LLMProvider | None = None,
    clip_brief: str | None = None,
    genre: str | None = None,
    signals_artefact: dict[str, Any] | None = None,
    scenes_artefact: list[dict[str, Any]] | dict[str, Any] | None = None,
    faces_artefact: dict[str, Any] | None = None,
    topics: list[dict[str, Any]] | None = None,
    max_clips: int | None = None,
    threshold: float | None = None,
    score_mode: str | None = None,
    aspect: str = "9:16",
    caption_template: str = "bold_pop@1",
    layout_template: str = "auto_reframe@1",
) -> SelectionResult:
    """End-to-end Spec v2 highlight engine: segment -> map-reduce LLM -> rank -> snap -> clip_plan.json."""
    cfg = load_highlights_v2_config()
    language = str(transcript.get("language", "en"))
    script = str(transcript.get("script", "latn"))

    candidates = generate_candidate_windows(
        transcript,
        genre=genre,
        config=cfg,
        signals_artefact=signals_artefact,
        scenes_artefact=scenes_artefact,
        faces_artefact=faces_artefact,
        topics=topics,
    )

    effective_mode = score_mode or "full"
    llm_moments: dict[str, ScoredMoment] = {}
    all_warnings: list[str] = []

    if effective_mode != "signals_only" and candidates:
        mr_res = score_candidates_map_reduce(
            transcript,
            candidates,
            provider=provider,
            clip_brief=clip_brief,
            genre=genre,
            topics=topics,
            config=cfg,
        )
        llm_moments = mr_res.moments
        all_warnings.extend(mr_res.warnings)
        if score_mode is None:
            effective_mode = mr_res.score_mode

    selection = rank_and_select_candidates_v2(
        candidates,
        llm_moments=llm_moments,
        score_mode=effective_mode,
        genre=genre,
        language=language,
        config=cfg,
        max_clips_override=max_clips,
        threshold_override=threshold,
    )
    selection.warnings = all_warnings + selection.warnings

    silences = (
        signals_artefact.get("silences")
        if isinstance(signals_artefact, dict)
        else None
    )

    for idx, clip in enumerate(selection.clips, start=1):
        snapped = snap_clip_boundaries(
            float(clip["start"]),
            float(clip["end"]),
            transcript,
            silences=silences,
            scenes=scenes_artefact,
            config=cfg,
        )
        clip["start"] = snapped.start
        clip["end"] = snapped.end
        clip["duration"] = snapped.duration
        clip["boundary_meta"] = snapped.to_dict()

        clip_id = str(clip.get("clip_id") or f"clip_{idx:02d}")
        clip["clip_id"] = clip_id
        plan_path = job_dir / "clips" / clip_id / "clip_plan.json"
        plan = build_clip_plan(
            clip_id=clip_id,
            start=snapped.start,
            end=snapped.end,
            layout_template=layout_template,
            caption_template=caption_template,
            script=script,
            aspect=aspect,
            output_path=plan_path,
        )
        clip["clip_plan_path"] = str(plan_path.relative_to(job_dir))
        clip["clip_plan"] = plan

    highlights_dir = job_dir / "highlights"
    highlights_dir.mkdir(parents=True, exist_ok=True)
    (highlights_dir / "clips.json").write_text(
        json.dumps(selection.clips, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return selection


def analyze_and_rank_highlights(
    job_dir: Path,
    transcript: dict[str, Any],
    max_clips: int = 12,
) -> list[dict[str, Any]]:
    """Run full candidate generation, signal + LLM scoring, fusion, NMS, and boundary snapping."""
    settings = get_settings()
    cfg = settings.load_yaml_config("highlights")
    weights = cfg.get(
        "weights",
        {
            "llm": 0.55,
            "energy": 0.12,
            "speech_rate": 0.08,
            "reaction": 0.08,
            "scene_dynamics": 0.05,
            "heatmap": 0.07,
            "asr_confidence": 0.05,
        },
    )
    threshold = float(cfg.get("quality_threshold", 0.55))
    max_overlap = float(cfg.get("max_overlap_ratio", 0.30))
    language = transcript.get("language", "en")

    candidates = build_candidate_windows(
        transcript,
        min_sec=float(cfg.get("min_clip_seconds", 12.0)),
        max_sec=float(cfg.get("max_clip_seconds", 60.0)),
    )

    # Compute median words/sec
    wps_list = [
        len(c.get("words", [])) / max(1.0, float(c.get("duration", 20.0)))
        for c in candidates
    ] or [2.5]
    median_wps = sorted(wps_list)[len(wps_list) // 2]

    scored: list[dict[str, Any]] = []
    for cand in candidates:
        feats = compute_signal_features(cand, median_wps)
        sem = score_candidate_llm_or_heuristic(cand, language)
        fusion = (
            weights["llm"] * sem["llm_score"]
            + weights["energy"] * feats["energy"]
            + weights["speech_rate"] * feats["speech_rate"]
            + weights["reaction"] * feats["reaction"]
            + weights["scene_dynamics"] * feats["scene_dynamics"]
            + weights["heatmap"] * feats["heatmap"]
            + weights["asr_confidence"] * feats["asr_confidence"]
        )
        fusion_score = round(min(0.99, fusion), 3)
        breakdown = {
            "total": fusion_score,
            "llm": sem["llm_score"],
            "provider": sem["provider"],
            **feats,
        }
        words = cand.get("words", [])
        # Snap start/end to exact first/last word timestamps with 0.25s tail padding
        snapped_start, snapped_end = snap_boundaries_to_silence(cand)

        scored.append(
            {
                "start": snapped_start,
                "end": snapped_end,
                "duration": round(snapped_end - snapped_start, 2),
                "score": fusion_score,
                "score_breakdown": breakdown,
                "title": sem["title"],
                "hook_line": " ".join(cand["text"].split()[:8]),
                "hashtags": sem["hashtags"],
                "reason": sem["reason"],
                "transcript_snippet": cand["text"],
                "words": words,
            }
        )

    # Sort descending by fusion score
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Non-Maximum Suppression (NMS) to prevent overlapping duplicate clips
    selected: list[dict[str, Any]] = []
    for item in scored:
        if len(selected) >= max_clips:
            break
        if item["score"] < threshold and len(selected) >= int(cfg.get("min_clips", 3)):
            continue
        overlaps = any(
            _overlap_ratio(item["start"], item["end"], s["start"], s["end"]) > max_overlap
            for s in selected
        )
        if not overlaps:
            item["rank"] = len(selected) + 1
            selected.append(item)

    highlights_dir = job_dir / "highlights"
    highlights_dir.mkdir(parents=True, exist_ok=True)
    (highlights_dir / "clips.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return selected
