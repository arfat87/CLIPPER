from __future__ import annotations

import json
import math
import random
import subprocess
from pathlib import Path

import numpy as np
import pytest

from backend.pipeline.reframe import (
    ASPECT_RESOLUTIONS,
    FaceDetection,
    FaceDetector,
    MotionBoxDetector,
    ReframeError,
    ReframeKeyframe,
    ReframePath,
    SampledFrame,
    build_reframe_path,
    compute_crop_box,
    compute_headroom_target,
    compute_jitter_metric,
    create_face_detector,
    sample_clip_frames,
)


class _InjectedDetector:
    """Deterministic test detector implementing the FaceDetector protocol."""

    def __init__(self, fn) -> None:
        self._fn = fn

    def detect(self, frame: np.ndarray, timestamp: float) -> list[FaceDetection]:
        return self._fn(frame, timestamp)


def _make_moving_box_video(dest: Path, *, duration: float = 4.0, size: str = "640x360") -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x0f172a:s={size}:r=30:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x38bdf8:s=100x120:r=30:d={duration}",
        "-filter_complex",
        "[0:v][1:v]overlay=x='(W-w)*0.15 + (W*0.55)*(t/4.0)':y='(H-h)*0.3':shortest=1",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def test_face_detector_protocol_and_default_backend() -> None:
    det = create_face_detector("motion_box")
    assert isinstance(det, FaceDetector)
    auto_det = create_face_detector("auto")
    assert isinstance(auto_det, FaceDetector)

    with pytest.raises(ReframeError):
        create_face_detector("unknown")  # type: ignore[arg-type]


def test_synthetic_moving_face_smoothing_and_jitter_reduction() -> None:
    rng = random.Random(42)
    fps_sample = 5.0
    duration = 10.0
    times = [round(i / fps_sample, 3) for i in range(int(duration * fps_sample) + 1)]

    def true_cx(t: float) -> float:
        return 0.25 + 0.50 * (t / duration)

    frames = [SampledFrame(timestamp=t, image=np.zeros((18, 32, 3), dtype=np.uint8)) for t in times]
    raw_centers: list[ReframeKeyframe] = []

    def _detect(frame: np.ndarray, t: float) -> list[FaceDetection]:
        noisy_cx = true_cx(t) + rng.uniform(-0.055, 0.055)
        raw_centers.append(ReframeKeyframe(t=t, cx=noisy_cx, cy=0.42))
        w, h = 0.16, 0.28
        return [
            FaceDetection(
                bbox_norm=(noisy_cx - w / 2, 0.42 - h / 2, w, h),
                confidence=0.95,
                mouth_motion=0.3,
                face_id=1,
            )
        ]

    path = build_reframe_path(
        frames=frames,
        detector=_InjectedDetector(_detect),
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
    )

    assert len(path.keyframes) == len(times)
    raw_jitter = compute_jitter_metric(raw_centers)
    tracked_jitter = compute_jitter_metric(path.keyframes, path.cuts)

    assert raw_jitter > 0.03
    assert tracked_jitter < 0.015
    assert tracked_jitter < raw_jitter * 0.45

    errors = [abs(kf.cx - true_cx(kf.t)) for kf in path.keyframes]
    assert max(errors) < 0.09
    assert sum(errors) / len(errors) < 0.06


def test_scene_cut_resets_ema_without_pan_interpolation() -> None:
    times = [round(i * 0.2, 3) for i in range(41)]  # 0.0 .. 8.0s
    cut_t = 5.0

    detections = []
    for t in times:
        if t < cut_t:
            cx = 0.22
            fid = 1
        else:
            cx = 0.78
            fid = 2
        detections.append(
            (
                t,
                [
                    FaceDetection(
                        bbox_norm=(cx - 0.08, 0.30, 0.16, 0.26),
                        confidence=0.95,
                        mouth_motion=0.4,
                        face_id=fid,
                    )
                ],
            )
        )

    path = build_reframe_path(
        detections_by_frame=detections,
        cuts=[cut_t],
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
    )

    before_cut = [kf for kf in path.keyframes if kf.t < cut_t]
    at_cut = next(kf for kf in path.keyframes if math.isclose(kf.t, cut_t, abs_tol=1e-6))

    assert math.isclose(before_cut[-1].cx, 0.22, abs_tol=1e-4)
    # Instantaneous jump at cut (not EMA-smoothed from 0.22!)
    assert math.isclose(at_cut.cx, 0.78, abs_tol=1e-4)
    # Continuous-scene jitter excludes the cut jump
    assert compute_jitter_metric(path.keyframes, path.cuts) < 1e-4


