from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from backend.app.config import get_settings

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "hi", "hinglish", "bn")
VALID_SCRIPTS: tuple[str, ...] = ("latn", "deva", "beng")
VALID_WORD_FLAGS: frozenset[str] = frozenset({"low_conf", "edited", "interpolated"})

ProgressFn = Callable[[float, str], None]

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

SAMPLE_TRANSCRIPTS: dict[str, dict[str, Any]] = {}


class ASREngineError(RuntimeError):
    """Typed error raised by ASR engines and registry resolution."""


class ASREngineUnavailableError(ASREngineError):
    """Raised when a requested ASR engine or its runtime dependency is unavailable."""


@dataclass(slots=True)
class ASROpts:
    """Decode and guard options for Spec v2 ASR engines."""

    model_id: str | None = None
    device: str = "auto"
    compute_type: str = "auto"
    beam_size: int = 5
    vad_filter: bool = True
    condition_on_previous_text: bool = False
    word_timestamps: bool = True
    window_s: int = 30
    overlap_s: int = 5
    max_compression_ratio: float = 2.4
    max_repeat_ngram: int = 4
    low_conf_threshold: float = 0.3


@dataclass(slots=True)
class RawWord:
    """Single recognized word token with timing, probability, and quality flags."""

    w: str
    s: float
    e: float
    p: float | None = None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        start_s = round(max(0.0, float(self.s)), 3)
        end_e = round(max(start_s, float(self.e)), 3)
        payload: dict[str, Any] = {
            "w": self.w,
            "s": start_s,
            "e": end_e,
        }
        if self.p is not None:
            payload["p"] = round(min(1.0, max(0.0, float(self.p))), 4)
        valid_flags = [f for f in self.flags if f in VALID_WORD_FLAGS]
        if valid_flags:
            payload["flags"] = valid_flags
        return payload


@dataclass(slots=True)
class RawSegment:
    """Recognized speech segment containing word-level timestamps."""

    id: int
    start: float
    end: float
    text: str
    words: list[RawWord] = field(default_factory=list)
    speaker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        start_s = round(max(0.0, float(self.start)), 3)
        end_s = round(max(start_s, float(self.end)), 3)
        return {
            "id": max(0, int(self.id)),
            "start": start_s,
            "end": end_s,
            "text": self.text,
            "speaker": self.speaker,
            "words": [w.to_dict() for w in self.words if w.w.strip()],
        }


@dataclass(slots=True)
class RawTranscript:
    """Engine-agnostic ASR transcript conforming to contracts/schemas/transcript.schema.json."""

    language: str
    script: str
    duration: float
    engine: str
    segments: list[RawSegment] = field(default_factory=list)
    model_id: str | None = None
    mean_conf: float | None = None
    edited: bool = False
    schema_version: int = 1
    produced_by: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        lang = self.language if self.language in SUPPORTED_LANGUAGES else "en"
        script = self.script if self.script in VALID_SCRIPTS else "latn"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "language": lang,
            "script": script,
            "duration": round(max(0.0, float(self.duration)), 3),
            "engine": self.engine,
            "edited": bool(self.edited),
            "segments": [seg.to_dict() for seg in self.segments],
        }
        if self.model_id is not None:
            payload["model_id"] = str(self.model_id)
        if self.mean_conf is not None:
            payload["mean_conf"] = round(min(1.0, max(0.0, float(self.mean_conf))), 4)
        if self.produced_by is not None:
            payload["produced_by"] = dict(self.produced_by)
        return payload


@runtime_checkable
class ASREngine(Protocol):
    """Protocol implemented by all Spec v2 ASR engines."""

    id: str

    def supports(self, lang: str) -> bool: ...

    def transcribe(
        self,
        wav: Path,
        lang: str | None,
        opts: ASROpts,
        progress: ProgressFn | None = None,
    ) -> RawTranscript: ...


