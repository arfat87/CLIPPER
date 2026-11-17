from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from backend.app.config import get_settings

HINDI_ROMAN_LEXICON = {
    "dosto", "aaj", "hum", "baat", "karenge", "ke", "baare", "mein", "kyunki",
    "woh", "pehle", "teen", "nahi", "dete", "agar", "aapka", "hai", "toh",
    "koi", "rukega", "hi", "samjhe", "doosra", "zyada", "zaroori", "har",
    "ek", "dena", "jo", "turant", "kare", "jab", "maine", "apna", "banaya",
    "tab", "mujhe", "samajh", "aaya", "ki", "aur", "asli", "hain", "kya",
    "kaise", "bahut", "acha", "accha", "bhai", "yaar",
}

DEVANAGARI_TO_ROMAN = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "क": "ka", "ख": "kha",
    "ग": "ga", "घ": "gha", "च": "cha", "छ": "chha", "ज": "ja", "झ": "jha",
    "ट": "ta", "ठ": "tha", "ड": "da", "ढ": "dha", "ण": "na", "त": "ta",
    "थ": "tha", "द": "da", "ध": "dha", "न": "na", "प": "pa", "फ": "pha",
    "ब": "ba", "भ": "bha", "म": "ma", "य": "ya", "र": "ra", "ल": "la",
    "व": "va", "श": "sha", "ष": "sha", "स": "sa", "ह": "ha",
    "ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "े": "e",
    "ै": "ai", "ो": "o", "ौ": "au", "ं": "n", "्": "", "ँ": "n",
}


def detect_word_script(word: str) -> str:
    """Detect whether a token is Devanagari ('deva'), Bengali ('beng'), or Latin ('latn')."""
    for ch in word:
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F:
            return "deva"
        if 0x0980 <= cp <= 0x09FF:
            return "beng"
    return "latn"


def detect_language_and_hinglish(segments: list[dict[str, Any]], declared_lang: str = "auto") -> dict[str, str]:
    """Detect language (en, hi, hinglish, bn) and dominant script (latn, deva, beng)."""
    all_words = [w["w"] for seg in segments for w in seg.get("words", [])]
    if not all_words:
        all_words = " ".join(seg.get("text", "") for seg in segments).split()

    script_counts = {"latn": 0, "deva": 0, "beng": 0}
    roman_hindi_hits = 0
    for w in all_words:
        sc = detect_word_script(w)
        script_counts[sc] += 1
        clean = re.sub(r"[^\w]", "", w.lower())
        if clean in HINDI_ROMAN_LEXICON:
            roman_hindi_hits += 1

    total = max(1, len(all_words))
    if declared_lang and declared_lang != "auto":
        lang = declared_lang
    elif script_counts["beng"] / total >= 0.25:
        lang = "bn"
    elif script_counts["deva"] / total >= 0.40 and script_counts["latn"] / total >= 0.20:
        lang = "hinglish"
    elif script_counts["deva"] / total >= 0.30:
        lang = "hi"
    elif roman_hindi_hits / total >= 0.12:
        lang = "hinglish"
    else:
        lang = "en"

    script = "beng" if lang == "bn" else ("deva" if lang == "hi" else "latn")
    return {"language": lang, "script": script}


def romanize_devanagari(text: str) -> str:
    out = []
    for ch in text:
        out.append(DEVANAGARI_TO_ROMAN.get(ch, ch))
    return "".join(out)


def normalize_transcript_script(
    segments: list[dict[str, Any]],
    language: str,
    script_pref: str = "auto",
) -> tuple[list[dict[str, Any]], str]:
    """Normalize Unicode NFC and optionally transliterate Devanagari to Roman for Hinglish."""
    target_script = script_pref
    if target_script == "auto":
        if language == "bn":
            target_script = "beng"
        elif language == "hi":
            target_script = "deva"
        else:
            target_script = "latn"

    normalized: list[dict[str, Any]] = []
    for seg in segments:
        seg_copy = dict(seg)
        txt = unicodedata.normalize("NFC", seg_copy.get("text", ""))
        if target_script == "latn" and detect_word_script(txt) == "deva":
            txt = romanize_devanagari(txt)
        seg_copy["text"] = txt
        new_words = []
        for w in seg_copy.get("words", []):
            w_copy = dict(w)
            wt = unicodedata.normalize("NFC", str(w_copy.get("w", "")))
            if target_script == "latn" and detect_word_script(wt) == "deva":
                wt = romanize_devanagari(wt)
            w_copy["w"] = wt
            w_copy["script"] = detect_word_script(wt)
            new_words.append(w_copy)
        seg_copy["words"] = new_words
        normalized.append(seg_copy)

    return normalized, target_script


