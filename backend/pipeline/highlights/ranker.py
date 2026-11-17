from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from backend.app.config import get_settings

VIRAL_HOOK_KEYWORDS = {
    "secret", "secrets", "truth", "brutal", "percent", "99", "fail", "hook",
    "golden", "rule", "viral", "game", "changer", "why", "never", "best",
    "सफलता", "रहस्य", "ज़िंदगी", "विजेता", "लक्ष्य",
    "ভাইরাল", "কৌশল", "মনোযোগ", "দারুণ",
    "sabse", "asli", "turant", "samjhe",
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
        snapped_start = round(max(0.0, float(words[0]["s"]) - 0.10), 2) if words else cand["start"]
        snapped_end = round(float(words[-1]["e"]) + 0.25, 2) if words else cand["end"]

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