def test_two_speaker_hysteresis_requires_one_second_dominance() -> None:
    times = [round(i * 0.2, 3) for i in range(31)]  # 0.0 .. 6.0s
    detections = []

    for t in times:
        # Speaker A on left (0.25), Speaker B on right (0.75)
        if t < 1.0:
            mouth_a, mouth_b = 0.8, 0.1
        elif 1.0 <= t < 1.6:
            # Brief 0.6s dominance by Speaker B (< 1.0s) -> must NOT switch
            mouth_a, mouth_b = 0.1, 0.9
        elif 1.6 <= t < 2.2:
            # Speaker A dominates again -> resets hysteresis timer
            mouth_a, mouth_b = 0.8, 0.1
        else:
            # Speaker B dominates continuously from t=2.2s onward -> switches at t >= 3.2s
            mouth_a, mouth_b = 0.05, 0.9

        detections.append(
            (
                t,
                [
                    FaceDetection(
                        bbox_norm=(0.25 - 0.07, 0.30, 0.14, 0.24),
                        confidence=0.95,
                        mouth_motion=mouth_a,
                        face_id=None,  # verify centroid track matching works without explicit IDs
                    ),
                    FaceDetection(
                        bbox_norm=(0.75 - 0.07, 0.30, 0.14, 0.24),
                        confidence=0.95,
                        mouth_motion=mouth_b,
                        face_id=None,
                    ),
                ],
            )
        )

    path = build_reframe_path(
        detections_by_frame=detections,
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
        hysteresis_s=1.0,
    )

    by_t = {kf.t: kf for kf in path.keyframes}
    # Never switched during brief 0.6s burst
    assert by_t[1.4].cx < 0.35
    # Still on Speaker A before 1.0s sustained dominance (2.2 + 0.8 = 3.0s)
    assert by_t[3.0].cx < 0.35
    # Switched to Speaker B at/after 2.2 + 1.0 = 3.2s and pans cleanly toward 0.75
    assert by_t[3.2].cx > by_t[3.0].cx
    assert by_t[6.0].cx > 0.65


def test_no_face_safety_fallback_and_forced_layout_override() -> None:
    times = [round(i * 0.2, 3) for i in range(26)]  # 0.0 .. 5.0s
    detections = []
    for t in times:
        if t < 1.0:
            # Confident face on left
            detections.append(
                (
                    t,
                    [
                        FaceDetection(
                            bbox_norm=(0.20, 0.30, 0.14, 0.24),
                            confidence=0.92,
                            mouth_motion=0.3,
                            face_id=1,
                        )
                    ],
                )
            )
        else:
            # Low-confidence / no face from t=1.0s onward
            detections.append(
                (
                    t,
                    [
                        FaceDetection(
                            bbox_norm=(0.20, 0.30, 0.14, 0.24),
                            confidence=0.0,
                            mouth_motion=0.0,
                            face_id=1,
                        )
                    ],
                )
            )

    auto_path = build_reframe_path(
        detections_by_frame=detections,
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
    )
    by_t = {kf.t: kf for kf in auto_path.keyframes}
    # During brief dropout <= 1.5s (1.0 -> 2.4s), remains "fill"
    assert by_t[2.4].layout == "fill"
    # After > 1.5s (t >= 2.6s), switches to "fit_blur" and blends smoothly toward center (0.5)
    assert by_t[2.6].layout == "fit_blur"
    assert by_t[5.0].layout == "fit_blur"
    assert abs(by_t[5.0].cx - 0.5) < abs(by_t[2.4].cx - 0.5)

    # Forced "fill" keeps layout="fill" and blends smoothly toward centered crop
    forced_fill = build_reframe_path(
        detections_by_frame=detections,
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
        force_layout="fill",
    )
    assert all(kf.layout == "fill" for kf in forced_fill.keyframes)
    assert abs(forced_fill.keyframes[-1].cx - 0.5) < abs(forced_fill.keyframes[0].cx - 0.5)

    # Forced "fit_blur" sets every keyframe to "fit_blur"
    forced_blur = build_reframe_path(
        detections_by_frame=detections,
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
        force_layout="fit_blur",
    )
    assert all(kf.layout == "fit_blur" for kf in forced_blur.keyframes)


