from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pytest

from backend.pipeline.analysis import (
    VALID_ENERGY_PROFILES,
    VALID_GENRES,
    VALID_LAYOUT_HINTS,
    VALID_VISUAL_STYLES,
    analyze_visual_windows,
    build_video_profile,
    compute_frame_hash,
    is_visual_analysis_enabled,
    select_genre_weights,
)
from backend.pipeline.providers.base import LLMResult, LLMUnavailableError
from backend.pipeline.reframe.tracker import SampledFrame

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "assets" / "samples"

VIDEO_PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "genre",
        "genre_conf",
        "speakers_estimated",
        "layout_hint",
        "visual_style",
        "has_screenshare",
        "language",
        "topics_overview",
        "energy_profile",
        "weights",
        "window_bounds",
        "visual_recommended",
        "warnings",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "genre": {"enum": list(VALID_GENRES)},
        "genre_conf": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "speakers_estimated": {"type": "integer", "minimum": 1},
        "layout_hint": {"enum": list(VALID_LAYOUT_HINTS)},
        "visual_style": {"enum": list(VALID_VISUAL_STYLES)},
        "has_screenshare": {"type": "boolean"},
        "language": {"enum": ["en", "hi", "hinglish", "bn"]},
        "topics_overview": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "energy_profile": {"enum": list(VALID_ENERGY_PROFILES)},
        "weights": {
            "type": "object",
            "required": ["llm", "energy", "speech_rate", "reaction", "scene", "heatmap", "asr_conf"],
        },
        "window_bounds": {
            "type": "object",
            "required": ["min_len_s", "max_len_s", "target_len_s"],
        },
        "visual_recommended": {"type": "boolean"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

VISUAL_DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "enabled",
        "frames_analyzed",
        "cache_hits",
        "budget_max_frames",
        "frames",
        "warnings",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "enabled": {"type": "boolean"},
        "frames_analyzed": {"type": "integer", "minimum": 0},
        "cache_hits": {"type": "integer", "minimum": 0},
        "budget_max_frames": {"type": "integer", "minimum": 0},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "t",
                    "frame_hash",
                    "description",
                    "on_screen_text",
                    "visual_interest",
                    "is_screenshare",
                ],
                "properties": {
                    "t": {"type": "number", "minimum": 0.0},
                    "frame_hash": {"type": "string", "minLength": 64, "maxLength": 64},
                    "description": {"type": "string", "minLength": 1},
                    "on_screen_text": {"type": "string"},
                    "visual_interest": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "is_screenshare": {"type": "boolean"},
                },
            },
        },
    },
}


class _MockLLM:
    provider_name = "mock"
    model = "mock-v1"

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._payload = payload or {}
        self._raise_exc = raise_exc
        self.calls = 0

    def generate_json(
        self,
        prompt: str,
        data_text: str,
        schema: dict[str, Any],
        temperature: float = 0.2,
    ) -> LLMResult:
        self.calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        return LLMResult(
            data=self._payload,
            provider=self.provider_name,
            model=self.model,
            raw_text=json.dumps(self._payload),
            repaired=False,
            attempts=1,
            usage={"prompt_tokens": 40, "completion_tokens": 30},
        )


@pytest.mark.parametrize(
    ("sample_file", "expected_lang", "expected_genre"),
    [
        ("sample_english_keynote.transcript.json", "en", "solo_talk"),
        ("sample_hindi_motivation.transcript.json", "hi", "solo_talk"),
        ("sample_hinglish_podcast.transcript.json", "hinglish", "podcast"),
        ("sample_bengali_tech.transcript.json", "bn", "solo_talk"),
    ],
)
def test_build_video_profile_on_four_sample_transcripts(
    sample_file: str,
    expected_lang: str,
    expected_genre: str,
) -> None:
    transcript = json.loads((SAMPLES_DIR / sample_file).read_text(encoding="utf-8"))
    profile_doc = build_video_profile(transcript)
    jsonschema.validate(instance=profile_doc, schema=VIDEO_PROFILE_SCHEMA)

    assert profile_doc["language"] == expected_lang
    assert profile_doc["genre"] == expected_genre
    assert profile_doc["topics_overview"]
    assert abs(sum(profile_doc["weights"].values()) - 1.0) < 1e-6


