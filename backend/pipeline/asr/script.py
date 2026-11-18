from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from copy import deepcopy
from typing import Any

from backend.pipeline.asr.engine import detect_word_script
from backend.pipeline.asr.language import detect_language_multiwindow, script_for_language

LLMFallbackFn = Callable[[list[str], str, str], Any]

_DEVA_INDEPENDENT_VOWELS: dict[str, str] = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ऋ": "ri",
    "ॠ": "ree",
    "ऌ": "lri",
    "ऍ": "e",
    "ऎ": "e",
    "ए": "e",
    "ऐ": "ai",
    "ऑ": "o",
    "ऒ": "o",
    "ओ": "o",
    "औ": "au",
}

_DEVA_MATRAS: dict[str, str] = {
    "ा": "aa",
    "ि": "i",
    "ी": "ee",
    "ु": "u",
    "ू": "oo",
    "ृ": "ri",
    "ॄ": "ree",
    "ॅ": "e",
    "ॆ": "e",
    "े": "e",
    "ै": "ai",
    "ॉ": "o",
    "ो": "o",
    "ौ": "au",
}

_DEVA_CONSONANTS: dict[str, str] = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "ऩ": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ऱ": "r",
    "ल": "l",
    "ळ": "l",
    "ऴ": "zh",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    # Precomposed nukta consonants
    "क़": "q",
    "ख़": "kh",
    "ग़": "gh",
    "ज़": "z",
    "ड़": "r",
    "ढ़": "rh",
    "फ़": "f",
    "य़": "y",
    "\u0958": "q",
    "\u0959": "kh",
    "\u095a": "gh",
    "\u095b": "z",
    "\u095c": "r",
    "\u095d": "rh",
    "\u095e": "f",
    "\u095f": "y",
}

_DEVA_NUKTA_BASE_MAP: dict[str, str] = {
    "क": "q",
    "ख": "kh",
    "ग": "gh",
    "ज": "z",
    "ड": "r",
    "ढ": "rh",
    "फ": "f",
    "य": "y",
}

_DEVA_DIGITS: dict[str, str] = {
    "०": "0",
    "१": "1",
    "२": "2",
    "३": "3",
    "४": "4",
    "५": "5",
    "६": "6",
    "७": "7",
    "८": "8",
    "९": "9",
}

_DEVA_PUNCT: dict[str, str] = {
    "।": ".",
    "॥": ".",
    "॰": ".",
}

_VIRAMA = "\u094d"
_NUKTA = "\u093c"
_ANUSVARA = "\u0902"
_CHANDRABINDU = "\u0901"
_VISARGA = "\u0903"
_AVAGRAHA = "\u093d"
_LABIAL_CONSONANTS = frozenset({"प", "फ", "ब", "भ", "म"})

_DEVA_RUN_RE = re.compile(r"[\u0900-\u097F]+")

DEFAULT_PROFANITY_WORDS: frozenset[str] = frozenset(
    {
        "fuck",
        "fucking",
        "shit",
        "bitch",
        "asshole",
        "bastard",
        "chutiya",
        "madarchod",
        "bhenchod",
        "bc",
        "mc",
    }
)


def _is_deva_consonant_at(run: str, idx: int) -> bool:
    return run[idx] in _DEVA_CONSONANTS


