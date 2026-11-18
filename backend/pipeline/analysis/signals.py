from __future__ import annotations

import math
import unicodedata
import wave
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from backend.pipeline.analysis.scenes import detect_scenes, scene_density_1hz

DEFAULT_SILENCE_THRESHOLD_DBFS: float = -40.0
DEFAULT_MIN_SILENCE_S: float = 0.4
DEFAULT_THOUGHT_MAX_DURATION_S: float = 18.0
DEFAULT_THOUGHT_MIN_PAUSE_S: float = 0.5

_SENTENCE_END_PUNCT = (".", "!", "?", "।", "…", ";", ":")
_SIGNAL_WEIGHT_ALIASES: dict[str, str] = {
    "reaction": "laughter",
    "scene": "scene_density",
}

YamnetFn = Callable[[np.ndarray, int], Sequence[float]]

__all__ = [
    "DEFAULT_MIN_SILENCE_S",
    "DEFAULT_SILENCE_THRESHOLD_DBFS",
    "DEFAULT_THOUGHT_MAX_DURATION_S",
    "DEFAULT_THOUGHT_MIN_PAUSE_S",
    "SignalAnalysisError",
    "YamnetFn",
    "aggregate_signals",
    "build_thought_units",
    "compute_speech_rate_1hz",
    "extract_audio_signals_1hz",
    "redistribute_signal_weights",
    "robust_normalize_series",
]


class SignalAnalysisError(RuntimeError):
    """Typed error raised when audio or signal analysis fails."""