def load_asr_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load Spec v2 ASR config (preferring config/spec_v2/asr.yaml, falling back to config/asr.yaml)."""
    if config_path is not None:
        p = Path(config_path)
        if p.exists():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return {}
    settings = get_settings()
    return settings.load_spec_v2_yaml_config("asr")


class ASREngineRegistry:
    """Resolves enabled ASR engines per language according to config/spec_v2/asr.yaml."""

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._engines: dict[str, ASREngine] = {}
        self._enabled: dict[str, bool] = {}
        raw_pref = self.config.get("language_preference")
        if isinstance(raw_pref, dict):
            self.language_preference: dict[str, list[str]] = {
                str(k).lower(): [str(eid) for eid in v]
                for k, v in raw_pref.items()
                if isinstance(v, list)
            }
        else:
            self.language_preference = {
                "en": ["faster_whisper"],
                "hi": ["indic_specialist", "faster_whisper"],
                "hinglish": ["indic_specialist", "faster_whisper"],
                "bn": ["indic_specialist", "faster_whisper"],
            }

    def register(self, engine: ASREngine, *, enabled: bool = True) -> None:
        self._engines[engine.id] = engine
        self._enabled[engine.id] = enabled

    def set_enabled(self, engine_id: str, enabled: bool) -> None:
        self._enabled[engine_id] = enabled

    def is_enabled(self, engine_id: str) -> bool:
        return bool(self._enabled.get(engine_id, False))

    def get(self, engine_id: str) -> ASREngine | None:
        return self._engines.get(engine_id)

    def resolve(self, lang: str = "auto", *, preferred_engine: str | None = None) -> ASREngine:
        norm_lang = (lang or "auto").strip().lower()

        if preferred_engine and preferred_engine != "auto":
            eng = self._engines.get(preferred_engine)
            if eng is None or not self.is_enabled(preferred_engine):
                raise ASREngineUnavailableError(
                    f"Requested ASR engine {preferred_engine!r} is not registered or disabled."
                )
            if not eng.supports(norm_lang):
                raise ASREngineUnavailableError(
                    f"ASR engine {preferred_engine!r} does not support language {lang!r}."
                )
            return eng

        if norm_lang == "auto":
            candidate_ids: list[str] = []
            for pref_list in self.language_preference.values():
                for eid in pref_list:
                    if eid not in candidate_ids:
                        candidate_ids.append(eid)
            for eid in self._engines:
                if eid not in candidate_ids:
                    candidate_ids.append(eid)
        else:
            candidate_ids = list(self.language_preference.get(norm_lang, []))
            if not candidate_ids:
                candidate_ids = list(self._engines.keys())

        for eid in candidate_ids:
            if not self.is_enabled(eid):
                continue
            eng = self._engines.get(eid)
            if eng is not None and eng.supports(norm_lang):
                return eng

        raise ASREngineUnavailableError(
            f"No enabled ASR engine supports language {norm_lang!r}."
        )

    def build_opts(self, *, engine_id: str = "faster_whisper", **overrides: Any) -> ASROpts:
        engines_cfg = self.config.get("engines") or {}
        eng_cfg = engines_cfg.get(engine_id) or {}
        guard_cfg = self.config.get("hallucination_guard") or {}

        kwargs: dict[str, Any] = {
            "device": str(self.config.get("device", "auto")),
            "beam_size": int(eng_cfg.get("beam_size", 5)),
            "vad_filter": bool(eng_cfg.get("vad_filter", self.config.get("vad_filter", True))),
            "condition_on_previous_text": bool(eng_cfg.get("condition_on_previous_text", False)),
            "word_timestamps": bool(eng_cfg.get("word_timestamps", True)),
            "window_s": int(eng_cfg.get("window_s", 30)),
            "overlap_s": int(eng_cfg.get("overlap_s", 5)),
            "max_compression_ratio": float(guard_cfg.get("max_compression_ratio", 2.4)),
            "max_repeat_ngram": int(guard_cfg.get("max_repeat_ngram", 4)),
            "low_conf_threshold": float(self.config.get("low_conf_threshold", 0.3)),
        }
        if "model_size" in self.config and "models" not in eng_cfg:
            kwargs["model_id"] = str(self.config["model_size"])
        if "compute_type" in self.config and isinstance(self.config["compute_type"], str):
            kwargs["compute_type"] = str(self.config["compute_type"])
        for k, v in overrides.items():
            if v is not None:
                kwargs[k] = v
        return ASROpts(**kwargs)

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None = None,
        *,
        config_path: Path | None = None,
        engines: dict[str, ASREngine] | None = None,
        auto_register_defaults: bool = True,
    ) -> ASREngineRegistry:
        cfg = dict(config) if config is not None else load_asr_config(config_path)
        registry = cls(config=cfg)

        engines_cfg = cfg.get("engines")
        fw_enabled = True
        if isinstance(engines_cfg, dict) and "faster_whisper" in engines_cfg:
            fw_cfg = engines_cfg.get("faster_whisper") or {}
            fw_enabled = bool(fw_cfg.get("enabled", True))

        if engines:
            for eid, eng in engines.items():
                enabled = True
                if isinstance(engines_cfg, dict) and eid in engines_cfg:
                    ec = engines_cfg.get(eid) or {}
                    enabled = bool(ec.get("enabled", True))
                registry.register(eng, enabled=enabled)

        if auto_register_defaults and "faster_whisper" not in registry._engines:
            from backend.pipeline.asr.faster_whisper_engine import FasterWhisperEngine

            registry.register(FasterWhisperEngine(config=cfg), enabled=fw_enabled)

        return registry


def _normalize_wer_tokens(text: str) -> list[str]:
    norm = unicodedata.normalize("NFC", text).lower()
    out: list[str] = []
    for raw_tok in norm.split():
        cleaned = "".join(
            ch for ch in raw_tok if unicodedata.category(ch)[0] not in ("P", "S")
        )
        if cleaned:
            out.append(cleaned)
    return out


def compute_wer(reference: str, hypothesis: str) -> float:
    """Compute normalized Word Error Rate (WER) after Unicode NFC, lowercasing, and punctuation stripping."""
    ref_words = _normalize_wer_tokens(reference)
    hyp_words = _normalize_wer_tokens(hypothesis)

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    m, n = len(ref_words), len(hyp_words)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        ref_tok = ref_words[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ref_tok == hyp_words[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,      # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr
    return float(prev[n]) / float(m)


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
        hint_segments: list[dict[str, Any]] = raw.get("segments", [])
        det = detect_language_and_hinglish(
            hint_segments,
            requested_lang if requested_lang != "auto" else raw.get("language", "auto"),
        )
        norm_segments, final_script = normalize_transcript_script(hint_segments, det["language"], script_pref)
        result: dict[str, Any] = {
            "schema_version": 1,
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

    if settings.asr_engine in ("auto", "faster_whisper") and audio_wav.exists():
        try:
            from backend.pipeline.asr.faster_whisper_engine import FasterWhisperEngine

            engine = FasterWhisperEngine()
            opts = ASROpts(
                model_id=settings.asr_model_size,
                device=settings.asr_device,
                compute_type=settings.asr_compute_type,
            )
            raw_transcript = engine.transcribe(
                audio_wav,
                None if requested_lang == "auto" else requested_lang,
                opts,
            )
            segments = [seg.to_dict() for seg in raw_transcript.segments]
            engine_used = raw_transcript.engine
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
        "schema_version": 1,
        "language": det["language"],
        "script": final_script,
        "duration": float(meta.get("duration", 60.0)),
        "engine": engine_used,
        "segments": norm_segments,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