def _transliterate_deva_run(run: str) -> str:
    """Transliterate a contiguous Devanagari substring into Roman/Latin characters."""
    # Keep decomposed nukta sequences consistent before scanning
    chars = list(unicodedata.normalize("NFC", run))
    n = len(chars)
    out: list[str] = []
    i = 0

    while i < n:
        ch = chars[i]

        if ch in _DEVA_DIGITS:
            out.append(_DEVA_DIGITS[ch])
            i += 1
            continue

        if ch in _DEVA_PUNCT:
            out.append(_DEVA_PUNCT[ch])
            i += 1
            continue

        if ch in _DEVA_INDEPENDENT_VOWELS:
            out.append(_DEVA_INDEPENDENT_VOWELS[ch])
            i += 1
            continue

        if ch == _ANUSVARA:
            next_ch = chars[i + 1] if i + 1 < n else ""
            out.append("m" if next_ch in _LABIAL_CONSONANTS else "n")
            i += 1
            continue

        if ch == _CHANDRABINDU:
            out.append("n")
            i += 1
            continue

        if ch == _VISARGA:
            out.append("h")
            i += 1
            continue

        if ch == _AVAGRAHA:
            i += 1
            continue

        if ch in _DEVA_CONSONANTS:
            base = _DEVA_CONSONANTS[ch]
            j = i + 1

            # Handle combining nukta if not already precomposed
            if j < n and chars[j] == _NUKTA:
                base = _DEVA_NUKTA_BASE_MAP.get(ch, base)
                j += 1

            # Handle virama (halant) -> suppress inherent vowel
            if j < n and chars[j] == _VIRAMA:
                # Cleaner gemination for च् + च/छ -> cch
                if ch == "च" and j + 1 < n and chars[j + 1] in ("च", "छ"):
                    out.append("c")
                else:
                    out.append(base)
                i = j + 1
                continue

            # Handle explicit dependent vowel matra
            if j < n and chars[j] in _DEVA_MATRAS:
                matra = _DEVA_MATRAS[chars[j]]
                # Word-final long aa often rendered as single 'a' when multi-syllabic unless short monosyllable
                if matra == "aa" and j == n - 1 and len(out) >= 1:
                    out.append(base + "a")
                else:
                    out.append(base + matra)
                i = j + 1
                continue

            # Inherent schwa 'a': drop at word-final position if word already has earlier content
            is_word_final = j >= n
            has_prior_vowel_or_syllable = len(out) > 0
            if is_word_final and has_prior_vowel_or_syllable:
                out.append(base)
            else:
                out.append(base + "a")
            i = j
            continue

        if ch in _DEVA_MATRAS:
            out.append(_DEVA_MATRAS[ch])
            i += 1
            continue

        if ch in (_VIRAMA, _NUKTA):
            i += 1
            continue

        # Any other Devanagari symbol
        i += 1

    return "".join(out)


def transliterate_devanagari_to_roman(text: str) -> str:
    """Rule-based Devanagari -> Roman transliterator leaving existing Latin/English tokens untouched."""
    nfc = unicodedata.normalize("NFC", text)
    if not _DEVA_RUN_RE.search(nfc):
        return nfc
    return _DEVA_RUN_RE.sub(lambda m: _transliterate_deva_run(m.group(0)), nfc)


def transliterate_devanagari_token(token: str) -> str:
    """Transliterate a single token preserving non-empty single-token invariant."""
    nfc = unicodedata.normalize("NFC", token)
    if detect_word_script(nfc) != "deva" and not _DEVA_RUN_RE.search(nfc):
        return nfc
    converted = transliterate_devanagari_to_roman(nfc)
    # Remove any accidental whitespace so 1 token in -> 1 token out
    converted = "".join(converted.split())
    return converted if converted else nfc


