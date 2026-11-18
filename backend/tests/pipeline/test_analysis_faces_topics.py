from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.pipeline.analysis import (
    analyze_faces,
    choose_sampling_fps,
    face_presence_ratio,
    segment_topics,
    topic_for_time,
)
from backend.pipeline.providers.base import LLMResult, SchemaValidationError
from backend.pipeline.reframe.tracker import FaceDetection, SampledFrame

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "assets" / "samples"


class _ScriptedFaceDetector:
    """Deterministic test double implementing FaceDetector."""

    def __init__(
        self,
        detections_by_time: dict[float, list[FaceDetection]],
        *,
        fail_times: set[float] | None = None,
    ) -> None:
        self._by_time = detections_by_time
        self._fail_times = fail_times or set()

    def detect(self, frame_rgb: np.ndarray, timestamp: float) -> list[FaceDetection]:
        t = round(float(timestamp), 3)
        if t in self._fail_times:
            raise RuntimeError(f"synthetic detector failure at t={t}")
        return list(self._by_time.get(t, []))


class _AlwaysFailingDetector:
    def detect(self, frame_rgb: np.ndarray, timestamp: float) -> list[FaceDetection]:
        raise RuntimeError("mediapipe runtime unavailable")


class _MockTopicLLM:
    provider_name = "mock"
    model = "mock-topic-v1"

    def __init__(self, payload: dict[str, Any] | None = None, *, raise_exc: Exception | None = None) -> None:
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
            usage={"prompt_tokens": 50, "completion_tokens": 40},
        )


def _dummy_frames(timestamps: list[float]) -> list[SampledFrame]:
    img = np.zeros((36, 64, 3), dtype=np.uint8)
    return [SampledFrame(timestamp=t, image=img) for t in timestamps]


def test_choose_sampling_fps_adaptive_on_long_videos() -> None:
    assert choose_sampling_fps(60.0) == 5.0
    assert choose_sampling_fps(900.0) == 5.0
    assert choose_sampling_fps(900.1) == 2.0
    assert choose_sampling_fps(1800.0) == 2.0

    with pytest.raises(ValueError, match="must be > 0"):
        choose_sampling_fps(100.0, base_fps=0.0)


def test_analyze_faces_adaptive_fps_and_active_speaker_ranking() -> None:
    timestamps = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    frames = _dummy_frames(timestamps)

    # Track 1 (left): present on all 6 frames, larger box, high mouth motion
    # Track 2 (right): present on 4 frames, smaller box, low mouth motion
    dets_by_t: dict[float, list[FaceDetection]] = {}
    for idx, t in enumerate(timestamps):
        dets = [
            FaceDetection(
                bbox_norm=(0.15, 0.20, 0.25, 0.35),
                confidence=0.95,
                mouth_motion=0.75,
                face_id=1,
            )
        ]
        if idx < 4:
            dets.append(
                FaceDetection(
                    bbox_norm=(0.65, 0.25, 0.15, 0.22),
                    confidence=0.88,
                    mouth_motion=0.10,
                    face_id=2,
                )
            )
        dets_by_t[t] = dets

    detector = _ScriptedFaceDetector(dets_by_t)
    res = analyze_faces(
        None,
        duration_s=1200.0,  # > 900s triggers adaptive 2.0 fps
        detector=detector,
        frames=frames,
    )

    assert res["schema_version"] == 1
    assert res["status"] == "ok"
    assert res["adaptive_fps_applied"] is True
    assert res["sampling_fps"] == 2.0
    assert res["frame_count"] == 6
    assert res["summary"]["speakers_estimated"] == 2
    assert res["summary"]["face_presence_ratio"] == 1.0
    assert res["summary"]["max_face_count"] == 2
    assert len(res["dominant_speaker_track_ids"]) == 2

    top_track = res["dominant_track_id"]
    assert top_track == res["dominant_speaker_track_ids"][0]
    second_track = res["dominant_speaker_track_ids"][1]
    assert res["tracks"][top_track]["speaker_score"] > res["tracks"][second_track]["speaker_score"]
    assert res["tracks"][top_track]["mean_mouth_motion"] > res["tracks"][second_track]["mean_mouth_motion"]