def test_build_video_profile_synthetic_gaming_screenshare_podcast() -> None:
    # 1. Gaming profile
    gaming_transcript = {
        "title": "Valorant Radiant Ranked Clutch & Headshot Highlights",
        "language": "en",
        "duration": 240.0,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 15.0,
                "text": "Insane clutch in overtime lobby with a final headshot during the boss fight!",
                "energy": 0.92,
            }
        ],
    }
    gaming_scenes = {
        "scenes": [
            {"start": float(i * 3), "end": float((i + 1) * 3), "type": "cut"}
            for i in range(40)
        ]
    }
    gaming_profile = build_video_profile(gaming_transcript, scenes=gaming_scenes)
    jsonschema.validate(instance=gaming_profile, schema=VIDEO_PROFILE_SCHEMA)
    assert gaming_profile["genre"] == "gaming"
    assert gaming_profile["visual_style"] == "gameplay"
    assert gaming_profile["layout_hint"] == "gameplay"
    assert gaming_profile["visual_recommended"] is True
    assert gaming_profile["window_bounds"]["min_len_s"] == 15.0
    assert gaming_profile["window_bounds"]["max_len_s"] == 45.0
    assert gaming_profile["window_bounds"]["target_len_s"] == 28.0

    # 2. Screenshare tutorial profile
    tutorial_transcript = {
        "title": "Step by Step Docker & VS Code API Endpoint Tutorial",
        "language": "en",
        "duration": 360.0,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 20.0,
                "text": "Let me share my screen and open the terminal in VS Code to test the localhost API endpoint.",
                "energy": 0.45,
            }
        ],
    }
    tutorial_faces = {
        "status": "ok",
        "summary": {
            "face_presence_ratio": 0.12,
            "mean_face_count": 0.12,
            "max_face_count": 1,
            "speakers_estimated": 1,
        },
    }
    tutorial_profile = build_video_profile(tutorial_transcript, faces=tutorial_faces)
    jsonschema.validate(instance=tutorial_profile, schema=VIDEO_PROFILE_SCHEMA)
    assert tutorial_profile["genre"] == "screenshare_tutorial"
    assert tutorial_profile["has_screenshare"] is True
    assert tutorial_profile["layout_hint"] == "screenshare"
    assert tutorial_profile["visual_style"] == "screenshare"
    assert tutorial_profile["visual_recommended"] is True
    assert tutorial_profile["weights"]["scene"] == 0.13

    # 3. Multi-speaker podcast profile
    podcast_transcript = {
        "title": "Founder Episode 42",
        "language": "en",
        "duration": 600.0,
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 20.0,
                "text": "Welcome back to the podcast! Today on the show our co-host asks about product growth.",
                "energy": 0.52,
            }
        ],
    }
    podcast_faces = {
        "status": "ok",
        "summary": {
            "face_presence_ratio": 0.92,
            "mean_face_count": 1.85,
            "max_face_count": 2,
            "speakers_estimated": 2,
        },
    }
    podcast_profile = build_video_profile(podcast_transcript, faces=podcast_faces)
    jsonschema.validate(instance=podcast_profile, schema=VIDEO_PROFILE_SCHEMA)
    assert podcast_profile["genre"] == "podcast"
    assert podcast_profile["speakers_estimated"] == 2
    assert podcast_profile["layout_hint"] == "two_shot"
    assert podcast_profile["visual_style"] == "talking_head"
    assert podcast_profile["visual_recommended"] is False
    assert podcast_profile["weights"]["llm"] == 0.65


def test_build_video_profile_owner_override_and_select_genre_weights() -> None:
    transcript = json.loads(
        (SAMPLES_DIR / "sample_english_keynote.transcript.json").read_text(encoding="utf-8")
    )
    # Even if an LLM suggests solo_talk, explicit genre_override MUST win
    mock_llm = _MockLLM(
        {
            "genre": "solo_talk",
            "genre_conf": 0.95,
            "layout_hint": "single_speaker",
            "visual_style": "talking_head",
            "has_screenshare": False,
        }
    )
    overridden = build_video_profile(
        transcript,
        genre_override="lecture",
        llm_provider=mock_llm,
    )
    jsonschema.validate(instance=overridden, schema=VIDEO_PROFILE_SCHEMA)
    assert overridden["genre"] == "lecture"
    assert overridden["genre_conf"] == 1.0
    assert overridden["source"] == "override"
    assert mock_llm.calls == 0
    assert overridden["weights"]["llm"] == 0.70
    assert overridden["window_bounds"]["min_len_s"] == 40.0
    assert overridden["window_bounds"]["max_len_s"] == 90.0
    assert overridden["window_bounds"]["target_len_s"] == 55.0

    # Verify select_genre_weights for unknown/other falls back to default weights
    other_cfg = select_genre_weights("other")
    assert other_cfg["weights"]["llm"] == 0.55
    assert other_cfg["window_bounds"]["min_len_s"] == 20.0
    assert other_cfg["window_bounds"]["max_len_s"] == 60.0
    assert other_cfg["window_bounds"]["target_len_s"] == 35.0
    assert other_cfg["visual_recommended"] is False


