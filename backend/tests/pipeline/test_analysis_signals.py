from __future__ import annotations

import json
import math
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import numpy as np
import pytest

from backend.pipeline.analysis import (
    aggregate_signals,
    build_scenes_document,
    build_thought_units,
    compute_speech_rate_1hz,
    detect_scenes,
    extract_audio_signals_1hz,
    redistribute_signal_weights,
    robust_normalize_series,
    scene_density_1hz,
)

SCENES_DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "scenes", "warnings"],
    "properties": {
        "schema_version": {"const": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "scenes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["start", "end", "type"],
                "properties": {
                    "start": {"type": "number", "minimum": 0},
                    "end": {"type": "number", "minimum": 0},
                    "type": {"enum": ["cut", "fade"]},
                },
            },
        },
    },
}

SIGNALS_DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "hz", "duration", "series", "silences", "thought_units", "warnings"],
    "properties": {
        "schema_version": {"const": 1},
        "hz": {"const": 1},
        "duration": {"type": "number", "minimum": 0},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "silences": {
            "type": "array",
            "items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "number", "minimum": 0},
            },
        },
        "thought_units": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "start", "end", "w0", "w1", "text"],
                "properties": {
                    "id": {"type": "integer", "minimum": 0},
                    "start": {"type": "number", "minimum": 0},
                    "end": {"type": "number", "minimum": 0},
                    "w0": {"type": "integer", "minimum": 0},
                    "w1": {"type": "integer", "minimum": 0},
                    "text": {"type": "string"},
                },
            },
        },
        "series": {
            "type": "object",
            "required": [
                "energy",
                "speech_rate",
                "laughter",
                "scene_density",
                "face_presence",
                "heatmap",
                "music",
            ],
            "properties": {
                key: {
                    "type": ["array", "null"],
                    "items": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                }
                for key in (
                    "energy",
                    "speech_rate",
                    "laughter",
                    "scene_density",
                    "face_presence",
                    "heatmap",
                    "music",
                )
            },
        },
    },
}


def _make_scripted_cuts_mp4(out_path: Path) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=160x90:d=2:r=24",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=160x90:d=2:r=24",
        "-f",
        "lavfi",
        "-i",
        "color=c=green:s=160x90:d=2:r=24",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, timeout=30)
    return out_path


def _write_tone_burst_wav(path: Path, sample_rate: int = 16000) -> Path:
    # 10s total:
    # [0, 2): moderate 440Hz tone (amp=0.25)
    # [2, 4): silence
    # [4, 6): loud bursty 660Hz + 880Hz excitement burst (amp=0.80 with bursts)
    # [6, 8): silence
    # [8, 10): quiet steady 330Hz tone (amp=0.15)
    total_samples = 10 * sample_rate
    t = np.arange(total_samples, dtype=np.float64) / float(sample_rate)
    signal = np.zeros(total_samples, dtype=np.float64)

    seg0 = slice(0 * sample_rate, 2 * sample_rate)
    signal[seg0] = 0.25 * np.sin(2.0 * math.pi * 440.0 * t[seg0])

    seg2 = slice(4 * sample_rate, 6 * sample_rate)
    mod = 0.35 + 0.65 * (0.5 * (1.0 + np.sin(2.0 * math.pi * 6.0 * t[seg2])))
    signal[seg2] = 0.80 * mod * np.sin(2.0 * math.pi * 660.0 * t[seg2])

    seg4 = slice(8 * sample_rate, 10 * sample_rate)
    signal[seg4] = 0.15 * np.sin(2.0 * math.pi * 330.0 * t[seg4])

    pcm = np.clip(np.round(signal * 32767.0), -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return path


def test_detect_scenes_on_synthetic_ffmpeg_video_and_degenerate_inputs(tmp_path: Path) -> None:
    video_path = _make_scripted_cuts_mp4(tmp_path / "scripted_cuts.mp4")

    scenes = detect_scenes(video_path, threshold=27.0, min_scene_len_s=0.8)
    assert len(scenes) == 3
    assert scenes[0]["start"] == pytest.approx(0.0, abs=0.05)
    assert scenes[0]["end"] == pytest.approx(2.0, abs=0.25)
    assert scenes[1]["start"] == pytest.approx(2.0, abs=0.25)
    assert scenes[1]["end"] == pytest.approx(4.0, abs=0.25)
    assert scenes[2]["start"] == pytest.approx(4.0, abs=0.25)
    assert scenes[2]["end"] == pytest.approx(6.0, abs=0.15)
    for sc in scenes:
        assert sc["type"] in ("cut", "fade")

    doc = build_scenes_document(video_path)
    jsonschema.validate(instance=doc, schema=SCENES_DOC_SCHEMA)
    assert doc["warnings"] == []

    density = scene_density_1hz(scenes, duration_s=6.0)
    assert len(density) == 6
    assert density[2] > 0.0
    assert density[4] > 0.0
    assert density[0] == 0.0

    # Missing / degenerate video never fails the job
    missing_doc = build_scenes_document(tmp_path / "nonexistent.mp4")
    jsonschema.validate(instance=missing_doc, schema=SCENES_DOC_SCHEMA)
    assert len(missing_doc["scenes"]) == 1
    assert len(missing_doc["warnings"]) == 1

    # Very short 0.5s video returns single full-duration scene without cuts
    short_mp4 = tmp_path / "short.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:d=0.5:r=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(short_mp4),
        ],
        check=True,
        timeout=20,
    )
    short_scenes = detect_scenes(short_mp4, min_scene_len_s=0.8)
    assert len(short_scenes) == 1
    assert short_scenes[0]["start"] == 0.0
    assert short_scenes[0]["end"] == pytest.approx(0.5, abs=0.1)


