from __future__ import annotations

from backend.pipeline.analysis.scenes import (
    SceneDetectionError,
    build_scenes_document,
    detect_scenes,
    probe_video_stream,
    scene_density_1hz,
)
from backend.pipeline.analysis.signals import (
    SignalAnalysisError,
    aggregate_signals,
    build_thought_units,
    compute_speech_rate_1hz,
    extract_audio_signals_1hz,
    redistribute_signal_weights,
    robust_normalize_series,
)

__all__ = [
    "SceneDetectionError",
    "SignalAnalysisError",
    "aggregate_signals",
    "build_scenes_document",
    "build_thought_units",
    "compute_speech_rate_1hz",
    "detect_scenes",
    "extract_audio_signals_1hz",
    "probe_video_stream",
    "redistribute_signal_weights",
    "robust_normalize_series",
    "scene_density_1hz",
]