def _coerce_segments_and_language(
    transcript_or_segments: Any,
    language: str,
) -> tuple[list[dict[str, Any]], str]:
    resolved_lang = (language or "auto").strip().lower()
    if isinstance(transcript_or_segments, dict):
        raw_segs = transcript_or_segments.get("segments", [])
        if resolved_lang == "auto" and isinstance(transcript_or_segments.get("language"), str):
            resolved_lang = str(transcript_or_segments["language"]).strip().lower()
    elif hasattr(transcript_or_segments, "segments"):
        raw_segs = transcript_or_segments.segments
        if resolved_lang == "auto" and isinstance(getattr(transcript_or_segments, "language", None), str):
            resolved_lang = str(transcript_or_segments.language).strip().lower()
    else:
        raw_segs = transcript_or_segments

    seg_dicts: list[dict[str, Any]] = []
    for seg in raw_segs:
        if isinstance(seg, dict):
            seg_copy = deepcopy(seg)
        else:
            words_list: list[dict[str, Any]] = []
            for w in getattr(seg, "words", []) or []:
                if isinstance(w, dict):
                    words_list.append(deepcopy(w))
                elif hasattr(w, "to_dict"):
                    words_list.append(w.to_dict())
                else:
                    words_list.append(
                        {
                            "w": str(getattr(w, "w", "")),
                            "s": float(getattr(w, "s", 0.0)),
                            "e": float(getattr(w, "e", 0.0)),
                        }
                    )
            seg_copy = {
                "id": int(getattr(seg, "id", len(seg_dicts))),
                "start": float(getattr(seg, "start", 0.0)),
                "end": float(getattr(seg, "end", 0.0)),
                "text": str(getattr(seg, "text", "")),
                "speaker": getattr(seg, "speaker", None),
                "words": words_list,
            }
        seg_dicts.append(seg_copy)

    if resolved_lang == "auto":
        det = detect_language_multiwindow(seg_dicts, declared_lang="auto")
        resolved_lang = det.language

    return seg_dicts, resolved_lang


def _validate_llm_tokens(
    raw_output: Any,
    input_tokens: list[str],
) -> list[str] | None:
    """Strictly validate that LLM transliteration preserves 1-to-1 token count and pure Latin tokens."""
    if raw_output is None:
        return None

    parsed: list[str] | None = None
    if isinstance(raw_output, (list, tuple)):
        parsed = [str(item) for item in raw_output]
    elif isinstance(raw_output, str):
        stripped = raw_output.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                loaded = json.loads(stripped)
                if isinstance(loaded, list):
                    parsed = [str(item) for item in loaded]
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            parsed = stripped.split()

    if parsed is None or len(parsed) != len(input_tokens):
        return None

    cleaned_out: list[str] = []
    for orig, cand in zip(input_tokens, parsed, strict=True):
        c_norm = unicodedata.normalize("NFC", cand).strip()
        if not c_norm or any(ch.isspace() for ch in c_norm):
            return None
        # Pure Latin tokens must remain intact
        if not _DEVA_RUN_RE.search(orig) and detect_word_script(orig) == "latn":
            if c_norm != unicodedata.normalize("NFC", orig):
                return None
        cleaned_out.append(c_norm)

    return cleaned_out