def test_detect_scenes_optional_scenedetect_hook_and_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = _make_scripted_cuts_mp4(tmp_path / "sd_cuts.mp4")

    class FakeTC:
        def __init__(self, sec: float) -> None:
            self._sec = sec

        def get_seconds(self) -> float:
            return self._sec

    class FakeManager:
        def add_detector(self, _det: Any) -> None:
            pass

        def detect_scenes(self, _video: Any) -> None:
            pass

        def get_scene_list(self) -> list[tuple[FakeTC, FakeTC]]:
            return [
                (FakeTC(0.0), FakeTC(2.0)),
                (FakeTC(2.0), FakeTC(4.0)),
                (FakeTC(4.0), FakeTC(6.0)),
            ]

    fake_sd = SimpleNamespace(
        open_video=lambda p: p,
        SceneManager=FakeManager,
    )
    fake_det = SimpleNamespace(ContentDetector=lambda **_kwargs: object())
    monkeypatch.setitem(sys.modules, "scenedetect", fake_sd)
    monkeypatch.setitem(sys.modules, "scenedetect.detectors", fake_det)

    scenes = detect_scenes(video_path)
    assert scenes == [
        {"start": 0.0, "end": 2.0, "type": "cut"},
        {"start": 2.0, "end": 4.0, "type": "cut"},
        {"start": 4.0, "end": 6.0, "type": "cut"},
    ]

    # If scenedetect raises at runtime, cleanly fall back to frame_diff
    def raising_open(_p: str) -> Any:
        raise RuntimeError("scenedetect backend error")

    monkeypatch.setitem(
        sys.modules,
        "scenedetect",
        SimpleNamespace(open_video=raising_open, SceneManager=FakeManager),
    )
    warnings: list[str] = []
    fallback_scenes = detect_scenes(video_path, warnings_out=warnings)
    assert len(fallback_scenes) == 3
    assert any("PySceneDetect failed" in w for w in warnings)


def test_extract_audio_signals_1hz_silences_and_yamnet_hook(tmp_path: Path) -> None:
    wav_path = _write_tone_burst_wav(tmp_path / "bursts.wav")
    sig = extract_audio_signals_1hz(wav_path)

    assert sig["duration"] == pytest.approx(10.0)
    assert len(sig["energy"]) == 10
    assert len(sig["laughter"]) == 10
    assert len(sig["music"]) == 10

    # Silent seconds [2,3] and [6,7] have zero energy & music, and loud [4,5] peaks
    assert sig["energy"][2] == 0.0
    assert sig["energy"][3] == 0.0
    assert sig["energy"][6] == 0.0
    assert sig["energy"][7] == 0.0
    assert sig["energy"][4] > sig["energy"][0]
    assert sig["energy"][5] > sig["energy"][8]
    assert sig["music"][2] == 0.0
    assert sig["music"][6] == 0.0
    assert sig["laughter"][4] > 0.0 or sig["laughter"][5] > 0.0

    # Silences detected near [2.0, 4.0] and [6.0, 8.0]
    assert len(sig["silences"]) == 2
    assert sig["silences"][0][0] == pytest.approx(2.0, abs=0.1)
    assert sig["silences"][0][1] == pytest.approx(4.0, abs=0.1)
    assert sig["silences"][1][0] == pytest.approx(6.0, abs=0.1)
    assert sig["silences"][1][1] == pytest.approx(8.0, abs=0.1)

    # Optional YAMNet hook overrides laughter heuristic cleanly
    yamnet_sig = extract_audio_signals_1hz(
        wav_path,
        yamnet_fn=lambda _samples, _sr: [0.0, 0.1, 0.0, 0.0, 0.9, 1.0, 0.0, 0.0, 0.2, 0.1],
    )
    assert yamnet_sig["laughter"][5] == 1.0
    assert yamnet_sig["laughter"][0] == 0.0


