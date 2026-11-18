from __future__ import annotations

from backend.pipeline.analysis import faces, profile, scenes, signals, topics, visual
from backend.pipeline.analysis.faces import (
    analyze_faces,
    choose_sampling_fps,
    face_presence_ratio,
)
from backend.pipeline.analysis.profile import (
    VALID_ENERGY_PROFILES,
    VALID_GENRES,
    VALID_LAYOUT_HINTS,
    VALID_VISUAL_STYLES,
    build_video_profile,
    load_highlights_config,
    select_genre_weights,
)
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
from backend.pipeline.analysis.topics import (
    segment_topics,
    topic_for_time,
)
from backend.pipeline.analysis.visual import (
    analyze_visual_windows,
    compute_frame_hash,
    is_visual_analysis_enabled,
)

__all__ = [
    "SceneDetectionError",
    "SignalAnalysisError",
    "VALID_ENERGY_PROFILES",
    "VALID_GENRES",
    "VALID_LAYOUT_HINTS",
    "VALID_VISUAL_STYLES",
    "aggregate_signals",
    "analyze_faces",
    "analyze_visual_windows",
    "build_scenes_document",
    "build_thought_units",
    "build_video_profile",
    "choose_sampling_fps",
    "compute_frame_hash",
    "compute_speech_rate_1hz",
    "detect_scenes",
    "extract_audio_signals_1hz",
    "face_presence_ratio",
    "faces",
    "is_visual_analysis_enabled",
    "load_highlights_config",
    "probe_video_stream",
    "profile",
    "redistribute_signal_weights",
    "robust_normalize_series",
    "scene_density_1hz",
    "scenes",
    "segment_topics",
    "select_genre_weights",
    "signals",
    "topic_for_time",
    "topics",
    "visual",
]
