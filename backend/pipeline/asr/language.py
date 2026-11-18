from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.pipeline.asr.engine import HINDI_ROMAN_LEXICON, detect_word_script

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "hi", "hinglish", "bn"})

# Superset of engine.HINDI_ROMAN_LEXICON for robust Romanized Hindi / Hinglish detection
# (deliberately avoids ambiguous common English words like "is", "in", "to", "me", "he", "no", "be").
EXTENDED_HINDI_ROMAN_LEXICON: frozenset[str] = frozenset(
    HINDI_ROMAN_LEXICON
    | {
        "aap", "abhi", "apne", "apni", "baad", "bade", "badi", "bada", "batao",
        "bilkul", "bohot", "chalo", "dekho", "dil", "dost", "duniya", "gaana",
        "ghar", "hamesha", "hara", "hoga", "hogi", "honge", "hoon", "humne",
        "isliye", "jaise", "jana", "janta", "jisme", "kaam", "kab", "kabhi",
        "kahan", "kaha", "kaafi", "kam", "kar", "karna", "karke", "karo",
        "karta", "karti", "karte", "kaun", "khud", "kisi", "kitna", "kitne",
        "kuch", "kyu", "lekin", "liye", "log", "logon", "magar", "main",
        "matlab", "mera", "mere", "meri", "milta", "milti", "milte", "naam",
        "nazar", "paas", "paisa", "paise", "par", "pehla", "pehli", "phir",
        "pyaar", "raha", "rahe", "rahi", "rehna", "sab", "sabse", "sahi",
        "samay", "sath", "saath", "seekho", "sirf", "soch", "suno", "tarah",
        "tera", "tere", "teri", "tha", "the", "thi", "tum", "tumhara",
        "waise", "wala", "wale", "wali", "waqt", "wahan", "yahan", "yeh",
        "zindagi",
    }
)


def script_for_language(language: str) -> str:
    norm = (language or "en").strip().lower()
    if norm == "bn":
        return "beng"
    if norm == "hi":
        return "deva"
    return "latn"


@dataclass(slots=True)
class LanguageDetectionResult:
    """Structured language detection output and `asr/lang.json` evidence."""

    language: str
    script: str
    override_applied: bool
    detected_language: str
    window_votes: list[dict[str, Any]] = field(default_factory=list)
    latin_token_ratio: float = 0.0
    devanagari_ratio: float = 0.0
    bengali_ratio: float = 0.0
    roman_hindi_ratio: float = 0.0
    confidence: float = 0.0
    hinglish_latin_token_ratio_threshold: float = 0.25

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "language": self.language,
            "script": self.script,
            "override_applied": self.override_applied,
            "detected_language": self.detected_language,
            "window_votes": list(self.window_votes),
            "latin_token_ratio": round(self.latin_token_ratio, 4),
            "devanagari_ratio": round(self.devanagari_ratio, 4),
            "bengali_ratio": round(self.bengali_ratio, 4),
            "roman_hindi_ratio": round(self.roman_hindi_ratio, 4),
            "confidence": round(min(1.0, max(0.0, self.confidence)), 4),
            "hinglish_latin_token_ratio_threshold": self.hinglish_latin_token_ratio_threshold,
        }

    def write_json(self, path: Path) -> Path:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix=".lang_", suffix=".json", dir=out_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, out_path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return out_path


def _clean_latin_lookup_token(token: str) -> str:
    norm = unicodedata.normalize("NFC", token).lower()
    return "".join(ch for ch in norm if ch.isalnum() or ch == "_")


def _seg_to_dict(seg: Any) -> dict[str, Any]:
    if isinstance(seg, dict):
        return seg
    words_attr = getattr(seg, "words", None)
    words_list: list[dict[str, Any]] = []
    if words_attr:
        for w in words_attr:
            if isinstance(w, dict):
                words_list.append(w)
            else:
                words_list.append({"w": getattr(w, "w", getattr(w, "word", ""))})
    return {
        "start": float(getattr(seg, "start", 0.0)),
        "end": float(getattr(seg, "end", 0.0)),
        "text": str(getattr(seg, "text", "")),
        "words": words_list,
    }


