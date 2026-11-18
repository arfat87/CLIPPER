from __future__ import annotations

import copy
import importlib
import math
import statistics
import threading
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from backend.pipeline.asr.engine import (
    SUPPORTED_LANGUAGES,
    VALID_SCRIPTS,
    VALID_WORD_FLAGS,
    ASREngineError,
    RawTranscript,
    RawWord,
)
from backend.pipeline.asr.faster_whisper_engine import detect_asr_device

DEFAULT_ALIGNMENT_LANGUAGES: tuple[str, ...] = ("en", "hi")
DEFAULT_LOW_CONF_THRESHOLD: float = 0.3
DEFAULT_MIN_WORD_DURATION_S: float = 0.02

AlignModelLoader = Callable[[str, str], tuple[Any, Any]]
AudioLoader = Callable[[Path], Any]
WhisperXAlignFn = Callable[..., dict[str, Any]]

__all__ = [
    "AlignModelLoader",
    "AlignmentError",
    "AlignmentUnavailableError",
    "AudioLoader",
    "DEFAULT_ALIGNMENT_LANGUAGES",
    "DEFAULT_LOW_CONF_THRESHOLD",
    "DEFAULT_MIN_WORD_DURATION_S",
    "EngineWordTimeFallbackAligner",
    "WhisperXAlignFn",
    "WhisperXAligner",
    "WordAligner",
    "align_transcript",
    "extract_words",
    "median_word_boundary_error_ms",
    "median_word_end_error_ms",
    "median_word_start_error_ms",
    "word_boundary_error_ms",
    "word_end_error_ms",
    "word_start_error_ms",
]


class AlignmentError(ASREngineError):
    """Typed error raised by forced-alignment stages."""


class AlignmentUnavailableError(AlignmentError):
    """Raised when forced alignment cannot run for the requested language or runtime."""


@runtime_checkable
class WordAligner(Protocol):
    """Protocol implemented by word-level forced aligners and fallback timing refiners."""

    id: str

    def supports(self, lang: str) -> bool: ...

    def align(
        self,
        transcript: dict[str, Any],
        wav_path: Path,
        *,
        low_conf_threshold: float = DEFAULT_LOW_CONF_THRESHOLD,
    ) -> dict[str, Any]: ...


def _is_finite_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _coerce_float(val: Any) -> float | None:
    if _is_finite_number(val):
        return float(val)
    return None


def _coerce_prob(val: Any) -> float | None:
    num = _coerce_float(val)
    if num is None:
        return None
    return min(1.0, max(0.0, num))


def _default_script_for_language(lang: str) -> str:
    if lang == "bn":
        return "beng"
    if lang == "hi":
        return "deva"
    return "latn"


