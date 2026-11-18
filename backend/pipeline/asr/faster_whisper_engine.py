from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import unicodedata
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.app.config import get_settings
from backend.pipeline.asr.engine import (
    SUPPORTED_LANGUAGES,
    ASREngine,
    ASREngineError,
    ASREngineUnavailableError,
    ASROpts,
    ProgressFn,
    RawSegment,
    RawTranscript,
    RawWord,
    compute_wer,
    detect_language_and_hinglish,
    detect_word_script,
    romanize_devanagari,
)

__all__ = [
    "ASREngine",
    "ASREngineError",
    "ASREngineUnavailableError",
    "ASROpts",
    "FasterWhisperEngine",
    "ModelCache",
    "ModelFactory",
    "ProgressFn",
    "RawSegment",
    "RawTranscript",
    "RawWord",
    "compute_wer",
    "default_model_for_device",
    "detect_asr_device",
    "is_hallucinated_segment",
    "max_consecutive_ngram_repeat",
    "resolve_model_and_device",
]

ModelFactory = Callable[[str, str, str, Path], Any]


def _cuda_available_via_ctranslate2() -> bool:
    try:
        import ctranslate2  # type: ignore[import-not-found]

        get_count = getattr(ctranslate2, "get_cuda_device_count", None)
        if callable(get_count):
            return int(get_count()) > 0
    except Exception:
        return False
    return False


def _cuda_available_via_torch() -> bool:
    try:
        import torch  # type: ignore[import-not-found]

        cuda_mod = getattr(torch, "cuda", None)
        is_avail = getattr(cuda_mod, "is_available", None)
        if callable(is_avail):
            return bool(is_avail())
    except Exception:
        return False
    return False