def _extract_tokens_from_segments(segments: list[dict[str, Any]]) -> list[str]:
    tokens: list[str] = []
    for seg in segments:
        words = seg.get("words") or []
        if words:
            for w in words:
                txt = str(w.get("w", "") if isinstance(w, dict) else getattr(w, "w", "")).strip()
                if txt:
                    tokens.append(txt)
        else:
            text = str(seg.get("text", "")).strip()
            for tok in text.split():
                if tok:
                    tokens.append(tok)
    return tokens


def _token_stats(tokens: list[str]) -> dict[str, float | int]:
    latn = 0
    deva = 0
    beng = 0
    roman_hindi = 0
    alpha_tokens = 0

    for tok in tokens:
        # Only score script ratios over tokens containing alphabetic/syllabic characters
        has_alpha = any(ch.isalpha() for ch in tok)
        if not has_alpha:
            continue
        alpha_tokens += 1
        sc = detect_word_script(tok)
        if sc == "deva":
            deva += 1
        elif sc == "beng":
            beng += 1
        else:
            latn += 1
            clean = _clean_latin_lookup_token(tok)
            if clean in EXTENDED_HINDI_ROMAN_LEXICON:
                roman_hindi += 1

    denom = max(1, alpha_tokens)
    return {
        "token_count": len(tokens),
        "alpha_count": alpha_tokens,
        "latn_ratio": latn / denom,
        "deva_ratio": deva / denom,
        "beng_ratio": beng / denom,
        "roman_hindi_ratio": roman_hindi / denom,
    }


def _classify_stats(
    stats: dict[str, float | int],
    *,
    hinglish_latin_token_ratio: float,
    asr_hint_lang: str | None = None,
) -> str:
    latn_ratio = float(stats["latn_ratio"])
    deva_ratio = float(stats["deva_ratio"])
    beng_ratio = float(stats["beng_ratio"])
    roman_hindi_ratio = float(stats["roman_hindi_ratio"])
    hint = (asr_hint_lang or "").strip().lower()

    if beng_ratio >= 0.25 or (hint == "bn" and beng_ratio >= 0.10):
        return "bn"

    # Explicit Hindi hint or Devanagari presence + Latin token ratio >= threshold -> hinglish
    if hint == "hi" and latn_ratio >= hinglish_latin_token_ratio:
        return "hinglish"
    if deva_ratio > 0.0 and latn_ratio >= hinglish_latin_token_ratio and deva_ratio >= 0.15:
        return "hinglish"
    if deva_ratio >= 0.30 or (hint == "hi" and deva_ratio >= 0.10):
        return "hi"

    # All-Romanized or mixed Roman Hinglish (no Devanagari script emitted)
    if roman_hindi_ratio >= 0.12 and latn_ratio >= hinglish_latin_token_ratio:
        return "hinglish"
    if hint == "hinglish":
        return "hinglish"
    if hint == "hi":
        return "hi"
    return "en"