def _to_transcript_dict(transcript_dict_or_raw: RawTranscript | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(transcript_dict_or_raw, RawTranscript):
        payload = transcript_dict_or_raw.to_dict()
    elif isinstance(transcript_dict_or_raw, Mapping):
        payload = copy.deepcopy(dict(transcript_dict_or_raw))
    else:
        raise TypeError(
            f"Expected RawTranscript or Mapping, got {type(transcript_dict_or_raw).__name__}"
        )

    raw_lang = str(payload.get("language") or "en").strip().lower()
    lang = raw_lang if raw_lang in SUPPORTED_LANGUAGES else "en"
    raw_script = str(payload.get("script") or "").strip().lower()
    script = raw_script if raw_script in VALID_SCRIPTS else _default_script_for_language(lang)

    raw_segments = payload.get("segments")
    segments = list(raw_segments) if isinstance(raw_segments, list) else []

    max_seg_end = 0.0
    for seg in segments:
        if isinstance(seg, Mapping):
            se = _coerce_float(seg.get("end"))
            if se is not None and se > max_seg_end:
                max_seg_end = se

    duration = _coerce_float(payload.get("duration"))
    if duration is None or duration < 0.0:
        duration = max(0.0, max_seg_end)

    payload["schema_version"] = 1
    payload["language"] = lang
    payload["script"] = script
    payload["duration"] = round(max(0.0, duration), 3)
    payload["engine"] = str(payload.get("engine") or "faster_whisper")
    payload["edited"] = bool(payload.get("edited", False))
    payload["segments"] = segments
    return payload


def _normalize_word_token(raw_word: Mapping[str, Any]) -> str:
    txt = raw_word.get("w")
    if txt is None:
        txt = raw_word.get("word")
    return unicodedata.normalize("NFC", str(txt or "")).strip()


def _normalize_flags(
    raw_flags: Any,
    prob: float | None,
    low_conf_threshold: float,
    *,
    interpolated: bool = False,
) -> list[str]:
    flags: list[str] = []
    if isinstance(raw_flags, (list, tuple)):
        for f in raw_flags:
            fs = str(f)
            if fs in VALID_WORD_FLAGS and fs != "low_conf" and fs not in flags:
                flags.append(fs)
    if interpolated and "interpolated" not in flags:
        flags.append("interpolated")
    if prob is not None and prob < low_conf_threshold and "low_conf" not in flags:
        flags.append("low_conf")
    return flags


def _repair_segment_word_timings(
    seg_start: float,
    seg_end: float,
    raw_words: Sequence[Mapping[str, Any]],
    *,
    low_conf_threshold: float,
    min_word_duration: float = DEFAULT_MIN_WORD_DURATION_S,
) -> list[dict[str, Any]]:
    seg_s = max(0.0, float(seg_start))
    seg_e = max(seg_s, float(seg_end))

    cleaned_entries: list[dict[str, Any]] = []
    for rw in raw_words:
        token = _normalize_word_token(rw)
        if not token:
            continue
        ws = _coerce_float(rw.get("s") if "s" in rw else rw.get("start"))
        we = _coerce_float(rw.get("e") if "e" in rw else rw.get("end"))
        prob = _coerce_prob(
            rw.get("p")
            if "p" in rw
            else (
                rw.get("score")
                if "score" in rw
                else (rw.get("probability") if "probability" in rw else rw.get("prob"))
            )
        )
        cleaned_entries.append(
            {
                "w": token,
                "s": ws,
                "e": we,
                "p": prob,
                "flags": rw.get("flags"),
                "interpolated": False,
            }
        )

    n = len(cleaned_entries)
    if n == 0:
        return []

    # Step 1: Interpolate any words with missing/non-finite timestamps across available gaps.
    valid_mask = [e["s"] is not None and e["e"] is not None for e in cleaned_entries]
    idx = 0
    while idx < n:
        if valid_mask[idx]:
            idx += 1
            continue
        run_start = idx
        while idx < n and not valid_mask[idx]:
            idx += 1
        run_end = idx  # exclusive

        left_bound = seg_s
        if run_start > 0:
            prev_e = cleaned_entries[run_start - 1]["e"]
            if prev_e is not None:
                left_bound = max(seg_s, min(seg_e, float(prev_e)))

        right_bound = seg_e
        if run_end < n:
            next_s = cleaned_entries[run_end]["s"]
            if next_s is not None:
                right_bound = max(left_bound, min(seg_e, float(next_s)))
        right_bound = max(left_bound, right_bound)

        count = run_end - run_start
        step = (right_bound - left_bound) / float(count)
        for offset in range(count):
            entry = cleaned_entries[run_start + offset]
            ws = left_bound + offset * step
            we = left_bound + (offset + 0.9) * step if step > 0.0 else ws
            entry["s"] = ws
            entry["e"] = max(ws, we)
            entry["interpolated"] = True

    # Step 2: Clamp into [seg_s, seg_e], resolve overlaps and zero-duration words monotonically.
    span = seg_e - seg_s
    eff_min = min(max(0.0, float(min_word_duration)), span / float(n)) if span > 0.0 else 0.0

    repaired_bounds: list[tuple[float, float]] = []
    cursor = seg_s
    for i, entry in enumerate(cleaned_entries):
        rem = n - 1 - i
        max_end = seg_e - rem * eff_min
        max_start = max(seg_s, max_end - eff_min)

        raw_s = float(entry["s"])
        raw_e = float(entry["e"])
        ws = max(seg_s, min(seg_e, raw_s))
        we = max(ws, min(seg_e, raw_e))

        if i > 0 and ws < cursor:
            prev_s, prev_e = repaired_bounds[i - 1]
            if ws >= prev_s + eff_min and prev_e > ws:
                mid = (prev_e + ws) / 2.0
                upper_mid = min(max_start, max(ws, we - eff_min))
                mid = max(prev_s + eff_min, min(upper_mid, mid))
                repaired_bounds[i - 1] = (prev_s, mid)
                cursor = mid

        ws = max(cursor, min(ws, max_start))
        if we < ws + eff_min:
            we = min(max_end, ws + eff_min)
        else:
            we = min(max_end, we)
        if we < ws:
            we = ws

        repaired_bounds.append((ws, we))
        cursor = we

    # Step 3: Round to 3 decimals while strictly preserving 0 <= s <= e and word[i].e <= word[i+1].s.
    seg_s_r = round(seg_s, 3)
    seg_e_r = max(seg_s_r, round(seg_e, 3))
    cursor_r = seg_s_r
    out_words: list[dict[str, Any]] = []

    for i, (entry, (ws, we)) in enumerate(zip(cleaned_entries, repaired_bounds, strict=True)):
        rem = n - 1 - i
        sr = round(ws, 3)
        er = round(we, 3)
        sr = max(cursor_r, min(seg_e_r, sr))
        er = max(sr, min(seg_e_r, er))
        if er == sr and round(seg_e_r - sr, 3) >= round(0.001 * (rem + 1), 3):
            er = round(sr + 0.001, 3)
        cursor_r = er

        word_payload: dict[str, Any] = {
            "w": entry["w"],
            "s": sr,
            "e": er,
        }
        word_prob: float | None = entry["p"]
        if word_prob is not None:
            word_payload["p"] = round(word_prob, 4)
        flags = _normalize_flags(
            entry["flags"],
            word_prob,
            low_conf_threshold,
            interpolated=bool(entry["interpolated"]),
        )
        if flags:
            word_payload["flags"] = flags
        out_words.append(word_payload)

    return out_words


def _finalize_transcript_dict(
    transcript: dict[str, Any],
    *,
    low_conf_threshold: float,
    min_word_duration: float = DEFAULT_MIN_WORD_DURATION_S,
) -> dict[str, Any]:
    payload = _to_transcript_dict(transcript)
    final_segments: list[dict[str, Any]] = []
    probs: list[float] = []
    prev_seg_end = 0.0

    for idx, raw_seg in enumerate(payload["segments"]):
        if not isinstance(raw_seg, Mapping):
            continue
        seg_dict = copy.deepcopy(dict(raw_seg))
        raw_id = seg_dict.get("id")
        seg_id = int(raw_id) if isinstance(raw_id, int) and not isinstance(raw_id, bool) and raw_id >= 0 else idx

        raw_start = _coerce_float(seg_dict.get("start"))
        raw_end = _coerce_float(seg_dict.get("end"))
        seg_start = max(prev_seg_end, max(0.0, raw_start if raw_start is not None else prev_seg_end))
        seg_end = max(seg_start, raw_end if raw_end is not None else seg_start)

        raw_words = seg_dict.get("words")
        words_seq = [w for w in raw_words if isinstance(w, Mapping)] if isinstance(raw_words, list) else []

        repaired_words = _repair_segment_word_timings(
            seg_start,
            seg_end,
            words_seq,
            low_conf_threshold=low_conf_threshold,
            min_word_duration=min_word_duration,
        )

        for w in repaired_words:
            if "p" in w and _is_finite_number(w["p"]):
                probs.append(float(w["p"]))

        seg_start_r = round(seg_start, 3)
        seg_end_r = max(seg_start_r, round(seg_end, 3))
        if repaired_words:
            seg_end_r = max(seg_end_r, float(repaired_words[-1]["e"]))
        prev_seg_end = seg_end_r

        speaker_raw = seg_dict.get("speaker")
        speaker = str(speaker_raw) if isinstance(speaker_raw, str) else None

        seg_dict["id"] = seg_id
        seg_dict["start"] = seg_start_r
        seg_dict["end"] = seg_end_r
        seg_dict["text"] = unicodedata.normalize("NFC", str(seg_dict.get("text") or ""))
        seg_dict["speaker"] = speaker
        seg_dict["words"] = repaired_words
        final_segments.append(seg_dict)

    payload["segments"] = final_segments
    if probs:
        payload["mean_conf"] = round(sum(probs) / len(probs), 4)
    elif "mean_conf" in payload:
        mc = _coerce_prob(payload.get("mean_conf"))
        if mc is not None:
            payload["mean_conf"] = round(mc, 4)
        else:
            payload.pop("mean_conf", None)

    if final_segments:
        payload["duration"] = round(max(float(payload["duration"]), prev_seg_end), 3)

    if payload.get("model_id") is not None:
        payload["model_id"] = str(payload["model_id"])
    else:
        payload.pop("model_id", None)

    return payload


class EngineWordTimeFallbackAligner:
    """Fallback word aligner that refines engine word timings into monotonic segment-bounded tokens."""

    id: str = "engine_word_times"

    def __init__(self, *, min_word_duration: float = DEFAULT_MIN_WORD_DURATION_S) -> None:
        self.min_word_duration = max(0.0, float(min_word_duration))

    def supports(self, lang: str) -> bool:
        norm = (lang or "").strip().lower()
        return norm in SUPPORTED_LANGUAGES or norm == ""

    def align(
        self,
        transcript: dict[str, Any],
        wav_path: Path,  # noqa: ARG002 - kept for WordAligner protocol parity
        *,
        low_conf_threshold: float = DEFAULT_LOW_CONF_THRESHOLD,
    ) -> dict[str, Any]:
        return _finalize_transcript_dict(
            transcript,
            low_conf_threshold=low_conf_threshold,
            min_word_duration=self.min_word_duration,
        )


def _norm_token_for_match(token: str) -> str:
    norm = unicodedata.normalize("NFC", token).lower().strip()
    stripped = "".join(ch for ch in norm if unicodedata.category(ch)[0] not in ("P", "S"))
    return stripped or norm


def _reconcile_aligned_words(
    orig_words: Sequence[Mapping[str, Any]],
    aligned_words: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve exact original token count and text while adopting aligned timestamps and scores."""
    if not orig_words:
        out: list[dict[str, Any]] = []
        for aw in aligned_words:
            tok = _normalize_word_token(aw)
            if not tok:
                continue
            out.append(
                {
                    "w": tok,
                    "s": aw.get("s") if "s" in aw else aw.get("start"),
                    "e": aw.get("e") if "e" in aw else aw.get("end"),
                    "p": aw.get("p") if "p" in aw else aw.get("score"),
                    "flags": list(aw.get("flags") or []) if isinstance(aw.get("flags"), list) else [],
                }
            )
        return out

    # Fast exact-count path (normal WhisperX and mocked output case)
    if len(aligned_words) == len(orig_words):
        merged: list[dict[str, Any]] = []
        for ow, aw in zip(orig_words, aligned_words, strict=True):
            tok = _normalize_word_token(ow) or _normalize_word_token(aw)
            ws = aw.get("s") if "s" in aw else aw.get("start")
            we = aw.get("e") if "e" in aw else aw.get("end")
            if not _is_finite_number(ws) or not _is_finite_number(we):
                ws = ow.get("s") if "s" in ow else ow.get("start")
                we = ow.get("e") if "e" in ow else ow.get("end")
            prob = aw.get("p") if "p" in aw else aw.get("score")
            if not _is_finite_number(prob):
                prob = ow.get("p") if "p" in ow else ow.get("score")
            flags = list(ow.get("flags") or []) if isinstance(ow.get("flags"), list) else []
            merged.append({"w": tok, "s": ws, "e": we, "p": prob, "flags": flags})
        return merged

    # Token-count mismatch fallback: greedy monotonic text match, preserving every original token
    aligned_cursor = 0
    merged = []
    for ow in orig_words:
        orig_tok = _normalize_word_token(ow)
        orig_key = _norm_token_for_match(orig_tok)
        matched_aw: Mapping[str, Any] | None = None
        for probe in range(aligned_cursor, len(aligned_words)):
            cand = aligned_words[probe]
            cand_key = _norm_token_for_match(_normalize_word_token(cand))
            if cand_key == orig_key:
                matched_aw = cand
                aligned_cursor = probe + 1
                break

        if matched_aw is not None:
            ws = matched_aw.get("s") if "s" in matched_aw else matched_aw.get("start")
            we = matched_aw.get("e") if "e" in matched_aw else matched_aw.get("end")
            prob = matched_aw.get("p") if "p" in matched_aw else matched_aw.get("score")
        else:
            ws = ow.get("s") if "s" in ow else ow.get("start")
            we = ow.get("e") if "e" in ow else ow.get("end")
            prob = ow.get("p") if "p" in ow else ow.get("score")

        if not _is_finite_number(ws) or not _is_finite_number(we):
            ws = ow.get("s") if "s" in ow else ow.get("start")
            we = ow.get("e") if "e" in ow else ow.get("end")
        if not _is_finite_number(prob):
            prob = ow.get("p") if "p" in ow else ow.get("score")

        flags = list(ow.get("flags") or []) if isinstance(ow.get("flags"), list) else []
        merged.append({"w": orig_tok, "s": ws, "e": we, "p": prob, "flags": flags})
    return merged


class WhisperXAligner:
    """Lazy-loading WhisperX wav2vec2 forced aligner with thread-safe model cache and DI hooks."""

    id: str = "whisperx"

    def __init__(
        self,
        *,
        languages: Sequence[str] = DEFAULT_ALIGNMENT_LANGUAGES,
        device: str = "auto",
        model_loader: AlignModelLoader | None = None,
        audio_loader: AudioLoader | None = None,
        align_fn: WhisperXAlignFn | None = None,
        min_word_duration: float = DEFAULT_MIN_WORD_DURATION_S,
    ) -> None:
        self.languages: frozenset[str] = frozenset(
            str(lang).strip().lower() for lang in languages if str(lang).strip()
        )
        self.device = device
        self._model_loader = model_loader
        self._audio_loader = audio_loader
        self._align_fn = align_fn
        self.min_word_duration = max(0.0, float(min_word_duration))
        self._lock = threading.Lock()
        self._model_cache: dict[tuple[str, str], tuple[Any, Any]] = {}

    def supports(self, lang: str) -> bool:
        return (lang or "").strip().lower() in self.languages

    def clear_cache(self) -> None:
        with self._lock:
            self._model_cache.clear()

    def _resolve_device(self) -> str:
        pref = (self.device or "auto").strip().lower()
        if pref in ("cpu", "cuda"):
            return pref
        resolved, _ = detect_asr_device("auto")
        return resolved

    @staticmethod
    def _import_whisperx() -> Any:
        try:
            return importlib.import_module("whisperx")
        except Exception as exc:
            raise AlignmentUnavailableError(
                "whisperx is not installed or failed to import; falling back to engine word times."
            ) from exc

    def _get_or_load_model(self, lang: str, device: str, wx_mod: Any | None) -> tuple[Any, Any]:
        key = (lang, device)
        with self._lock:
            if key in self._model_cache:
                return self._model_cache[key]

            if self._model_loader is not None:
                loaded = self._model_loader(lang, device)
            else:
                if wx_mod is None:
                    wx_mod = self._import_whisperx()
                loaded = wx_mod.load_align_model(language_code=lang, device=device)

            if isinstance(loaded, tuple) and len(loaded) == 2:
                pair: tuple[Any, Any] = (loaded[0], loaded[1])
            else:
                pair = (loaded, {})
            self._model_cache[key] = pair
            return pair

    def align(
        self,
        transcript: dict[str, Any],
        wav_path: Path,
        *,
        low_conf_threshold: float = DEFAULT_LOW_CONF_THRESHOLD,
    ) -> dict[str, Any]:
        payload = _to_transcript_dict(transcript)
        lang = str(payload.get("language") or "").strip().lower()
        if not self.supports(lang):
            raise AlignmentUnavailableError(
                f"WhisperX forced alignment does not support language {lang!r}."
            )

        segments = payload.get("segments") or []
        if not segments:
            return _finalize_transcript_dict(
                payload,
                low_conf_threshold=low_conf_threshold,
                min_word_duration=self.min_word_duration,
            )

        wx_mod: Any | None = None
        if self._model_loader is None or self._align_fn is None:
            wx_mod = self._import_whisperx()

        device = self._resolve_device()
        align_model, metadata = self._get_or_load_model(lang, device, wx_mod)

        wav = Path(wav_path)
        if self._audio_loader is not None:
            audio = self._audio_loader(wav)
        elif wx_mod is not None:
            if not wav.exists():
                raise FileNotFoundError(f"Audio WAV file not found for alignment: {wav}")
            audio = wx_mod.load_audio(str(wav))
        else:
            audio = str(wav)

        wx_input_segments = [
            {
                "id": seg.get("id", idx),
                "start": float(seg.get("start", 0.0) or 0.0),
                "end": float(seg.get("end", seg.get("start", 0.0)) or 0.0),
                "text": str(seg.get("text") or ""),
            }
            for idx, seg in enumerate(segments)
        ]

        if self._align_fn is not None:
            aligned_result = self._align_fn(
                wx_input_segments,
                align_model,
                metadata,
                audio,
                device,
            )
        else:
            assert wx_mod is not None
            aligned_result = wx_mod.align(
                wx_input_segments,
                align_model,
                metadata,
                audio,
                device,
                return_char_alignments=False,
            )

        aligned_segments = (
            aligned_result.get("segments", [])
            if isinstance(aligned_result, Mapping)
            else []
        )

        new_segments: list[dict[str, Any]] = []
        for idx, orig_seg in enumerate(segments):
            seg_copy = copy.deepcopy(dict(orig_seg))
            raw_orig_words = seg_copy.get("words")
            orig_words: list[Mapping[str, Any]] = (
                [w for w in raw_orig_words if isinstance(w, Mapping)]
                if isinstance(raw_orig_words, list)
                else []
            )
            aligned_seg = aligned_segments[idx] if idx < len(aligned_segments) else None
            if isinstance(aligned_seg, Mapping):
                aligned_words = aligned_seg.get("words")
                if isinstance(aligned_words, list):
                    seg_copy["words"] = _reconcile_aligned_words(orig_words, aligned_words)
            new_segments.append(seg_copy)

        payload["segments"] = new_segments
        return _finalize_transcript_dict(
            payload,
            low_conf_threshold=low_conf_threshold,
            min_word_duration=self.min_word_duration,
        )


def align_transcript(
    transcript_dict_or_raw: RawTranscript | Mapping[str, Any],
    wav_path: Path,
    *,
    enabled: bool = True,
    languages: Sequence[str] = DEFAULT_ALIGNMENT_LANGUAGES,
    low_conf_threshold: float = DEFAULT_LOW_CONF_THRESHOLD,
    aligner: WordAligner | None = None,
) -> dict[str, Any]:
    """Run forced alignment when enabled & supported, cleanly falling back to engine word times."""
    payload = _to_transcript_dict(transcript_dict_or_raw)
    lang = str(payload["language"]).strip().lower()
    allowed_langs = {str(item).strip().lower() for item in languages if str(item).strip()}

    fallback_aligner = EngineWordTimeFallbackAligner()
    warnings: list[str] = []
    fallback_used: str | None = None
    aligner_used = fallback_aligner.id

    if not enabled:
        warnings.append("Forced alignment is disabled; using engine word times.")
        fallback_used = fallback_aligner.id
        aligned = fallback_aligner.align(payload, wav_path, low_conf_threshold=low_conf_threshold)
    elif lang not in allowed_langs:
        warnings.append(
            f"No forced-alignment model configured for language {lang!r}; using engine word times."
        )
        fallback_used = fallback_aligner.id
        aligned = fallback_aligner.align(payload, wav_path, low_conf_threshold=low_conf_threshold)
    else:
        primary: WordAligner = aligner if aligner is not None else WhisperXAligner(languages=tuple(allowed_langs))
        if not primary.supports(lang):
            warnings.append(
                f"Aligner {primary.id!r} does not support language {lang!r}; using engine word times."
            )
            fallback_used = fallback_aligner.id
            aligned = fallback_aligner.align(payload, wav_path, low_conf_threshold=low_conf_threshold)
        else:
            try:
                candidate = primary.align(
                    payload,
                    Path(wav_path),
                    low_conf_threshold=low_conf_threshold,
                )
                aligned = _finalize_transcript_dict(
                    candidate,
                    low_conf_threshold=low_conf_threshold,
                )
                aligner_used = primary.id
            except Exception as exc:
                warnings.append(
                    f"Forced alignment with {primary.id!r} failed ({exc}); falling back to engine word times."
                )
                fallback_used = fallback_aligner.id
                aligned = fallback_aligner.align(
                    payload,
                    wav_path,
                    low_conf_threshold=low_conf_threshold,
                )

    produced_by = dict(aligned.get("produced_by") or {})
    produced_by["alignment"] = {
        "aligner": aligner_used,
        "enabled": bool(enabled),
        "fallback_used": fallback_used,
        "warnings": warnings,
    }
    aligned["produced_by"] = produced_by
    return aligned


def extract_words(
    source: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Extract normalized word dicts ({w, s, e}) from a transcript dict, RawTranscript, or word list."""
    if isinstance(source, RawTranscript):
        source = source.to_dict()

    if isinstance(source, Mapping):
        if "segments" in source and isinstance(source["segments"], list):
            words: list[dict[str, Any]] = []
            for seg in source["segments"]:
                if isinstance(seg, Mapping) and isinstance(seg.get("words"), list):
                    for w in seg["words"]:
                        if isinstance(w, (RawWord, Mapping)):
                            words.extend(extract_words([w]))
            return words
        if "words" in source and isinstance(source["words"], list):
            return extract_words(source["words"])
        return []

    out: list[dict[str, Any]] = []
    for item in source:
        if isinstance(item, RawWord):
            out.append({"w": item.w, "s": float(item.s), "e": float(item.e)})
        elif isinstance(item, Mapping):
            ws = _coerce_float(item.get("s") if "s" in item else item.get("start"))
            we = _coerce_float(item.get("e") if "e" in item else item.get("end"))
            if ws is None or we is None:
                raise ValueError(f"Word item is missing numeric start/end timestamps: {item!r}")
            out.append({"w": _normalize_word_token(item), "s": ws, "e": we})
        else:
            raise TypeError(f"Unsupported word item type: {type(item).__name__}")
    return out


def _pair_word_sequences(
    predicted_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
    reference_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pred = extract_words(predicted_words)
    ref = extract_words(reference_words)
    if not pred and not ref:
        return []
    if len(pred) != len(ref):
        raise ValueError(
            f"Word count mismatch between predicted ({len(pred)}) and reference ({len(ref)}) words."
        )
    return list(zip(pred, ref, strict=True))


def word_start_error_ms(pred: RawWord | Mapping[str, Any], ref: RawWord | Mapping[str, Any]) -> float:
    """Return absolute word start boundary error in milliseconds."""
    p = extract_words([pred])[0]
    r = extract_words([ref])[0]
    return abs(float(p["s"]) - float(r["s"])) * 1000.0


def word_end_error_ms(pred: RawWord | Mapping[str, Any], ref: RawWord | Mapping[str, Any]) -> float:
    """Return absolute word end boundary error in milliseconds."""
    p = extract_words([pred])[0]
    r = extract_words([ref])[0]
    return abs(float(p["e"]) - float(r["e"])) * 1000.0


def word_boundary_error_ms(pred: RawWord | Mapping[str, Any], ref: RawWord | Mapping[str, Any]) -> float:
    """Return average start/end boundary error ((|pred.s - ref.s| + |pred.e - ref.e|) / 2) * 1000 ms."""
    p = extract_words([pred])[0]
    r = extract_words([ref])[0]
    return ((abs(float(p["s"]) - float(r["s"])) + abs(float(p["e"]) - float(r["e"]))) / 2.0) * 1000.0


def median_word_start_error_ms(
    predicted_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
    reference_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
) -> float:
    """Return median absolute start boundary error in milliseconds."""
    pairs = _pair_word_sequences(predicted_words, reference_words)
    if not pairs:
        return 0.0
    errors = [abs(p["s"] - r["s"]) * 1000.0 for p, r in pairs]
    return float(statistics.median(errors))


def median_word_end_error_ms(
    predicted_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
    reference_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
) -> float:
    """Return median absolute end boundary error in milliseconds."""
    pairs = _pair_word_sequences(predicted_words, reference_words)
    if not pairs:
        return 0.0
    errors = [abs(p["e"] - r["e"]) * 1000.0 for p, r in pairs]
    return float(statistics.median(errors))


def median_word_boundary_error_ms(
    predicted_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
    reference_words: RawTranscript | Mapping[str, Any] | Sequence[RawWord | Mapping[str, Any]],
) -> float:
    """Return median((|pred.s - ref.s| + |pred.e - ref.e|) / 2) * 1000 ms across aligned words."""
    pairs = _pair_word_sequences(predicted_words, reference_words)
    if not pairs:
        return 0.0
    errors = [
        ((abs(p["s"] - r["s"]) + abs(p["e"] - r["e"])) / 2.0) * 1000.0
        for p, r in pairs
    ]
    return float(statistics.median(errors))