def test_frame_bounds_property_across_aspects_resolutions_and_random_inputs() -> None:
    rng = random.Random(1337)
    resolutions = [
        (1920, 1080),
        (1280, 720),
        (1080, 1920),
        (854, 480),
        (640, 480),
        (1080, 1080),
    ]
    aspects = list(ASPECT_RESOLUTIONS.keys())

    for _ in range(500):
        src_w, src_h = rng.choice(resolutions)
        aspect = rng.choice(aspects)
        cx = rng.uniform(-0.5, 1.5)
        cy = rng.uniform(-0.5, 1.5)
        zoom = rng.uniform(0.5, 3.0)

        box = compute_crop_box(cx, cy, zoom, src_w, src_h, aspect)
        assert box.x >= 0.0
        assert box.y >= 0.0
        assert box.w > 0.0
        assert box.h > 0.0
        assert box.x + box.w <= src_w + 1e-9
        assert box.y + box.h <= src_h + 1e-9
        assert 0.0 <= box.x_norm <= 1.0
        assert 0.0 <= box.y_norm <= 1.0
        assert box.x_norm + box.w_norm <= 1.0 + 1e-9
        assert box.y_norm + box.h_norm <= 1.0 + 1e-9
        assert (box.out_w, box.out_h) == ASPECT_RESOLUTIONS[aspect]


def test_headroom_rule_positions_face_near_forty_percent() -> None:
    # Tall source cropped to 1:1 gives full vertical headroom freedom
    bbox = (0.40, 0.25, 0.20, 0.14)  # face_cy = 0.32
    cx, cy = compute_headroom_target(
        bbox,
        zoom=1.0,
        source_w=1080,
        source_h=1920,
        out_aspect="1:1",
        headroom_ratio=0.40,
    )
    box = compute_crop_box(cx, cy, 1.0, 1080, 1920, "1:1")
    face_cy = bbox[1] + bbox[3] * 0.5
    pos_in_crop = (face_cy - box.y_norm) / box.h_norm
    assert 0.38 <= pos_in_crop <= 0.42


def test_low_res_upscaling_warning_and_json_round_trip(tmp_path: Path) -> None:
    detections = [
        (0.0, [FaceDetection(bbox_norm=(0.4, 0.3, 0.2, 0.25), confidence=0.9, face_id=1)]),
        (0.2, [FaceDetection(bbox_norm=(0.42, 0.3, 0.2, 0.25), confidence=0.9, face_id=1)]),
    ]
    low_res_path = build_reframe_path(
        detections_by_frame=detections,
        cuts=[1.2, 2.4],
        source_w=854,
        source_h=480,
        out_aspect="4:5",
    )
    assert any("upscal" in w.lower() and "720" in w for w in low_res_path.warnings)

    hd_path = build_reframe_path(
        detections_by_frame=detections,
        source_w=1920,
        source_h=1080,
        out_aspect="9:16",
    )
    assert hd_path.warnings == []

    json_file = tmp_path / "reframe_path.json"
    low_res_path.save_json(json_file)
    parsed = json.loads(json_file.read_text(encoding="utf-8"))
    assert set(parsed.keys()) == {
        "schema_version",
        "fps",
        "source_w",
        "source_h",
        "out_aspect",
        "keyframes",
        "cuts",
        "warnings",
    }
    assert parsed["schema_version"] == 1
    assert parsed["out_aspect"] == "4:5"

    reloaded = ReframePath.load_json(json_file)
    assert reloaded.to_dict() == low_res_path.to_dict()


def test_ffmpeg_sample_clip_frames_and_motion_box_end_to_end(tmp_path: Path) -> None:
    video_path = _make_moving_box_video(tmp_path / "moving.mp4", duration=4.0, size="640x360")
    frames = sample_clip_frames(
        video_path,
        base_fps=5.0,
        dense_fps=10.0,
        cuts=[2.0],
        dense_window_s=0.5,
    )
    assert len(frames) > 15

    # Verify 10 fps around cut [1.5, 2.5] vs 5 fps away from cut [0.0, 1.2]
    early_dts = [
        round(frames[i].timestamp - frames[i - 1].timestamp, 3)
        for i in range(1, len(frames))
        if frames[i].timestamp <= 1.2
    ]
    cut_dts = [
        round(frames[i].timestamp - frames[i - 1].timestamp, 3)
        for i in range(1, len(frames))
        if 1.6 <= frames[i].timestamp <= 2.4
    ]
    assert all(math.isclose(dt, 0.2, abs_tol=0.02) for dt in early_dts)
    assert all(math.isclose(dt, 0.1, abs_tol=0.02) for dt in cut_dts)

    reframe = build_reframe_path(
        video_path=video_path,
        detector=MotionBoxDetector(),
        cuts=[2.0],
        dense_window_s=0.5,
        out_aspect="9:16",
    )
    assert reframe.source_w == 640
    assert reframe.source_h == 360
    assert any("upscal" in w.lower() for w in reframe.warnings)
    assert len(reframe.keyframes) == len(frames)
    # Motion box moved from left toward right over the 4s clip
    assert reframe.keyframes[-1].cx > reframe.keyframes[1].cx + 0.12
    assert all(kf.layout == "fill" for kf in reframe.keyframes)