def _normalize_windows(
    windows_or_segments: Any,
    *,
    audio_duration: float | None,
    window_s: float,
    num_windows: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (window_specs, all_segments_for_overall_stats)."""
    # Unwrap transcript dict or RawTranscript object
    if isinstance(windows_or_segments, dict):
        if audio_duration is None and isinstance(windows_or_segments.get("duration"), (int, float)):
            audio_duration = float(windows_or_segments["duration"])
        raw_items: Any = windows_or_segments.get("segments", [])
    elif hasattr(windows_or_segments, "segments"):
        if audio_duration is None and isinstance(getattr(windows_or_segments, "duration", None), (int, float)):
            audio_duration = float(windows_or_segments.duration)
        raw_items = windows_or_segments.segments
    else:
        raw_items = windows_or_segments

    if not raw_items:
        return [], []

    items = list(raw_items)

    # Case 1: List of window lists -> [[seg, ...], [seg, ...], ...]
    if isinstance(items[0], list):
        labels = ["start", "middle", "end"]
        win_specs: list[dict[str, Any]] = []
        all_segs: list[dict[str, Any]] = []
        for idx, sub in enumerate(items):
            segs = [_seg_to_dict(s) for s in sub]
            all_segs.extend(segs)
            label = labels[idx] if idx < len(labels) else f"window_{idx}"
            w_start = min((float(s.get("start", 0.0)) for s in segs), default=0.0)
            w_end = max((float(s.get("end", 0.0)) for s in segs), default=0.0)
            win_specs.append(
                {
                    "window": label,
                    "start": w_start,
                    "end": w_end,
                    "segments": segs,
                    "hint_lang": None,
                }
            )
        return win_specs, all_segs

    # Case 2: List of window dicts -> [{"window": ..., "segments": [...], "language": ...}, ...]
    if isinstance(items[0], dict) and "segments" in items[0]:
        labels = ["start", "middle", "end"]
        win_specs = []
        all_segs = []
        for idx, wdict in enumerate(items):
            segs = [_seg_to_dict(s) for s in (wdict.get("segments") or [])]
            all_segs.extend(segs)
            label = str(wdict.get("window") or (labels[idx] if idx < len(labels) else f"window_{idx}"))
            w_start = float(wdict.get("start", min((float(s.get("start", 0.0)) for s in segs), default=0.0)))
            w_end = float(wdict.get("end", max((float(s.get("end", 0.0)) for s in segs), default=0.0)))
            hint = wdict.get("language") or wdict.get("detected_lang")
            win_specs.append(
                {
                    "window": label,
                    "start": w_start,
                    "end": w_end,
                    "segments": segs,
                    "hint_lang": str(hint) if hint else None,
                }
            )
        return win_specs, all_segs

    # Case 3: Flat list of segments -> partition into start / middle / end windows
    flat_segs = [_seg_to_dict(s) for s in items]
    max_end = max((float(s.get("end", 0.0)) for s in flat_segs), default=0.0)
    duration = float(audio_duration) if (audio_duration is not None and audio_duration > 0) else max_end

    if duration <= 0.0 or num_windows <= 1:
        return [
            {
                "window": "start",
                "start": 0.0,
                "end": max(0.0, duration),
                "segments": flat_segs,
                "hint_lang": None,
            }
        ], flat_segs

    span = min(float(window_s), duration)
    half = span / 2.0
    mid_center = duration / 2.0
    bounds: list[tuple[str, float, float]] = [
        ("start", 0.0, round(min(duration, span), 3)),
        ("middle", round(max(0.0, mid_center - half), 3), round(min(duration, mid_center + half), 3)),
        ("end", round(max(0.0, duration - span), 3), round(duration, 3)),
    ]

    win_specs = []
    for label, w_start, w_end in bounds[:num_windows]:
        matched = [
            s
            for s in flat_segs
            if float(s.get("end", 0.0)) >= w_start and float(s.get("start", 0.0)) <= w_end
        ]
        if not matched:
            # Fallback to midpoint inclusion if timestamps are point-like
            matched = [
                s
                for s in flat_segs
                if w_start <= ((float(s.get("start", 0.0)) + float(s.get("end", 0.0))) / 2.0) <= w_end
            ]
        win_specs.append(
            {
                "window": label,
                "start": w_start,
                "end": w_end,
                "segments": matched,
                "hint_lang": None,
            }
        )
    return win_specs, flat_segs


def detect_language_multiwindow(
    windows_or_segments: Any,
    *,
    declared_lang: str = "auto",
    audio_duration: float | None = None,
    hinglish_latin_token_ratio: float = 0.25,
    window_s: float = 30.0,
    num_windows: int = 3,
) -> LanguageDetectionResult:
    """Detect language (en, hi, hinglish, bn) via 3-window majority vote + Hinglish heuristic."""
    win_specs, all_segs = _normalize_windows(
        windows_or_segments,
        audio_duration=audio_duration,
        window_s=window_s,
        num_windows=num_windows,
    )

    all_tokens = _extract_tokens_from_segments(all_segs)
    overall_stats = _token_stats(all_tokens)

    window_votes: list[dict[str, Any]] = []
    non_empty_votes: list[str] = []
    hint_langs: list[str] = []

    for spec in win_specs:
        w_tokens = _extract_tokens_from_segments(spec["segments"])
        w_stats = _token_stats(w_tokens)
        hint_lang = spec.get("hint_lang")
        if hint_lang:
            hint_langs.append(str(hint_lang).strip().lower())

        if int(w_stats["alpha_count"]) == 0 and not w_tokens:
            vote_lang: str | None = hint_lang if hint_lang in SUPPORTED_LANGUAGES else None
        else:
            vote_lang = _classify_stats(
                w_stats,
                hinglish_latin_token_ratio=hinglish_latin_token_ratio,
                asr_hint_lang=hint_lang,
            )

        if vote_lang is not None:
            non_empty_votes.append(vote_lang)

        window_votes.append(
            {
                "window": spec["window"],
                "start": round(float(spec["start"]), 3),
                "end": round(float(spec["end"]), 3),
                "language": vote_lang,
                "token_count": int(w_stats["token_count"]),
                "latin_token_ratio": round(float(w_stats["latn_ratio"]), 4),
                "devanagari_ratio": round(float(w_stats["deva_ratio"]), 4),
                "bengali_ratio": round(float(w_stats["beng_ratio"]), 4),
                "roman_hindi_ratio": round(float(w_stats["roman_hindi_ratio"]), 4),
            }
        )

    # Overall classification serves as tie-breaker or fallback when all windows are empty
    overall_hint = Counter(hint_langs).most_common(1)[0][0] if hint_langs else None
    overall_lang = _classify_stats(
        overall_stats,
        hinglish_latin_token_ratio=hinglish_latin_token_ratio,
        asr_hint_lang=overall_hint,
    )

    if non_empty_votes:
        counts = Counter(non_empty_votes)
        ranked = counts.most_common()
        top_lang, top_count = ranked[0]
        tied = [lang for lang, cnt in ranked if cnt == top_count]
        if len(tied) == 1:
            detected_lang = top_lang
        elif overall_lang in tied:
            detected_lang = overall_lang
        else:
            detected_lang = top_lang

        # Spec rule: detected hi AND >= 25% Latin-script tokens overall -> hinglish
        if detected_lang == "hi" and float(overall_stats["latn_ratio"]) >= hinglish_latin_token_ratio:
            detected_lang = "hinglish"

        vote_agreement = top_count / len(non_empty_votes)
    else:
        detected_lang = overall_lang
        vote_agreement = 0.0 if not all_tokens else 1.0

    norm_declared = (declared_lang or "auto").strip().lower()
    override_applied = bool(norm_declared and norm_declared != "auto")
    final_lang = norm_declared if override_applied else detected_lang
    final_script = script_for_language(final_lang)
    confidence = 1.0 if override_applied else vote_agreement

    return LanguageDetectionResult(
        language=final_lang,
        script=final_script,
        override_applied=override_applied,
        detected_language=detected_lang,
        window_votes=window_votes,
        latin_token_ratio=float(overall_stats["latn_ratio"]),
        devanagari_ratio=float(overall_stats["deva_ratio"]),
        bengali_ratio=float(overall_stats["beng_ratio"]),
        roman_hindi_ratio=float(overall_stats["roman_hindi_ratio"]),
        confidence=confidence,
        hinglish_latin_token_ratio_threshold=hinglish_latin_token_ratio,
    )


def write_language_evidence(result: LanguageDetectionResult, job_dir: Path) -> Path:
    """Persist detection evidence to `<job_dir>/asr/lang.json`."""
    return result.write_json(Path(job_dir) / "asr" / "lang.json")
