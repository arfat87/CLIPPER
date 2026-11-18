from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from backend.pipeline.reframe.tracker import (
    FaceDetection,
    FaceDetector,
    SampledFrame,
    _assign_track_ids,
    create_face_detector,
    probe_video_metadata,
    sample_clip_frames,
    score_face_detection,
)

DEFAULT_BASE_FPS = 5.0
DEFAULT_LONG_VIDEO_THRESHOLD_S = 900.0  # 15 minutes
DEFAULT_LONG_VIDEO_FPS = 2.0
DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_ANALYSIS_WIDTH = 320
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def choose_sampling_fps(
    duration_s: float,
    *,
    base_fps: float = DEFAULT_BASE_FPS,
    long_video_threshold_s: float = DEFAULT_LONG_VIDEO_THRESHOLD_S,
    long_video_fps: float = DEFAULT_LONG_VIDEO_FPS,
) -> float:
    """Select adaptive face-sampling FPS (5 fps default, 2 fps on videos > 15 min)."""
    if base_fps <= 0.0 or long_video_fps <= 0.0:
        raise ValueError("base_fps and long_video_fps must be > 0")
    if duration_s > long_video_threshold_s:
        return float(long_video_fps)
    return float(base_fps)


def _config_hash(
    *,
    base_fps: float,
    long_video_threshold_s: float,
    long_video_fps: float,
    confidence_threshold: float,
) -> str:
    payload = json.dumps(
        {
            "base_fps": round(float(base_fps), 4),
            "long_video_threshold_s": round(float(long_video_threshold_s), 4),
            "long_video_fps": round(float(long_video_fps), 4),
            "confidence_threshold": round(float(confidence_threshold), 4),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _load_image_rgb(image_path: Path) -> np.ndarray:
    """Load an image file as an RGB uint8 NumPy array using PIL, OpenCV, or FFmpeg."""
    if importlib.util.find_spec("PIL") is not None:
        pil_image = importlib.import_module("PIL.Image")
        with pil_image.open(image_path) as img:
            return np.asarray(img.convert("RGB"), dtype=np.uint8)

    if importlib.util.find_spec("cv2") is not None:
        cv2 = importlib.import_module("cv2")
        bgr = cv2.imread(str(image_path))
        if bgr is None:
            raise RuntimeError(f"Failed to read thumbnail image: {image_path}")
        return np.ascontiguousarray(bgr[:, :, [2, 1, 0]], dtype=np.uint8)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(image_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        detail = probe.stderr.strip() or f"exit code {probe.returncode}"
        raise RuntimeError(f"ffprobe failed on thumbnail {image_path.name}: {detail}")

    info = json.loads(probe.stdout)
    streams = info.get("streams") or []
    if not streams:
        raise RuntimeError(f"No video/image stream found in {image_path.name}")
    width = int(streams[0]["width"])
    height = int(streams[0]["height"])

    dec = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(image_path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if dec.returncode != 0:
        detail = dec.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed decoding thumbnail {image_path.name}: {detail}")

    expected = width * height * 3
    if len(dec.stdout) < expected or expected <= 0:
        raise RuntimeError(f"Decoded thumbnail buffer too small for {image_path.name}")
    return np.frombuffer(dec.stdout, dtype=np.uint8, count=expected).reshape((height, width, 3)).copy()


def _load_frames_from_thumbs(
    thumbs_dir: Path,
    *,
    duration_s: float,
    sampling_fps: float,
) -> tuple[list[SampledFrame], float]:
    """Load sorted thumbnail images from `thumbs_dir` as SampledFrame objects."""
    if not thumbs_dir.exists() or not thumbs_dir.is_dir():
        raise FileNotFoundError(f"Thumbnail directory does not exist: {thumbs_dir}")

    thumb_files = sorted(
        p
        for p in thumbs_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    )
    if not thumb_files:
        raise FileNotFoundError(f"No thumbnail images found in {thumbs_dir}")

    n_thumbs = len(thumb_files)
    if duration_s > 0.0:
        step = duration_s / n_thumbs
        effective_duration = float(duration_s)
    else:
        step = 1.0 / max(sampling_fps, 1e-6)
        effective_duration = round(n_thumbs * step, 3)

    sampled: list[SampledFrame] = []
    for idx, path in enumerate(thumb_files):
        img = _load_image_rgb(path)
        sampled.append(SampledFrame(timestamp=round(idx * step, 3), image=img))
    return sampled, effective_duration


def _serialize_landmarks(raw_landmarks: Any) -> list[list[float]] | dict[str, Any] | None:
    if raw_landmarks is None:
        return None
    if isinstance(raw_landmarks, Mapping):
        out_map: dict[str, Any] = {}
        for k, v in raw_landmarks.items():
            if isinstance(v, (tuple, list)):
                out_map[str(k)] = [round(float(x), 5) for x in v]
            elif isinstance(v, (int, float)):
                out_map[str(k)] = round(float(v), 5)
        return out_map or None
    if isinstance(raw_landmarks, Sequence) and not isinstance(raw_landmarks, (str, bytes)):
        pts: list[list[float]] = []
        for item in raw_landmarks:
            if isinstance(item, Sequence) and len(item) >= 2:
                pts.append([round(float(item[0]), 5), round(float(item[1]), 5)])
            elif hasattr(item, "x") and hasattr(item, "y"):
                pts.append([round(float(item.x), 5), round(float(item.y), 5)])
        return pts or None
    return None


def _mouth_proxy_from_landmarks(
    landmarks: Any,
    bbox_h: float,
    prev_open: float | None,
) -> tuple[float, float | None]:
    """Compute a normalized mouth-motion proxy if lip landmarks are present."""
    if isinstance(landmarks, Mapping):
        upper = landmarks.get("upper_lip")
        lower = landmarks.get("lower_lip")
        if (
            isinstance(upper, Sequence)
            and isinstance(lower, Sequence)
            and len(upper) >= 2
            and len(lower) >= 2
        ):
            mouth_open = abs(float(lower[1]) - float(upper[1])) / max(bbox_h, 1e-4)
            baseline = mouth_open if prev_open is None else prev_open
            motion = min(1.0, abs(mouth_open - baseline) * 10.0)
            return round(motion, 4), mouth_open
    return 0.0, prev_open


def _degraded_result(
    *,
    duration_s: float,
    sampling_fps: float,
    adaptive_applied: bool,
    warnings: list[str],
    cfg_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "produced_by": {
            "stage": "analyze_faces",
            "version": "1.0.0",
            "config_hash": cfg_hash,
        },
        "status": "degraded",
        "hz": 1,
        "duration_s": round(max(0.0, float(duration_s)), 3),
        "sampling_fps": round(float(sampling_fps), 3),
        "adaptive_fps_applied": adaptive_applied,
        "frame_count": 0,
        "frames": [],
        "face_presence_series": None,
        "dominant_speaker_track_ids": [],
        "dominant_track_id": None,
        "tracks": {},
        "summary": {
            "face_presence_ratio": None,
            "mean_face_count": None,
            "max_face_count": None,
            "unique_track_count": 0,
            "speakers_estimated": 0,
        },
        "warnings": warnings,
    }


def _build_1hz_presence_series(
    frame_records: list[dict[str, Any]],
    *,
    duration_s: float,
    sampling_fps: float,
    failed_timestamps: set[float],
) -> list[float | None]:
    if duration_s <= 0.0:
        if not frame_records:
            return []
        max_t = max(float(f["t"]) for f in frame_records)
        n_bins = max(1, int(math.floor(max_t)) + 1)
    else:
        n_bins = max(1, int(math.ceil(duration_s)))

    series: list[float | None] = []
    max_nearest_dist = max(1.0, 1.0 / max(sampling_fps, 1e-3))

    for sec in range(n_bins):
        win_start = float(sec)
        win_end = float(sec + 1)

        if any(win_start <= ft < win_end for ft in failed_timestamps) and not any(
            win_start <= float(f["t"]) < win_end for f in frame_records
        ):
            series.append(None)
            continue

        bucket = [f for f in frame_records if win_start <= float(f["t"]) < win_end]
        if bucket:
            ratio = sum(1 for f in bucket if int(f["face_count"]) > 0) / len(bucket)
            series.append(round(min(1.0, max(0.0, ratio)), 4))
            continue

        if sampling_fps < 1.0 and frame_records:
            center_t = win_start + 0.5
            nearest = min(frame_records, key=lambda f: abs(float(f["t"]) - center_t))
            if abs(float(nearest["t"]) - center_t) <= max_nearest_dist:
                series.append(1.0 if int(nearest["face_count"]) > 0 else 0.0)
                continue

        series.append(None)

    return series


def face_presence_ratio(
    faces_data: Mapping[str, Any],
    window_start: float,
    window_end: float,
) -> float | None:
    """Compute fraction of sampled frames/seconds with >=1 face in `[window_start, window_end]`."""
    start = float(window_start)
    end = float(window_end)
    if end < start:
        raise ValueError(f"window_end ({end}) must be >= window_start ({start})")

    if faces_data.get("status") == "degraded" or faces_data.get("face_presence_series") is None:
        return None

    frames = faces_data.get("frames") or []
    if isinstance(frames, Sequence) and frames:
        if end > start:
            win_frames = [f for f in frames if start <= float(f["t"]) < end]
            if not win_frames:
                win_frames = [f for f in frames if start <= float(f["t"]) <= end]
        else:
            win_frames = [f for f in frames if math.isclose(float(f["t"]), start, abs_tol=1e-6)]

        if win_frames:
            present = sum(1 for f in win_frames if int(f.get("face_count", 0)) > 0)
            return round(present / len(win_frames), 4)

    series = faces_data.get("face_presence_series")
    if isinstance(series, Sequence) and series:
        start_idx = max(0, int(math.floor(start)))
        end_idx = max(start_idx + 1, int(math.ceil(end))) if end > start else start_idx + 1
        vals = [
            float(v)
            for v in series[start_idx:end_idx]
            if v is not None
        ]
        if vals:
            return round(sum(vals) / len(vals), 4)

    return None


def analyze_faces(
    video_path: Path | None,
    *,
    thumbs_dir: Path | None = None,
    duration_s: float = 0.0,
    base_fps: float = DEFAULT_BASE_FPS,
    long_video_threshold_s: float = DEFAULT_LONG_VIDEO_THRESHOLD_S,
    long_video_fps: float = DEFAULT_LONG_VIDEO_FPS,
    detector: FaceDetector | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    frames: Sequence[SampledFrame] | None = None,
    analysis_width: int = DEFAULT_ANALYSIS_WIDTH,
) -> dict[str, Any]:
    """Analyze face presence, bounding boxes, mouth-motion proxy, and dominant speaker tracks."""
    warnings: list[str] = []
    cfg_hash = _config_hash(
        base_fps=base_fps,
        long_video_threshold_s=long_video_threshold_s,
        long_video_fps=long_video_fps,
        confidence_threshold=confidence_threshold,
    )

    effective_duration = max(0.0, float(duration_s))
    if effective_duration <= 0.0 and video_path is not None:
        try:
            meta = probe_video_metadata(Path(video_path))
            effective_duration = max(0.0, float(meta.get("duration") or 0.0))
        except Exception as exc:
            warnings.append(f"video_probe_failed: {exc}")

    if effective_duration <= 0.0 and frames:
        max_frame_t = max(float(f.timestamp) for f in frames)
        effective_duration = round(max_frame_t + (1.0 / max(base_fps, 1e-3)), 3)

    sampling_fps = choose_sampling_fps(
        effective_duration,
        base_fps=base_fps,
        long_video_threshold_s=long_video_threshold_s,
        long_video_fps=long_video_fps,
    )
    adaptive_applied = effective_duration > long_video_threshold_s

    sampled_frames: list[SampledFrame] = []
    if frames is not None:
        sampled_frames = sorted(frames, key=lambda f: float(f.timestamp))
    elif video_path is not None:
        try:
            sampled_frames = sample_clip_frames(
                Path(video_path),
                start=0.0,
                end=None,
                base_fps=sampling_fps,
                dense_fps=sampling_fps,
                analysis_width=analysis_width,
            )
            if effective_duration <= 0.0 and sampled_frames:
                effective_duration = round(
                    max(float(f.timestamp) for f in sampled_frames) + (1.0 / sampling_fps),
                    3,
                )
        except Exception as exc:
            warnings.append(f"video_sampling_failed: {exc}")
            if thumbs_dir is not None:
                try:
                    sampled_frames, thumb_dur = _load_frames_from_thumbs(
                        Path(thumbs_dir),
                        duration_s=effective_duration,
                        sampling_fps=sampling_fps,
                    )
                    if effective_duration <= 0.0:
                        effective_duration = thumb_dur
                except Exception as thumb_exc:
                    warnings.append(f"thumb_sampling_failed: {thumb_exc}")
    elif thumbs_dir is not None:
        try:
            sampled_frames, thumb_dur = _load_frames_from_thumbs(
                Path(thumbs_dir),
                duration_s=effective_duration,
                sampling_fps=sampling_fps,
            )
            if effective_duration <= 0.0:
                effective_duration = thumb_dur
        except Exception as exc:
            warnings.append(f"thumb_sampling_failed: {exc}")
    else:
        warnings.append("no_media_source_provided")

    if not sampled_frames:
        if not warnings:
            warnings.append("no_frames_sampled")
        return _degraded_result(
            duration_s=effective_duration,
            sampling_fps=sampling_fps,
            adaptive_applied=adaptive_applied,
            warnings=warnings,
            cfg_hash=cfg_hash,
        )

    active_detector: FaceDetector
    if detector is not None:
        active_detector = detector
    else:
        try:
            active_detector = create_face_detector(
                "auto",
                confidence_threshold=confidence_threshold,
            )
        except Exception as exc:
            warnings.append(f"face_detector_init_failed: {exc}")
            return _degraded_result(
                duration_s=effective_duration,
                sampling_fps=sampling_fps,
                adaptive_applied=adaptive_applied,
                warnings=warnings,
                cfg_hash=cfg_hash,
            )

    prev_tracks: dict[str, tuple[float, float]] = {}
    next_auto_id = 1
    prev_mouth_open: dict[str, float] = {}
    active_tid: str | None = None

    frame_records: list[dict[str, Any]] = []
    track_stats: dict[str, dict[str, Any]] = {}
    failed_timestamps: set[float] = set()
    successful_frames = 0

    for sf in sampled_frames:
        t = round(float(sf.timestamp), 3)
        try:
            raw_dets = active_detector.detect(sf.image, t)
        except Exception as exc:
            failed_timestamps.add(t)
            msg = f"face_detector_failed at t={t}: {exc}"
            if msg not in warnings:
                warnings.append(msg)
            continue

        successful_frames += 1
        confident: list[FaceDetection] = [
            d for d in (raw_dets or []) if float(d.confidence) >= confidence_threshold
        ]
        tracked, prev_tracks, next_auto_id = _assign_track_ids(
            confident,
            prev_tracks,
            next_auto_id,
        )

        face_records: list[dict[str, Any]] = []
        best_frame_tid: str | None = None
        best_frame_score = -1.0

        for tid, det in tracked:
            x, y, w, h = det.bbox_norm
            area = max(0.0, float(w)) * max(0.0, float(h))
            raw_landmarks = getattr(det, "landmarks", None)
            serialized_landmarks = _serialize_landmarks(raw_landmarks)

            mouth_motion = float(det.mouth_motion)
            if mouth_motion == 0.0 and raw_landmarks is not None:
                mouth_motion, new_open = _mouth_proxy_from_landmarks(
                    raw_landmarks,
                    float(h),
                    prev_mouth_open.get(tid),
                )
                if new_open is not None:
                    prev_mouth_open[tid] = new_open

            spk_score = score_face_detection(det, is_active=(tid == active_tid))
            if spk_score > best_frame_score:
                best_frame_score = spk_score
                best_frame_tid = tid

            face_entry: dict[str, Any] = {
                "track_id": tid,
                "face_id": det.face_id,
                "bbox": [round(float(x), 6), round(float(y), 6), round(float(w), 6), round(float(h), 6)],
                "size": round(area, 6),
                "area": round(area, 6),
                "confidence": round(float(det.confidence), 4),
                "mouth_motion": round(max(0.0, mouth_motion), 4),
                "landmarks": serialized_landmarks,
            }
            face_records.append(face_entry)

            stat = track_stats.get(tid)
            if stat is None:
                stat = {
                    "track_id": tid,
                    "frame_count": 0,
                    "total_area": 0.0,
                    "total_mouth_motion": 0.0,
                    "speaker_score": 0.0,
                    "first_seen": t,
                    "last_seen": t,
                }
                track_stats[tid] = stat
            stat["frame_count"] += 1
            stat["total_area"] += area
            stat["total_mouth_motion"] += max(0.0, mouth_motion)
            stat["speaker_score"] += area * (0.5 + max(0.0, mouth_motion))
            stat["last_seen"] = t

        if best_frame_tid is not None:
            active_tid = best_frame_tid

        frame_records.append(
            {
                "t": t,
                "face_count": len(face_records),
                "faces": face_records,
                "max_face_size": round(max((f["size"] for f in face_records), default=0.0), 6),
                "dominant_track_id": best_frame_tid,
            }
        )

    total_attempted = len(sampled_frames)
    if successful_frames == 0 or len(failed_timestamps) >= max(1, (total_attempted + 1) // 2) and failed_timestamps:
        return _degraded_result(
            duration_s=effective_duration,
            sampling_fps=sampling_fps,
            adaptive_applied=adaptive_applied,
            warnings=warnings or ["face_detector_failed"],
            cfg_hash=cfg_hash,
        )

    status = "degraded" if failed_timestamps else "ok"

    track_summaries: dict[str, dict[str, Any]] = {}
    for tid, stat in track_stats.items():
        cnt = int(stat["frame_count"])
        track_summaries[tid] = {
            "track_id": tid,
            "frame_count": cnt,
            "presence_ratio": round(cnt / max(1, successful_frames), 4),
            "mean_face_size": round(stat["total_area"] / max(1, cnt), 6),
            "mean_mouth_motion": round(stat["total_mouth_motion"] / max(1, cnt), 4),
            "speaker_score": round(stat["speaker_score"], 6),
            "first_seen": stat["first_seen"],
            "last_seen": stat["last_seen"],
        }

    dominant_speaker_track_ids = [
        tid
        for tid, _ in sorted(
            track_summaries.items(),
            key=lambda kv: (-float(kv[1]["speaker_score"]), -int(kv[1]["frame_count"]), kv[0]),
        )
    ]

    series = _build_1hz_presence_series(
        frame_records,
        duration_s=effective_duration,
        sampling_fps=sampling_fps,
        failed_timestamps=failed_timestamps,
    )

    overall_ratio = round(
        sum(1 for f in frame_records if int(f["face_count"]) > 0) / max(1, len(frame_records)),
        4,
    )
    mean_face_count = round(
        sum(int(f["face_count"]) for f in frame_records) / max(1, len(frame_records)),
        4,
    )
    max_face_count = max((int(f["face_count"]) for f in frame_records), default=0)

    active_speakers = sum(
        1
        for s in track_summaries.values()
        if float(s["presence_ratio"]) >= 0.10 or int(s["frame_count"]) >= 2
    )
    speakers_estimated = active_speakers if active_speakers > 0 else len(track_summaries)

    return {
        "schema_version": 1,
        "produced_by": {
            "stage": "analyze_faces",
            "version": "1.0.0",
            "config_hash": cfg_hash,
        },
        "status": status,
        "hz": 1,
        "duration_s": round(effective_duration, 3),
        "sampling_fps": round(sampling_fps, 3),
        "adaptive_fps_applied": adaptive_applied,
        "frame_count": len(frame_records),
        "frames": frame_records,
        "face_presence_series": series,
        "dominant_speaker_track_ids": dominant_speaker_track_ids,
        "dominant_track_id": dominant_speaker_track_ids[0] if dominant_speaker_track_ids else None,
        "tracks": track_summaries,
        "summary": {
            "face_presence_ratio": overall_ratio,
            "mean_face_count": mean_face_count,
            "max_face_count": max_face_count,
            "unique_track_count": len(track_summaries),
            "speakers_estimated": speakers_estimated,
        },
        "warnings": warnings,
    }