def test_analyze_visual_windows_off_by_default_and_genre_activation() -> None:
    assert is_visual_analysis_enabled() is False
    assert is_visual_analysis_enabled(genre="podcast") is False
    assert is_visual_analysis_enabled(genre="solo_talk") is False
    assert is_visual_analysis_enabled(genre="gaming") is True
    assert is_visual_analysis_enabled(genre="screenshare_tutorial") is True
    assert is_visual_analysis_enabled(genre="vlog") is True
    assert is_visual_analysis_enabled(genre="podcast", enabled=True) is True
    assert is_visual_analysis_enabled(genre="gaming", enabled=False) is False

    off_doc = analyze_visual_windows(
        candidate_windows=[(0.0, 10.0)],
        genre="podcast",
    )
    jsonschema.validate(instance=off_doc, schema=VISUAL_DOC_SCHEMA)
    assert off_doc["enabled"] is False
    assert off_doc["frames_analyzed"] == 0
    assert off_doc["frames"] == []


def test_analyze_visual_windows_budget_and_sha256_cache_hits(tmp_path: Path) -> None:
    # Create 10 frames spaced by 2.5s across candidate windows:
    # First 4 frames have identical image bytes (static slide/screenshare) -> 1 miss + 3 cache hits!
    static_slide = np.full((36, 64, 3), 240, dtype=np.uint8)
    static_slide[::4, :, :] = 20  # high contrast horizontal lines (IDE/slide look)

    unique_frames: list[SampledFrame] = []
    for i in range(10):
        t = round(i * 2.5, 3)
        if i < 4:
            img = static_slide.copy()
        else:
            img = np.full((36, 64, 3), i * 20, dtype=np.uint8)
        unique_frames.append(SampledFrame(timestamp=t, image=img))

    assert compute_frame_hash(unique_frames[0].image) == compute_frame_hash(unique_frames[3].image)
    assert compute_frame_hash(unique_frames[0].image) != compute_frame_hash(unique_frames[4].image)

    mock_llm = _MockLLM(
        {
            "description": "VS Code editor showing Python FastAPI endpoint",
            "on_screen_text": "def analyze_visual_windows():",
            "visual_interest": 0.84,
            "is_screenshare": True,
        }
    )
    shared_cache: dict[str, dict[str, Any]] = {}
    cache_file = tmp_path / "visual_cache.json"

    # Cap budget at 6 frames out of the 10 available in window [0.0, 25.0]
    doc = analyze_visual_windows(
        candidate_windows=[{"start": 0.0, "end": 25.0}],
        genre="screenshare_tutorial",
        max_frames_per_job=6,
        frame_interval_s=2.5,
        llm_provider=mock_llm,
        frame_cache=shared_cache,
        cache_path=cache_file,
        frames=unique_frames,
    )
    jsonschema.validate(instance=doc, schema=VISUAL_DOC_SCHEMA)

    assert doc["enabled"] is True
    assert doc["budget_max_frames"] == 6
    assert doc["frames_analyzed"] == 6
    # Frames 0..3 share one SHA-256 hash (1 LLM call + 3 cache hits), frames 4 & 5 are unique (2 LLM calls)
    assert doc["cache_hits"] == 3
    assert doc["llm_calls"] == 3
    assert mock_llm.calls == 3
    assert any("visual_budget_capped_at_6_frames" in w for w in doc["warnings"])
    assert cache_file.exists()

    # Re-running with the disk cache should produce 100% cache hits and 0 new LLM calls
    second_llm = _MockLLM(raise_exc=LLMUnavailableError("should not be called when cached"))
    cached_doc = analyze_visual_windows(
        candidate_windows=[{"start": 0.0, "end": 25.0}],
        genre="screenshare_tutorial",
        max_frames_per_job=6,
        frame_interval_s=2.5,
        llm_provider=second_llm,
        cache_path=cache_file,
        frames=unique_frames,
    )
    jsonschema.validate(instance=cached_doc, schema=VISUAL_DOC_SCHEMA)
    assert cached_doc["frames_analyzed"] == 6
    assert cached_doc["cache_hits"] == 6
    assert cached_doc["llm_calls"] == 0
    assert second_llm.calls == 0


def test_analyze_visual_windows_graceful_downgrade_on_llm_failure() -> None:
    frame = SampledFrame(
        timestamp=5.0,
        image=np.random.default_rng(42).integers(0, 255, size=(36, 64, 3), dtype=np.uint8),
    )
    failing_llm = _MockLLM(raise_exc=LLMUnavailableError("vision endpoint timeout"))

    doc = analyze_visual_windows(
        candidate_windows=[(4.0, 8.0)],
        genre="gaming",
        llm_provider=failing_llm,
        frames=[frame],
    )
    jsonschema.validate(instance=doc, schema=VISUAL_DOC_SCHEMA)
    assert doc["enabled"] is True
    assert doc["frames_analyzed"] == 1
    assert any("visual_llm_fallback" in w for w in doc["warnings"])
    assert 0.0 <= doc["frames"][0]["visual_interest"] <= 1.0