def test_build_thought_units_and_speech_rate_1hz() -> None:
    transcript = {
        "schema_version": 1,
        "language": "en",
        "script": "latn",
        "duration": 10.0,
        "engine": "faster_whisper",
        "segments": [
            {
                "id": 0,
                "start": 0.2,
                "end": 5.5,
                "text": "First sentence ends here. Second clause pauses",
                "speaker": "A",
                "words": [
                    {"w": "First", "s": 0.2, "e": 0.6, "p": 0.95},
                    {"w": "sentence", "s": 0.65, "e": 1.1, "p": 0.95},
                    {"w": "ends", "s": 1.15, "e": 1.5, "p": 0.95},
                    {"w": "here.", "s": 1.55, "e": 1.9, "p": 0.95},  # punctuation break
                    {"w": "Second", "s": 2.0, "e": 2.5, "p": 0.95},
                    {"w": "clause", "s": 2.55, "e": 3.0, "p": 0.95},
                    {"w": "pauses", "s": 4.2, "e": 4.8, "p": 0.95},  # silence break [3.1, 4.1]
                ],
            },
            {
                "id": 1,
                "start": 5.0,
                "end": 9.0,
                "text": "Speaker B responds quickly",
                "speaker": "B",
                "words": [
                    {"w": "Speaker", "s": 5.0, "e": 5.5, "p": 0.9},  # speaker change break
                    {"w": "B", "s": 5.55, "e": 5.8, "p": 0.9},
                    {"w": "responds", "s": 5.85, "e": 6.4, "p": 0.9},
                    {"w": "quickly", "s": 6.45, "e": 7.0, "p": 0.9},
                ],
            },
        ],
    }
    silences = [[3.1, 4.1]]
    units = build_thought_units(transcript, silences, max_duration_s=18.0, min_pause_s=0.5)

    assert len(units) == 4
    assert units[0] == {
        "id": 0,
        "start": 0.2,
        "end": 1.9,
        "w0": 0,
        "w1": 3,
        "text": "First sentence ends here.",
    }
    assert units[1]["w0"] == 4 and units[1]["w1"] == 5
    assert units[2]["w0"] == 6 and units[2]["w1"] == 6
    assert units[3]["w0"] == 7 and units[3]["w1"] == 10

    speech_rate = compute_speech_rate_1hz(transcript, duration_s=10.0)
    assert speech_rate is not None
    assert len(speech_rate) == 10
    assert speech_rate[3] == 0.0  # silent second [3, 4)
    assert speech_rate[5] > 0.0