def _cuda_available_via_nvidia_smi() -> bool:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False
    try:
        proc = subprocess.run(
            [nvidia_smi, "-L"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return proc.returncode == 0 and "GPU " in proc.stdout
    except Exception:
        return False


def detect_asr_device(preferred: str = "auto") -> tuple[str, str]:
    """Detect optimal (device, compute_type) pair for faster-whisper without failing if GPU libs are absent."""
    pref = (preferred or "auto").strip().lower()
    if pref == "cpu":
        return ("cpu", "int8")
    if pref == "cuda":
        return ("cuda", "float16")

    if (
        _cuda_available_via_ctranslate2()
        or _cuda_available_via_torch()
        or _cuda_available_via_nvidia_smi()
    ):
        return ("cuda", "float16")
    return ("cpu", "int8")


def default_model_for_device(device: str, config: dict[str, Any] | None = None) -> str:
    """Return default Whisper model ID for the target device (cuda->large-v3, cpu->medium)."""
    if config:
        models_cfg = ((config.get("engines") or {}).get("faster_whisper") or {}).get("models")
        if isinstance(models_cfg, dict) and isinstance(models_cfg.get(device), str):
            return str(models_cfg[device])
    return "large-v3" if device == "cuda" else "medium"


def resolve_model_and_device(
    opts: ASROpts,
    config: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Resolve (model_id, device, compute_type) from ASROpts, hardware probe, and optional config."""
    device, auto_compute = detect_asr_device(opts.device)

    compute_type = opts.compute_type
    if not compute_type or compute_type == "auto":
        cfg_compute = (
            ((config or {}).get("engines") or {})
            .get("faster_whisper", {})
            .get("compute_type")
        )
        if isinstance(cfg_compute, dict) and isinstance(cfg_compute.get(device), str):
            compute_type = str(cfg_compute[device])
        else:
            compute_type = auto_compute

    if opts.model_id and opts.model_id != "auto":
        model_id = opts.model_id
    else:
        model_id = default_model_for_device(device, config)

    return model_id, device, compute_type


def _default_whisper_model_factory(
    model_id: str,
    device: str,
    compute_type: str,
    download_root: Path,
) -> Any:
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ASREngineUnavailableError(
            "faster-whisper is not installed in this environment. "
            "Install ASR extras (`pip install -e '.[asr]'`) or inject a mock model factory."
        ) from exc

    return WhisperModel(
        model_id,
        device=device,
        compute_type=compute_type,
        download_root=str(download_root),
    )


class ModelCache:
    """Thread-safe lazy loader and cache for faster-whisper WhisperModel instances."""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._models: dict[tuple[str, str, str, str], Any] = {}
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._model_factory = model_factory

    def resolve_download_root(self) -> Path:
        if self._cache_dir is not None:
            root = self._cache_dir / "whisper"
        else:
            settings = get_settings()
            base_cache = getattr(settings, "cache_dir", None)
            if base_cache is not None:
                root = Path(base_cache) / "whisper"
            else:
                root = Path(settings.data_dir) / "cache" / "whisper"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def get_or_load(
        self,
        model_id: str,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: Path | None = None,
    ) -> Any:
        root = Path(download_root) if download_root is not None else self.resolve_download_root()
        root.mkdir(parents=True, exist_ok=True)
        key = (model_id, device, compute_type, str(root))
        with self._lock:
            if key in self._models:
                return self._models[key]
            factory = self._model_factory or _default_whisper_model_factory
            model = factory(model_id, device, compute_type, root)
            self._models[key] = model
            return model

    def get(
        self,
        model_id: str,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: Path | None = None,
    ) -> Any:
        return self.get_or_load(
            model_id,
            device=device,
            compute_type=compute_type,
            download_root=download_root,
        )

    def clear(self) -> None:
        with self._lock:
            self._models.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._models)


def _tokenize_for_guard(text: str) -> list[str]:
    norm = unicodedata.normalize("NFC", text).lower()
    tokens: list[str] = []
    for raw_tok in norm.split():
        cleaned = "".join(
            ch for ch in raw_tok if unicodedata.category(ch)[0] not in ("P", "S")
        )
        if cleaned:
            tokens.append(cleaned)
    return tokens


def max_consecutive_ngram_repeat(text: str, max_n: int = 5) -> int:
    """Return the highest consecutive repetition count of any n-gram (1..max_n) in text."""
    tokens = _tokenize_for_guard(text)
    total = len(tokens)
    if total == 0:
        return 0

    best = 1
    upper_n = min(max_n, max(1, total // 2))
    for n in range(1, upper_n + 1):
        i = 0
        while i + n <= total:
            gram = tokens[i : i + n]
            count = 1
            j = i + n
            while j + n <= total and tokens[j : j + n] == gram:
                count += 1
                j += n
            if count > best:
                best = count
            i += n if count > 1 else 1
    return best


def is_hallucinated_segment(
    text: str,
    *,
    compression_ratio: float | None = None,
    max_compression_ratio: float = 2.4,
    max_repeat_ngram: int = 4,
) -> bool:
    """Return True if segment triggers Whisper compression-ratio or n-gram repetition guard."""
    if compression_ratio is not None and float(compression_ratio) > float(max_compression_ratio):
        return True
    if max_consecutive_ngram_repeat(text) > int(max_repeat_ngram):
        return True
    return False


def _probe_wav_info(wav: Path) -> tuple[float, int, int, int] | None:
    """Return (duration_s, nchannels, sampwidth, framerate) if wav is readable PCM WAV, else None."""
    try:
        with wave.open(str(wav), "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            if framerate <= 0 or nchannels <= 0 or sampwidth <= 0:
                return None
            return (float(nframes) / float(framerate), nchannels, sampwidth, framerate)
    except (wave.Error, EOFError, OSError):
        return None


def _compute_windows(duration: float, window_s: float, overlap_s: float) -> list[tuple[float, float]]:
    if duration <= window_s or window_s <= 0:
        return [(0.0, duration)]
    step = max(1.0, window_s - max(0.0, min(overlap_s, window_s * 0.5)))
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(duration, start + window_s)
        windows.append((start, end))
        if end >= duration:
            break
        start += step
    return windows


def _write_wav_slice(src_wav: Path, dest_wav: Path, start_s: float, end_s: float) -> None:
    with wave.open(str(src_wav), "rb") as src_wf:
        nchannels = src_wf.getnchannels()
        sampwidth = src_wf.getsampwidth()
        framerate = src_wf.getframerate()
        nframes = src_wf.getnframes()

        start_frame = max(0, min(nframes, int(round(start_s * framerate))))
        end_frame = max(start_frame, min(nframes, int(round(end_s * framerate))))
        src_wf.setpos(start_frame)
        frames_bytes = src_wf.readframes(end_frame - start_frame)

    with wave.open(str(dest_wav), "wb") as dst_wf:
        dst_wf.setnchannels(nchannels)
        dst_wf.setsampwidth(sampwidth)
        dst_wf.setframerate(framerate)
        dst_wf.writeframes(frames_bytes)


def _extract_segment_words(
    seg: Any,
    *,
    offset_s: float,
    low_conf_threshold: float,
) -> list[RawWord]:
    raw_words = getattr(seg, "words", None)
    words: list[RawWord] = []

    if raw_words is not None:
        for w in raw_words:
            token = unicodedata.normalize("NFC", str(getattr(w, "word", "") or "")).strip()
            if not token:
                continue
            ws = max(0.0, float(getattr(w, "start", 0.0)) + offset_s)
            we = max(ws, float(getattr(w, "end", ws)) + offset_s)
            prob_raw = getattr(w, "probability", getattr(w, "prob", None))
            prob: float | None = None
            flags: list[str] = []
            if isinstance(prob_raw, (int, float)):
                prob = min(1.0, max(0.0, float(prob_raw)))
                if prob < low_conf_threshold:
                    flags.append("low_conf")
            words.append(RawWord(w=token, s=ws, e=we, p=prob, flags=flags))
        return words

    # Fallback when segment omits word list completely
    text = unicodedata.normalize("NFC", str(getattr(seg, "text", "") or "")).strip()
    tokens = [t for t in text.split() if t]
    if not tokens:
        return words
    seg_start = max(0.0, float(getattr(seg, "start", 0.0)) + offset_s)
    seg_end = max(seg_start, float(getattr(seg, "end", seg_start)) + offset_s)
    step = max(0.01, seg_end - seg_start) / len(tokens)
    for idx, tok in enumerate(tokens):
        ws = seg_start + idx * step
        we = seg_start + (idx + 0.9) * step
        words.append(RawWord(w=tok, s=ws, e=we, p=None, flags=["interpolated"]))
    return words


class FasterWhisperEngine:
    """Spec v2 ASREngine implementation backed by faster-whisper and ModelCache."""

    id: str = "faster_whisper"

    def __init__(
        self,
        *,
        model_cache: ModelCache | None = None,
        model_factory: ModelFactory | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        if model_cache is not None:
            self.model_cache = model_cache
        else:
            self.model_cache = ModelCache(model_factory=model_factory)
        self.config: dict[str, Any] = dict(config or {})

    def supports(self, lang: str) -> bool:
        norm = (lang or "auto").strip().lower()
        return norm in (*SUPPORTED_LANGUAGES, "auto")

    def transcribe(
        self,
        wav: Path,
        lang: str | None = None,
        opts: ASROpts | None = None,
        progress: ProgressFn | None = None,
    ) -> RawTranscript:
        wav_path = Path(wav)
        if not wav_path.exists() or not wav_path.is_file():
            raise FileNotFoundError(f"Audio WAV file not found: {wav_path}")

        effective_opts = opts or ASROpts()
        model_id, device, compute_type = resolve_model_and_device(effective_opts, self.config)

        last_pct = 0.0

        def _emit(pct: float, msg: str) -> None:
            nonlocal last_pct
            if progress is None:
                return
            clamped = max(last_pct, min(100.0, float(pct)))
            last_pct = clamped
            progress(clamped, msg)

        _emit(5.0, f"Loading faster-whisper model {model_id} ({device}/{compute_type})...")
        model = self.model_cache.get_or_load(
            model_id,
            device=device,
            compute_type=compute_type,
        )
        _emit(15.0, "Running speech recognition...")

        req_lang = (lang or "auto").strip().lower()
        whisper_lang: str | None
        if req_lang in ("", "auto"):
            whisper_lang = None
        elif req_lang == "hinglish":
            whisper_lang = "hi"
        else:
            whisper_lang = req_lang

        wav_info = _probe_wav_info(wav_path)
        wav_duration = wav_info[0] if wav_info is not None else None

        window_s = float(effective_opts.window_s)
        overlap_s = float(effective_opts.overlap_s)
        use_windowing = (
            wav_duration is not None
            and window_s > 0.0
            and wav_duration > window_s
        )

        collected_segments: list[RawSegment] = []
        detected_info_lang: str | None = None
        detected_info_duration: float | None = None

        def _transcribe_single(audio_file: Path) -> tuple[list[Any], Any]:
            seg_iter, info = model.transcribe(
                str(audio_file),
                language=whisper_lang,
                beam_size=effective_opts.beam_size,
                vad_filter=effective_opts.vad_filter,
                condition_on_previous_text=effective_opts.condition_on_previous_text,
                word_timestamps=effective_opts.word_timestamps,
            )
            return list(seg_iter), info

        if use_windowing and wav_duration is not None:
            windows = _compute_windows(wav_duration, window_s, overlap_s)
            total_windows = len(windows)
            with tempfile.TemporaryDirectory(prefix="clipper_asr_win_") as tmp_dir_str:
                tmp_dir = Path(tmp_dir_str)
                for win_idx, (w_start, w_end) in enumerate(windows):
                    slice_path = tmp_dir / f"win_{win_idx:04d}.wav"
                    _write_wav_slice(wav_path, slice_path, w_start, w_end)
                    raw_segs, info = _transcribe_single(slice_path)

                    info_lang = getattr(info, "language", None)
                    if isinstance(info_lang, str) and not detected_info_lang:
                        detected_info_lang = info_lang.lower()

                    left_cut = 0.0 if win_idx == 0 else w_start + overlap_s / 2.0
                    right_cut = (
                        float("inf")
                        if win_idx == total_windows - 1
                        else w_end - overlap_s / 2.0
                    )

                    for s in raw_segs:
                        text = unicodedata.normalize("NFC", str(getattr(s, "text", "") or "")).strip()
                        if not text:
                            continue
                        comp_ratio_raw = getattr(s, "compression_ratio", None)
                        comp_ratio = (
                            float(comp_ratio_raw)
                            if isinstance(comp_ratio_raw, (int, float))
                            else None
                        )
                        if is_hallucinated_segment(
                            text,
                            compression_ratio=comp_ratio,
                            max_compression_ratio=effective_opts.max_compression_ratio,
                            max_repeat_ngram=effective_opts.max_repeat_ngram,
                        ):
                            continue

                        seg_start = max(0.0, float(getattr(s, "start", 0.0)) + w_start)
                        seg_end = max(seg_start, float(getattr(s, "end", seg_start)) + w_start)
                        seg_mid = (seg_start + seg_end) / 2.0
                        if not (left_cut <= seg_mid < right_cut):
                            continue

                        words = _extract_segment_words(
                            s,
                            offset_s=w_start,
                            low_conf_threshold=effective_opts.low_conf_threshold,
                        )
                        if collected_segments and seg_start < collected_segments[-1].end and words:
                            prev_end = collected_segments[-1].end
                            trimmed_words = [w for w in words if w.s >= prev_end - 0.1]
                            if not trimmed_words:
                                continue
                            words = trimmed_words
                            seg_start = words[0].s
                            text = " ".join(w.w for w in words)

                        collected_segments.append(
                            RawSegment(
                                id=len(collected_segments),
                                start=seg_start,
                                end=seg_end,
                                text=text,
                                words=words,
                            )
                        )

                    win_pct = 20.0 + 70.0 * ((win_idx + 1) / max(1, total_windows))
                    _emit(win_pct, f"Transcribed window {win_idx + 1}/{total_windows}...")
        else:
            raw_segs, info = _transcribe_single(wav_path)
            info_lang = getattr(info, "language", None)
            if isinstance(info_lang, str):
                detected_info_lang = info_lang.lower()
            info_dur = getattr(info, "duration", None)
            if isinstance(info_dur, (int, float)) and float(info_dur) > 0:
                detected_info_duration = float(info_dur)

            total_segs = max(1, len(raw_segs))
            for idx, s in enumerate(raw_segs):
                text = unicodedata.normalize("NFC", str(getattr(s, "text", "") or "")).strip()
                if not text:
                    continue
                comp_ratio_raw = getattr(s, "compression_ratio", None)
                comp_ratio = (
                    float(comp_ratio_raw)
                    if isinstance(comp_ratio_raw, (int, float))
                    else None
                )
                if is_hallucinated_segment(
                    text,
                    compression_ratio=comp_ratio,
                    max_compression_ratio=effective_opts.max_compression_ratio,
                    max_repeat_ngram=effective_opts.max_repeat_ngram,
                ):
                    continue

                seg_start = max(0.0, float(getattr(s, "start", 0.0)))
                seg_end = max(seg_start, float(getattr(s, "end", seg_start)))
                words = _extract_segment_words(
                    s,
                    offset_s=0.0,
                    low_conf_threshold=effective_opts.low_conf_threshold,
                )
                collected_segments.append(
                    RawSegment(
                        id=len(collected_segments),
                        start=seg_start,
                        end=seg_end,
                        text=text,
                        words=words,
                    )
                )
                seg_pct = 20.0 + 70.0 * ((idx + 1) / total_segs)
                _emit(seg_pct, f"Processed segment {idx + 1}/{total_segs}...")

        # Re-index sequentially
        for i, seg in enumerate(collected_segments):
            seg.id = i

        # Resolve language & script
        seg_dicts_for_lang = [
            {"text": seg.text, "words": [{"w": w.w} for w in seg.words]}
            for seg in collected_segments
        ]
        if req_lang in SUPPORTED_LANGUAGES:
            final_lang = req_lang
        else:
            det = detect_language_and_hinglish(seg_dicts_for_lang, "auto")
            final_lang = det["language"]
            if not collected_segments and detected_info_lang in ("en", "hi", "bn"):
                final_lang = detected_info_lang

        if final_lang == "bn":
            final_script = "beng"
        elif final_lang == "hi":
            final_script = "deva"
        else:
            final_script = "latn"

        # Romanize Devanagari tokens when target language is Hinglish (Roman script default)
        if final_lang == "hinglish":
            for seg in collected_segments:
                if detect_word_script(seg.text) == "deva":
                    seg.text = romanize_devanagari(seg.text)
                for w in seg.words:
                    if detect_word_script(w.w) == "deva":
                        w.w = romanize_devanagari(w.w)

        probs = [w.p for seg in collected_segments for w in seg.words if w.p is not None]
        mean_conf: float | None = None
        if probs:
            mean_conf = round(sum(probs) / len(probs), 4)

        max_seg_end = max((seg.end for seg in collected_segments), default=0.0)
        duration = wav_duration or detected_info_duration or max_seg_end

        _emit(100.0, f"Transcription complete ({len(collected_segments)} segments).")

        return RawTranscript(
            language=final_lang,
            script=final_script,
            duration=float(duration),
            engine=self.id,
            model_id=model_id,
            mean_conf=mean_conf,
            edited=False,
            segments=collected_segments,
        )