def test_face_presence_ratio_window_queries() -> None:
    timestamps = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    frames = _dummy_frames(timestamps)
    # Faces present only in [0.0, 2.0), absent in [2.0, 4.0)
    dets_by_t: dict[float, list[FaceDetection]] = {
        0.0: [FaceDetection(bbox_norm=(0.3, 0.2, 0.2, 0.3), confidence=0.9, face_id=1)],
        0.5: [FaceDetection(bbox_norm=(0.3, 0.2, 0.2, 0.3), confidence=0.9, face_id=1)],
        1.0: [FaceDetection(bbox_norm=(0.3, 0.2, 0.2, 0.3), confidence=0.9, face_id=1)],
        1.5: [FaceDetection(bbox_norm=(0.3, 0.2, 0.2, 0.3), confidence=0.9, face_id=1)],
    }
    res = analyze_faces(
        None,
        duration_s=4.0,
        detector=_ScriptedFaceDetector(dets_by_t),
        frames=frames,
    )

    assert res["summary"]["face_presence_ratio"] == 0.5
    assert face_presence_ratio(res, 0.0, 2.0) == 1.0
    assert face_presence_ratio(res, 2.0, 4.0) == 0.0
    assert face_presence_ratio(res, 1.0, 3.0) == 0.5

    with pytest.raises(ValueError, match="window_end"):
        face_presence_ratio(res, 3.0, 1.0)


def test_analyze_faces_graceful_degradation_when_detector_fails() -> None:
    frames = _dummy_frames([0.0, 0.2, 0.4, 0.6])
    res = analyze_faces(
        None,
        duration_s=1.0,
        detector=_AlwaysFailingDetector(),
        frames=frames,
    )

    assert res["schema_version"] == 1
    assert res["status"] == "degraded"
    assert res["face_presence_series"] is None
    assert res["summary"]["face_presence_ratio"] is None
    assert res["summary"]["speakers_estimated"] == 0
    assert res["warnings"]
    assert face_presence_ratio(res, 0.0, 1.0) is None

    # Also test when no media or frames are provided
    no_media_res = analyze_faces(None, duration_s=10.0)
    assert no_media_res["status"] == "degraded"
    assert no_media_res["face_presence_series"] is None
    assert "no_media_source_provided" in no_media_res["warnings"]


def test_segment_topics_youtube_chapters_seed_and_topic_for_time() -> None:
    transcript_path = SAMPLES_DIR / "sample_hinglish_podcast.transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

    chapters = [
        {"title": "Intro & Welcome", "start_time": 0.0, "end_time": 30.0},
        {"title": "Consistency vs Virality", "start_time": 30.0, "end_time": 60.0},
        {"title": "Creator Burnout Advice", "start_time": 60.0, "end_time": 90.0},
    ]

    doc = segment_topics(transcript, chapters=chapters)
    assert doc["schema_version"] == 1
    assert doc["source"] == "chapters"
    assert len(doc["topics"]) == 3
    assert [t["title"] for t in doc["topics"]] == [
        "Intro & Welcome",
        "Consistency vs Virality",
        "Creator Burnout Advice",
    ]

    # All transcript segments must be covered
    covered_ids = {sid for t in doc["topics"] for sid in t["segment_ids"]}
    expected_ids = {int(s["id"]) for s in transcript["segments"]}
    assert covered_ids == expected_ids

    t0 = topic_for_time(doc, 10.0)
    assert t0 is not None and t0["title"] == "Intro & Welcome"
    t1 = topic_for_time(doc, 45.0)
    assert t1 is not None and t1["title"] == "Consistency vs Virality"
    t2 = topic_for_time(doc, 90.0)
    assert t2 is not None and t2["title"] == "Creator Burnout Advice"
    assert topic_for_time(doc, -5.0) is None
    assert topic_for_time(doc, 120.0) is None