def transcribe_job(job_dir: Path, requested_lang: str = "auto", script_pref: str = "auto") -> dict[str, Any]:
    """Run word-level transcription via faster-whisper if installed, or load sample/heuristic transcript."""
    meta_path = job_dir / "source" / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    hint_path = meta.get("transcript_hint")
    asr_dir = job_dir / "asr"
    asr_dir.mkdir(parents=True, exist_ok=True)
    out_path = asr_dir / "transcript.json"

    if hint_path and Path(hint_path).exists():
        raw = json.loads(Path(hint_path).read_text(encoding="utf-8"))
        segments = raw.get("segments", [])
        det = detect_language_and_hinglish(segments, requested_lang if requested_lang != "auto" else raw.get("language", "auto"))
        norm_segments, final_script = normalize_transcript_script(segments, det["language"], script_pref)
        result = {
            "language": det["language"],
            "script": final_script,
            "duration": float(raw.get("duration", meta.get("duration", 60.0))),
            "engine": "sample_fixture",
            "segments": norm_segments,
        }
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    # Try faster-whisper if installed
    settings = get_settings()
    audio_wav = job_dir / "audio" / "audio.wav"
    segments: list[dict[str, Any]] = []
    engine_used = "heuristic_fallback"

    if settings.asr_engine in ("auto", "faster_whisper"):
        try:
            from faster_whisper import WhisperModel  # type: ignore

            whisper_lang = None if requested_lang in ("auto", "hinglish") else requested_lang
            if requested_lang == "hinglish":
                whisper_lang = "hi"
            model = WhisperModel(
                settings.asr_model_size,
                device="cpu" if settings.asr_device == "auto" else settings.asr_device,
                compute_type=settings.asr_compute_type,
            )
            seg_iter, _info = model.transcribe(
                str(audio_wav),
                language=whisper_lang,
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            for idx, s in enumerate(seg_iter):
                words = [
                    {
                        "w": w.word.strip(),
                        "s": round(float(w.start), 2),
                        "e": round(float(w.end), 2),
                        "p": round(float(getattr(w, "probability", 0.9)), 2),
                    }
                    for w in (s.words or [])
                    if w.word.strip()
                ]
                segments.append(
                    {
                        "id": idx,
                        "start": round(float(s.start), 2),
                        "end": round(float(s.end), 2),
                        "text": s.text.strip(),
                        "words": words,
                    }
                )
            engine_used = "faster_whisper"
        except Exception:
            segments = []

    if not segments:
        # Offline fallback segment generator for arbitrary uploaded videos when faster-whisper model weights aren't cached
        duration = float(meta.get("duration", 60.0))
        chunk = 18.0
        t = 1.0
        idx = 0
        while t < duration - 3.0:
            end_t = min(duration - 0.5, t + chunk)
            txt = f"Key highlight moment number {idx + 1} with high impact insights and clear takeaway for viewers!"
            raw_words = txt.split()
            step = (end_t - t) / len(raw_words)
            words = [
                {
                    "w": w,
                    "s": round(t + i * step, 2),
                    "e": round(t + (i + 0.9) * step, 2),
                    "p": 0.92,
                }
                for i, w in enumerate(raw_words)
            ]
            segments.append(
                {
                    "id": idx,
                    "start": round(t, 2),
                    "end": round(end_t, 2),
                    "text": txt,
                    "words": words,
                }
            )
            t = end_t + 1.5
            idx += 1

    det = detect_language_and_hinglish(segments, requested_lang)
    norm_segments, final_script = normalize_transcript_script(segments, det["language"], script_pref)
    result = {
        "language": det["language"],
        "script": final_script,
        "duration": float(meta.get("duration", 60.0)),
        "engine": engine_used,
        "segments": norm_segments,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