def normalize_script_tokens(
    transcript_or_segments: Any,
    *,
    language: str = "auto",
    script_pref: str = "auto",
    llm_fallback: LLMFallbackFn | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Normalize script (NFC + token-count-preserving transliteration) across segments & words.

    Invariant: 1 input word token -> exactly 1 output word token, preserving `s`, `e`, `p`, `flags`.
    """
    seg_dicts, resolved_lang = _coerce_segments_and_language(transcript_or_segments, language)

    norm_pref = (script_pref or "auto").strip().lower()
    if norm_pref in ("latn", "deva", "beng"):
        target_script = norm_pref
    else:
        target_script = script_for_language(resolved_lang)

    normalized_segments: list[dict[str, Any]] = []
    for seg in seg_dicts:
        seg_out = dict(seg)
        words = seg_out.get("words") or []

        if words:
            input_tokens = [
                unicodedata.normalize("NFC", str(w.get("w", "")))
                for w in words
            ]
            needs_deva_to_roman = target_script == "latn" and any(
                _DEVA_RUN_RE.search(tok) for tok in input_tokens
            )

            llm_tokens: list[str] | None = None
            if needs_deva_to_roman and llm_fallback is not None:
                try:
                    raw_llm = llm_fallback(list(input_tokens), resolved_lang, target_script)
                    llm_tokens = _validate_llm_tokens(raw_llm, input_tokens)
                except Exception:
                    llm_tokens = None

            out_words: list[dict[str, Any]] = []
            for idx, w in enumerate(words):
                w_out = dict(w)
                orig_tok = input_tokens[idx]
                if llm_tokens is not None:
                    new_tok = llm_tokens[idx]
                elif target_script == "latn" and _DEVA_RUN_RE.search(orig_tok):
                    new_tok = transliterate_devanagari_token(orig_tok)
                else:
                    new_tok = orig_tok

                new_tok = unicodedata.normalize("NFC", new_tok)
                if not new_tok:
                    new_tok = orig_tok
                w_out["w"] = new_tok
                w_out["script"] = detect_word_script(new_tok)
                out_words.append(w_out)

            seg_out["words"] = out_words
            seg_out["text"] = " ".join(w["w"] for w in out_words)
        else:
            raw_text = unicodedata.normalize("NFC", str(seg_out.get("text", "")))
            if target_script == "latn" and _DEVA_RUN_RE.search(raw_text):
                raw_text = transliterate_devanagari_to_roman(raw_text)
            seg_out["text"] = unicodedata.normalize("NFC", raw_text)

        normalized_segments.append(seg_out)

    return normalized_segments, target_script


def restore_punctuation_and_casing(
    segments: list[dict[str, Any]],
    *,
    language: str = "en",
) -> list[dict[str, Any]]:
    """Deterministic, token-count-preserving light casing restoration for Latin tokens."""
    norm_lang = (language or "en").strip().lower()
    out: list[dict[str, Any]] = []
    for seg in segments:
        seg_copy = deepcopy(seg)
        words = seg_copy.get("words") or []
        if not words:
            out.append(seg_copy)
            continue

        at_sentence_start = True
        for w in words:
            tok = unicodedata.normalize("NFC", str(w.get("w", "")))
            if tok and detect_word_script(tok) == "latn":
                stripped_core = tok.strip(".,!?;:\"'()-")
                if norm_lang == "en" and stripped_core == "i":
                    tok = tok.replace("i", "I", 1)
                elif at_sentence_start and tok[0].islower():
                    tok = tok[0].upper() + tok[1:]
                w["w"] = tok
            at_sentence_start = tok.endswith((".", "!", "?", "।"))
        seg_copy["text"] = " ".join(str(w.get("w", "")) for w in words)
        out.append(seg_copy)
    return out


def _mask_single_token(token: str, profanity: frozenset[str], mask_char: str) -> str:
    match = re.match(r"^([^\w]*)([\w]+)([^\w]*)$", token)
    if not match:
        return token
    prefix, core, suffix = match.groups()
    if core.lower() not in profanity:
        return token
    if len(core) <= 2:
        masked = mask_char * len(core)
    else:
        masked = core[0] + (mask_char * (len(core) - 2)) + core[-1]
    return f"{prefix}{masked}{suffix}"


def mask_profanity_for_captions(
    words_or_segments: list[dict[str, Any]],
    *,
    profanity_list: Iterable[str] | None = None,
    mask_char: str = "*",
) -> list[dict[str, Any]]:
    """Caption-safe profanity masking that preserves token count and all timestamps."""
    profanity = (
        frozenset(w.strip().lower() for w in profanity_list if w.strip())
        if profanity_list is not None
        else DEFAULT_PROFANITY_WORDS
    )
    out: list[dict[str, Any]] = []
    for item in words_or_segments:
        item_copy = deepcopy(item)
        if "words" in item_copy:
            words = item_copy.get("words") or []
            for w in words:
                w["w"] = _mask_single_token(str(w.get("w", "")), profanity, mask_char)
            if words:
                item_copy["text"] = " ".join(str(w.get("w", "")) for w in words)
            else:
                text_tokens = str(item_copy.get("text", "")).split()
                item_copy["text"] = " ".join(
                    _mask_single_token(t, profanity, mask_char) for t in text_tokens
                )
        elif "w" in item_copy:
            item_copy["w"] = _mask_single_token(str(item_copy.get("w", "")), profanity, mask_char)
        out.append(item_copy)
    return out