def robust_normalize_series(
    values: Sequence[float] | np.ndarray,
    *,
    low_pct: float = 5.0,
    high_pct: float = 95.0,
) -> list[float]:
    """Normalize a numeric series into [0.0, 1.0] using 5/95 percentile bounds."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return []

    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    min_val = float(np.min(arr))
    max_val = float(np.max(arr))
    if max_val - min_val <= 1e-12:
        return [0.0] * int(arr.size)

    p_low = float(np.percentile(arr, low_pct))
    p_high = float(np.percentile(arr, high_pct))
    if p_high - p_low <= 1e-9:
        p_low, p_high = min_val, max_val

    denom = p_high - p_low
    if denom <= 1e-12:
        return [0.0] * int(arr.size)

    norm = np.clip((arr - p_low) / denom, 0.0, 1.0)
    return [round(float(x), 4) for x in norm]


def _read_wav_mono_float(wav_path: Path) -> tuple[np.ndarray, int]:
    path = Path(wav_path)
    if not path.exists() or not path.is_file():
        raise SignalAnalysisError(f"WAV file not found: {path}")

    try:
        with wave.open(str(path), "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            raw = wf.readframes(nframes)
    except (wave.Error, EOFError, OSError) as exc:
        raise SignalAnalysisError(f"Failed to read WAV {path.name}: {exc}") from exc

    if framerate <= 0 or nchannels <= 0:
        raise SignalAnalysisError(f"Invalid WAV metadata in {path.name}")

    if nframes == 0 or not raw:
        return np.empty((0,), dtype=np.float64), framerate

    if sampwidth == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif sampwidth == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    elif sampwidth == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483648.0
    else:
        raise SignalAnalysisError(f"Unsupported WAV sample width {sampwidth} bytes in {path.name}")

    if nchannels > 1:
        usable = (samples.size // nchannels) * nchannels
        samples = samples[:usable].reshape((-1, nchannels)).mean(axis=1)

    return samples, framerate


def _detect_silences(
    samples: np.ndarray,
    sr: int,
    *,
    silence_threshold_dbfs: float,
    min_silence_s: float,
) -> list[list[float]]:
    if samples.size == 0 or sr <= 0:
        return []

    win_len = max(1, int(round(0.05 * sr)))  # 50 ms frames
    n_wins = max(1, int(math.ceil(samples.size / win_len)))
    thresh_amp = 10.0 ** (float(silence_threshold_dbfs) / 20.0)

    silences: list[list[float]] = []
    sil_start: float | None = None

    for idx in range(n_wins):
        start_i = idx * win_len
        end_i = min(samples.size, start_i + win_len)
        seg = samples[start_i:end_i]
        rms = float(np.sqrt(np.mean(seg * seg))) if seg.size > 0 else 0.0
        is_silent = rms < thresh_amp

        t0 = start_i / float(sr)
        if is_silent:
            if sil_start is None:
                sil_start = t0
        else:
            if sil_start is not None:
                if t0 - sil_start >= min_silence_s:
                    silences.append([round(sil_start, 3), round(t0, 3)])
                sil_start = None

    if sil_start is not None:
        end_t = samples.size / float(sr)
        if end_t - sil_start >= min_silence_s:
            silences.append([round(sil_start, 3), round(end_t, 3)])

    return silences


def _align_series_length(values: Sequence[float], target_len: int) -> list[float]:
    if target_len <= 0:
        return []
    arr = [float(v) for v in values]
    if len(arr) == target_len:
        return arr
    if not arr:
        return [0.0] * target_len
    if len(arr) > target_len:
        return arr[:target_len]
    return arr + [arr[-1]] * (target_len - len(arr))


def extract_audio_signals_1hz(
    wav_path: Path,
    duration_s: float | None = None,
    *,
    silence_threshold_dbfs: float = DEFAULT_SILENCE_THRESHOLD_DBFS,
    min_silence_s: float = DEFAULT_MIN_SILENCE_S,
    yamnet_fn: YamnetFn | None = None,
) -> dict[str, Any]:
    """Extract 1 Hz energy, silences, laughter/excitement, and music heuristics from PCM WAV."""
    samples, sr = _read_wav_mono_float(wav_path)
    audio_dur = samples.size / float(sr) if sr > 0 else 0.0
    target_dur = float(duration_s) if duration_s is not None and duration_s > 0.0 else audio_dur

    if target_dur <= 0.0:
        return {
            "duration": 0.0,
            "energy": [],
            "silences": [],
            "laughter": [],
            "music": [],
        }

    n_bins = max(1, int(math.ceil(target_dur)))
    thresh_amp = 10.0 ** (float(silence_threshold_dbfs) / 20.0)

    rms_1hz = np.zeros(n_bins, dtype=np.float64)
    burstiness_1hz = np.zeros(n_bins, dtype=np.float64)
    music_raw_1hz = np.zeros(n_bins, dtype=np.float64)

    sub_len = max(1, int(round(0.05 * sr)))
    for sec in range(n_bins):
        i0 = sec * sr
        i1 = min(samples.size, (sec + 1) * sr)
        if i0 >= samples.size or i1 <= i0:
            continue
        chunk = samples[i0:i1]
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        rms_1hz[sec] = rms
        if rms < thresh_amp:
            continue

        # 50 ms sub-frame RMS envelope inside the 1s bin
        n_sub = max(1, chunk.size // sub_len)
        sub_rms = np.array(
            [
                float(np.sqrt(np.mean(chunk[k * sub_len : min(chunk.size, (k + 1) * sub_len)] ** 2)))
                for k in range(n_sub)
            ],
            dtype=np.float64,
        )
        mean_sub = float(np.mean(sub_rms))
        std_sub = float(np.std(sub_rms))
        cv = std_sub / max(mean_sub, 1e-9)
        burstiness_1hz[sec] = cv

        # Stationary tonal/wideband bed heuristic for intro/background music
        stationarity = max(0.0, 1.0 - min(1.0, cv))
        spectrum = np.abs(np.fft.rfft(chunk)) + 1e-12
        geom_mean = float(np.exp(np.mean(np.log(spectrum))))
        arith_mean = float(np.mean(spectrum))
        flatness = min(1.0, max(0.0, geom_mean / max(arith_mean, 1e-12)))
        music_raw_1hz[sec] = stationarity * (0.65 + 0.35 * (1.0 - abs(flatness - 0.5)))

    energy = robust_normalize_series(rms_1hz)

    if yamnet_fn is not None:
        raw_laughter = _align_series_length(yamnet_fn(samples, sr), n_bins)
        laughter = robust_normalize_series(raw_laughter)
    else:
        # Energy-spike heuristic: transient RMS excess above 5s rolling median baseline
        laughter_raw = np.zeros(n_bins, dtype=np.float64)
        for sec in range(n_bins):
            if rms_1hz[sec] < thresh_amp:
                continue
            lo = max(0, sec - 2)
            hi = min(n_bins, sec + 3)
            baseline = float(np.median(rms_1hz[lo:hi]))
            spike = max(0.0, float(rms_1hz[sec]) - baseline)
            laughter_raw[sec] = spike * (1.0 + min(2.0, float(burstiness_1hz[sec])))
        laughter = robust_normalize_series(laughter_raw)

    music = robust_normalize_series(music_raw_1hz)
    # Keep silent bins strictly 0.0 on music & laughter
    for sec in range(n_bins):
        if rms_1hz[sec] < thresh_amp:
            music[sec] = 0.0
            if yamnet_fn is None:
                laughter[sec] = 0.0

    silences = _detect_silences(
        samples,
        sr,
        silence_threshold_dbfs=silence_threshold_dbfs,
        min_silence_s=min_silence_s,
    )

    return {
        "duration": round(target_dur, 3),
        "energy": energy,
        "silences": silences,
        "laughter": laughter,
        "music": music,
    }


def _gap_overlaps_silence(
    gap_start: float,
    gap_end: float,
    silences: Sequence[Sequence[float]],
    min_pause_s: float,
) -> bool:
    lo = gap_start - 0.05
    hi = gap_end + 0.05
    for iv in silences:
        if len(iv) < 2:
            continue
        s0 = float(iv[0])
        s1 = float(iv[1])
        if s1 - s0 < min_pause_s:
            continue
        if max(lo, s0) <= min(hi, s1):
            return True
    return False


def build_thought_units(
    transcript: Mapping[str, Any],
    silences: Sequence[Sequence[float]] = (),
    *,
    max_duration_s: float = DEFAULT_THOUGHT_MAX_DURATION_S,
    min_pause_s: float = DEFAULT_THOUGHT_MIN_PAUSE_S,
) -> list[dict[str, Any]]:
    """Segment transcript words into thought units on punctuation, pauses, speaker shifts, and max duration."""
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return []

    flat_words: list[dict[str, Any]] = []
    for seg in segments:
        if not isinstance(seg, Mapping):
            continue
        speaker = seg.get("speaker")
        raw_words = seg.get("words")
        if not isinstance(raw_words, list):
            continue
        for w in raw_words:
            if not isinstance(w, Mapping):
                continue
            tok = unicodedata.normalize("NFC", str(w.get("w") or w.get("word") or "")).strip()
            if not tok:
                continue
            ws = w.get("s") if "s" in w else w.get("start")
            we = w.get("e") if "e" in w else w.get("end")
            if ws is None or we is None:
                continue
            s_val = max(0.0, float(ws))
            e_val = max(s_val, float(we))
            flat_words.append(
                {
                    "w": tok,
                    "s": s_val,
                    "e": e_val,
                    "speaker": str(speaker) if speaker is not None else None,
                }
            )

    if not flat_words:
        return []

    units: list[dict[str, Any]] = []
    unit_w0 = 0
    unit_start = flat_words[0]["s"]

    for idx in range(len(flat_words)):
        curr = flat_words[idx]
        is_last = idx == len(flat_words) - 1
        should_break = is_last

        if not is_last:
            nxt = flat_words[idx + 1]
            curr_tok = curr["w"]
            dur_so_far = curr["e"] - unit_start
            next_dur = nxt["e"] - unit_start
            gap = nxt["s"] - curr["e"]

            ends_sentence = curr_tok.endswith(_SENTENCE_END_PUNCT)
            ends_comma_long = curr_tok.endswith(",") and (
                dur_so_far >= max_duration_s * 0.5 or gap >= min_pause_s * 0.6
            )
            speaker_changed = (
                curr["speaker"] is not None
                and nxt["speaker"] is not None
                and curr["speaker"] != nxt["speaker"]
            )
            pause_break = gap >= min_pause_s or _gap_overlaps_silence(
                curr["e"],
                nxt["s"],
                silences,
                min_pause_s,
            )
            duration_break = next_dur > max_duration_s

            should_break = (
                ends_sentence
                or ends_comma_long
                or speaker_changed
                or pause_break
                or duration_break
            )

        if should_break:
            chunk = flat_words[unit_w0 : idx + 1]
            units.append(
                {
                    "id": len(units),
                    "start": round(chunk[0]["s"], 3),
                    "end": round(chunk[-1]["e"], 3),
                    "w0": unit_w0,
                    "w1": idx,
                    "text": " ".join(item["w"] for item in chunk),
                }
            )
            if not is_last:
                unit_w0 = idx + 1
                unit_start = flat_words[unit_w0]["s"]

    return units


def compute_speech_rate_1hz(
    transcript: Mapping[str, Any] | None,
    duration_s: float,
) -> list[float] | None:
    """Compute robust-normalized 1 Hz word-rate series from transcript, or None if unavailable."""
    if transcript is None:
        return None
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return None

    dur = max(0.0, float(duration_s))
    words_found: list[tuple[float, float]] = []
    for seg in segments:
        if not isinstance(seg, Mapping):
            continue
        raw_words = seg.get("words")
        if not isinstance(raw_words, list):
            continue
        for w in raw_words:
            if not isinstance(w, Mapping):
                continue
            ws = w.get("s") if "s" in w else w.get("start")
            we = w.get("e") if "e" in w else w.get("end")
            if ws is None or we is None:
                continue
            s_val = max(0.0, float(ws))
            e_val = max(s_val, float(we))
            words_found.append((s_val, e_val))
            dur = max(dur, e_val)

    if not words_found:
        return None

    n_bins = max(1, int(math.ceil(dur)))
    counts = np.zeros(n_bins, dtype=np.float64)
    for s_val, e_val in words_found:
        span = e_val - s_val
        if span <= 1e-6:
            bin_idx = min(n_bins - 1, max(0, int(math.floor(s_val))))
            counts[bin_idx] += 1.0
        else:
            b0 = min(n_bins - 1, max(0, int(math.floor(s_val))))
            b1 = min(n_bins - 1, max(b0, int(math.floor(max(s_val, e_val - 1e-9)))))
            for b in range(b0, b1 + 1):
                ov = min(e_val, float(b + 1)) - max(s_val, float(b))
                if ov > 0.0:
                    counts[b] += ov / span

    return robust_normalize_series(counts)


def _normalize_optional_series(
    raw_series: Sequence[float] | None,
    n_bins: int,
) -> list[float] | None:
    if raw_series is None:
        return None
    aligned = _align_series_length(raw_series, n_bins)
    return robust_normalize_series(aligned)


def aggregate_signals(
    *,
    duration_s: float | None = None,
    wav_path: Path | None = None,
    video_path: Path | None = None,
    transcript: Mapping[str, Any] | None = None,
    audio_signals: Mapping[str, Any] | None = None,
    scenes: list[dict[str, Any]] | None = None,
    face_presence: Sequence[float] | None = None,
    heatmap: Sequence[float] | None = None,
    audio_analyzer: Callable[[Path, float | None], dict[str, Any]] | None = None,
    scene_analyzer: Callable[[Path], list[dict[str, Any]]] | None = None,
    face_analyzer: Callable[[Path, float], Sequence[float] | None] | None = None,
    heatmap_analyzer: Callable[..., Sequence[float] | None] | None = None,
) -> dict[str, Any]:
    """Aggregate 1 Hz time-indexed signals, silences, and thought units into Spec v2 signals.json."""
    warnings: list[str] = []

    # 1. Resolve audio signals (never fabricate zeros if missing/failed)
    resolved_audio: Mapping[str, Any] | None = audio_signals
    if resolved_audio is None and (wav_path is not None or audio_analyzer is not None):
        fn = audio_analyzer or extract_audio_signals_1hz
        try:
            target_wav = Path(wav_path) if wav_path is not None else Path()
            resolved_audio = fn(target_wav, duration_s)
        except Exception as exc:
            warnings.append(f"Audio signal extraction failed ({exc}); energy/laughter/music set to null.")
            resolved_audio = None

    # 2. Resolve scenes if needed
    resolved_scenes: list[dict[str, Any]] | None = scenes
    if resolved_scenes is None and (video_path is not None or scene_analyzer is not None):
        s_fn = scene_analyzer or detect_scenes
        try:
            target_video = Path(video_path) if video_path is not None else Path()
            resolved_scenes = s_fn(target_video)
        except Exception as exc:
            warnings.append(f"Scene analysis failed ({exc}); scene_density set to null.")
            resolved_scenes = None

    # 3. Determine timeline duration & bin count
    inferred_dur = float(duration_s) if duration_s is not None and duration_s > 0.0 else 0.0
    if inferred_dur <= 0.0 and resolved_audio is not None:
        inferred_dur = max(inferred_dur, float(resolved_audio.get("duration", 0.0) or 0.0))
    if inferred_dur <= 0.0 and transcript is not None:
        inferred_dur = max(inferred_dur, float(transcript.get("duration", 0.0) or 0.0))
        for seg in transcript.get("segments") or []:
            if isinstance(seg, Mapping):
                inferred_dur = max(inferred_dur, float(seg.get("end", 0.0) or 0.0))
    if inferred_dur <= 0.0 and resolved_scenes:
        for sc in resolved_scenes:
            inferred_dur = max(inferred_dur, float(sc.get("end", 0.0) or 0.0))
    if inferred_dur <= 0.0 and resolved_audio is not None:
        eng_seq = resolved_audio.get("energy")
        if isinstance(eng_seq, Sequence):
            inferred_dur = float(len(eng_seq))

    n_bins = max(1, int(math.ceil(inferred_dur))) if inferred_dur > 0.0 else 0

    # 4. Populate series (null-safe: missing -> None, NEVER 0)
    silences: list[list[float]] = []
    energy_series: list[float] | None = None
    laughter_series: list[float] | None = None
    music_series: list[float] | None = None

    if resolved_audio is not None:
        raw_sil = resolved_audio.get("silences")
        if isinstance(raw_sil, list):
            silences = [
                [round(float(iv[0]), 3), round(float(iv[1]), 3)]
                for iv in raw_sil
                if isinstance(iv, (list, tuple)) and len(iv) >= 2
            ]
        if n_bins > 0:
            raw_e = resolved_audio.get("energy")
            raw_l = resolved_audio.get("laughter")
            raw_m = resolved_audio.get("music")
            if isinstance(raw_e, Sequence) and not isinstance(raw_e, (str, bytes)):
                energy_series = _normalize_optional_series(raw_e, n_bins)
            if isinstance(raw_l, Sequence) and not isinstance(raw_l, (str, bytes)):
                laughter_series = _normalize_optional_series(raw_l, n_bins)
            if isinstance(raw_m, Sequence) and not isinstance(raw_m, (str, bytes)):
                music_series = _normalize_optional_series(raw_m, n_bins)

    speech_rate_series = compute_speech_rate_1hz(transcript, inferred_dur) if n_bins > 0 else None
    if transcript is None:
        warnings.append("Transcript missing; speech_rate set to null and thought_units empty.")
    elif speech_rate_series is None and n_bins > 0:
        warnings.append("Transcript contains no timed words; speech_rate set to null.")

    if speech_rate_series is not None and n_bins > 0:
        speech_rate_series = _align_series_length(speech_rate_series, n_bins)

    scene_density_series: list[float] | None = None
    if resolved_scenes is not None and n_bins > 0:
        scene_density_series = _align_series_length(
            scene_density_1hz(resolved_scenes, inferred_dur),
            n_bins,
        )

    resolved_face: Sequence[float] | None = face_presence
    if resolved_face is None and face_analyzer is not None and video_path is not None:
        try:
            resolved_face = face_analyzer(Path(video_path), inferred_dur)
        except Exception as exc:
            warnings.append(f"Face analyzer failed ({exc}); face_presence set to null.")
            resolved_face = None
    face_series = _normalize_optional_series(resolved_face, n_bins) if n_bins > 0 else None

    resolved_heatmap: Sequence[float] | None = heatmap
    if resolved_heatmap is None and heatmap_analyzer is not None:
        try:
            resolved_heatmap = heatmap_analyzer()
        except Exception as exc:
            warnings.append(f"Heatmap analyzer failed ({exc}); heatmap set to null.")
            resolved_heatmap = None
    heatmap_series = _normalize_optional_series(resolved_heatmap, n_bins) if n_bins > 0 else None

    thought_units = build_thought_units(transcript, silences) if transcript is not None else []

    return {
        "schema_version": 1,
        "hz": 1,
        "duration": round(max(0.0, inferred_dur), 3),
        "series": {
            "energy": energy_series,
            "speech_rate": speech_rate_series,
            "laughter": laughter_series,
            "scene_density": scene_density_series,
            "face_presence": face_series,
            "heatmap": heatmap_series,
            "music": music_series,
        },
        "silences": silences,
        "thought_units": thought_units,
        "warnings": warnings,
    }


def _is_signal_available(key: str, available_signals: Mapping[str, Any]) -> bool:
    probe_keys = [key]
    if key in _SIGNAL_WEIGHT_ALIASES:
        probe_keys.append(_SIGNAL_WEIGHT_ALIASES[key])

    for k in probe_keys:
        if k in available_signals:
            val = available_signals[k]
            if val is None or val is False:
                return False
            if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
                return len(val) > 0
            return True
    return False


def redistribute_signal_weights(
    weights: Mapping[str, float],
    available_signals: Mapping[str, Any],
    *,
    fallback_key: str | None = None,
) -> dict[str, float]:
    """Re-normalize feature weights when some signals are None/unavailable so available weights sum to 1.0."""
    if not weights:
        return {}

    active: dict[str, float] = {}
    missing_weight = 0.0
    for key, raw_w in weights.items():
        w = max(0.0, float(raw_w))
        if _is_signal_available(key, available_signals):
            active[key] = w
        else:
            active[key] = 0.0
            missing_weight += w

    if fallback_key and fallback_key in active and _is_signal_available(fallback_key, available_signals):
        active[fallback_key] += missing_weight

    total_active = sum(active.values())
    if total_active <= 0.0:
        return {k: 0.0 for k in weights}

    normalized = {k: round(v / total_active, 6) for k, v in active.items()}
    # Adjust largest active weight by floating-point residual so exact sum == 1.0
    residual = 1.0 - sum(normalized.values())
    if abs(residual) > 0.0:
        max_k = max(normalized, key=lambda k: normalized[k])
        normalized[max_k] = round(normalized[max_k] + residual, 6)
    return normalized