def test_aggregate_signals_null_safety_schema_and_graceful_downgrade(tmp_path: Path) -> None:
    wav_path = _write_tone_burst_wav(tmp_path / "audio.wav")
    video_path = _make_scripted_cuts_mp4(tmp_path / "video.mp4")

    transcript = {
        "schema_version": 1,
        "language": "en",
        "script": "latn",
        "duration": 10.0,
        "engine": "faster_whisper",
        "segments": [
            {
                "id": 0,
                "start": 0.2,
                "end": 1.8,
                "text": "Fast hook here.",
                "words": [
                    {"w": "Fast", "s": 0.2, "e": 0.7, "p": 0.95},
                    {"w": "hook", "s": 0.75, "e": 1.2, "p": 0.95},
                    {"w": "here.", "s": 1.25, "e": 1.8, "p": 0.95},
                ],
            }
        ],
    }

    # Missing heatmap & face_presence MUST be None (null in JSON), NEVER zero arrays
    signals = aggregate_signals(
        duration_s=10.0,
        wav_path=wav_path,
        video_path=video_path,
        transcript=transcript,
    )
    jsonschema.validate(instance=signals, schema=SIGNALS_DOC_SCHEMA)
    assert signals["series"]["heatmap"] is None
    assert signals["series"]["face_presence"] is None
    assert signals["series"]["energy"] is not None
    assert signals["series"]["speech_rate"] is not None
    assert signals["series"]["scene_density"] is not None
    assert len(signals["series"]["energy"]) == 10
    assert len(signals["thought_units"]) == 1

    serialized = json.dumps(signals)
    parsed = json.loads(serialized)
    assert parsed["series"]["heatmap"] is None
    assert parsed["series"]["face_presence"] is None

    # Graceful downgrade when analyzers raise cleanly
    def failing_audio(_wav: Path, _dur: float | None) -> dict[str, Any]:
        raise RuntimeError("corrupt wav header")

    def failing_scenes(_vid: Path) -> list[dict[str, Any]]:
        raise RuntimeError("decoder crash")

    def failing_faces(_vid: Path, _dur: float) -> list[float]:
        raise RuntimeError("mediapipe unavailable")

    def failing_heatmap() -> list[float]:
        raise RuntimeError("heatmap missing")

    degraded = aggregate_signals(
        duration_s=10.0,
        wav_path=wav_path,
        video_path=video_path,
        transcript=transcript,
        audio_analyzer=failing_audio,
        scene_analyzer=failing_scenes,
        face_analyzer=failing_faces,
        heatmap_analyzer=failing_heatmap,
    )
    jsonschema.validate(instance=degraded, schema=SIGNALS_DOC_SCHEMA)
    assert degraded["series"]["energy"] is None
    assert degraded["series"]["laughter"] is None
    assert degraded["series"]["music"] is None
    assert degraded["series"]["scene_density"] is None
    assert degraded["series"]["face_presence"] is None
    assert degraded["series"]["heatmap"] is None
    assert degraded["series"]["speech_rate"] is not None
    assert len(degraded["warnings"]) == 4


def test_robust_normalize_and_redistribute_signal_weights() -> None:
    # Percentile normalization clips outliers cleanly and handles constant arrays
    norm = robust_normalize_series([0.0, 1.0, 2.0, 3.0, 100.0])
    assert len(norm) == 5
    assert norm[0] == 0.0
    assert norm[-1] == 1.0
    assert robust_normalize_series([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]
    assert robust_normalize_series([]) == []

    # Proportional re-normalization when heatmap & face_presence are None
    weights = {
        "energy": 0.40,
        "speech_rate": 0.30,
        "heatmap": 0.20,
        "face_presence": 0.10,
    }
    available = {
        "energy": [0.2, 0.8],
        "speech_rate": [0.5, 0.6],
        "heatmap": None,
        "face_presence": None,
    }
    redistributed = redistribute_signal_weights(weights, available)
    assert redistributed["heatmap"] == 0.0
    assert redistributed["face_presence"] == 0.0
    assert redistributed["energy"] == pytest.approx(0.4 / 0.7, abs=1e-5)
    assert redistributed["speech_rate"] == pytest.approx(0.3 / 0.7, abs=1e-5)
    assert sum(redistributed.values()) == pytest.approx(1.0, abs=1e-6)

    # Spec v2 highlights.yaml style fallback_key="llm" redistribution + aliases (reaction->laughter, scene->scene_density)
    spec_weights = {
        "llm": 0.55,
        "energy": 0.10,
        "speech_rate": 0.07,
        "reaction": 0.08,
        "scene": 0.05,
        "heatmap": 0.10,
        "asr_conf": 0.05,
    }
    spec_avail = {
        "llm": True,
        "energy": [0.5],
        "speech_rate": [0.5],
        "laughter": [0.1],
        "scene_density": [0.0],
        "heatmap": None,
        "asr_conf": True,
    }
    llm_redist = redistribute_signal_weights(spec_weights, spec_avail, fallback_key="llm")
    assert llm_redist["heatmap"] == 0.0
    assert llm_redist["llm"] == pytest.approx(0.65, abs=1e-6)
    assert sum(llm_redist.values()) == pytest.approx(1.0, abs=1e-6)