def test_segment_topics_deterministic_tfidf_segmentation() -> None:
    synthetic_transcript = {
        "duration": 120.0,
        "language": "en",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 18.0,
                "text": "Database indexing with B-trees and PostgreSQL query planner optimization reduces latency.",
            },
            {
                "id": 1,
                "start": 18.5,
                "end": 38.0,
                "text": "PostgreSQL vacuuming and B-tree index selectivity keep database queries fast under load.",
            },
            {
                "id": 2,
                "start": 41.0,
                "end": 60.0,
                "text": "Now let's talk about CSS grid layout, flexbox alignment, and responsive typography in browsers.",
            },
            {
                "id": 3,
                "start": 60.5,
                "end": 80.0,
                "text": "Responsive CSS flexbox components and browser media queries adapt typography to mobile screens.",
            },
            {
                "id": 4,
                "start": 83.0,
                "end": 102.0,
                "text": "Moving on to Kubernetes pod autoscaling, container orchestration, and Helm chart deployments.",
            },
            {
                "id": 5,
                "start": 102.5,
                "end": 120.0,
                "text": "Kubernetes cluster autoscaling and container health probes prevent deployment downtime.",
            },
        ],
    }

    doc = segment_topics(synthetic_transcript, max_topics=5)
    assert doc["schema_version"] == 1
    assert doc["source"] == "heuristic"
    assert 2 <= len(doc["topics"]) <= 5
    assert doc["topics"][0]["start"] == 0.0
    assert doc["topics"][-1]["end"] == 120.0

    for idx, topic in enumerate(doc["topics"]):
        assert topic["id"] == f"topic_{idx}"
        assert topic["title"]
        assert topic["context_line"]
        assert topic["keywords"]
        if idx > 0:
            assert topic["start"] == doc["topics"][idx - 1]["end"]


def test_segment_topics_mocked_llm_and_fallback_on_error() -> None:
    transcript_path = SAMPLES_DIR / "sample_english_keynote.transcript.json"
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

    llm_payload = {
        "topics": [
            {
                "title": "Shipping Velocity Beats Perfection",
                "start": 0.0,
                "end": 38.0,
                "summary": "Why founders must prioritize fast iteration in year one.",
                "keywords": ["shipping", "founders", "speed"],
                "context_line": "Shipping Velocity Beats Perfection: Fast iteration in year one.",
                "segment_ids": [0, 1],
            },
            {
                "title": "Ruthless Focus & Execution",
                "start": 38.0,
                "end": 75.0,
                "summary": "Eliminating distractions to compound product advantage.",
                "keywords": ["focus", "execution", "product"],
                "context_line": "Ruthless Focus & Execution: Compounding product advantage.",
                "segment_ids": [2, 3],
            },
        ]
    }
    mock_llm = _MockTopicLLM(llm_payload)
    doc = segment_topics(transcript, llm_provider=mock_llm)
    assert doc["source"] == "llm"
    assert mock_llm.calls == 1
    assert len(doc["topics"]) == 2
    assert doc["topics"][0]["title"] == "Shipping Velocity Beats Perfection"
    assert doc["topics"][1]["end"] == 75.0

    # When LLM fails schema validation, segment_topics must fall back gracefully to heuristic
    bad_llm = _MockTopicLLM(raise_exc=SchemaValidationError("invalid topic JSON"))
    fallback_doc = segment_topics(transcript, llm_provider=bad_llm)
    assert fallback_doc["source"] == "heuristic"
    assert len(fallback_doc["topics"]) >= 1
    assert any("llm_topic_fallback" in w for w in fallback_doc["warnings"])
