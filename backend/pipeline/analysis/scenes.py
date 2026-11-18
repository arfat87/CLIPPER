from __future__ import annotations

import importlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Literal

import numpy as np

DEFAULT_SCENE_THRESHOLD: float = 27.0
DEFAULT_MIN_SCENE_LEN_S: float = 0.8
_FRAME_WIDTH = 96
_FRAME_HEIGHT = 54
_MAX_SAMPLE_FPS = 10.0

SceneBackend = Literal["auto", "scenedetect", "frame_diff"]

__all__ = [
    "DEFAULT_MIN_SCENE_LEN_S",
    "DEFAULT_SCENE_THRESHOLD",
    "SceneDetectionError",
    "build_scenes_document",
    "detect_scenes",
    "probe_video_stream",
    "scene_density_1hz",
]


class SceneDetectionError(RuntimeError):
    """Typed error raised when video scene detection fails."""


def _parse_fps(rate_str: str | None) -> float:
    if not rate_str or rate_str in ("0/0", "0"):
        return 0.0
    if "/" in rate_str:
        num_s, den_s = rate_str.split("/", 1)
        try:
            num = float(num_s)
            den = float(den_s)
            if den > 0.0 and num > 0.0:
                return num / den
        except ValueError:
            return 0.0
        return 0.0
    try:
        val = float(rate_str)
        return val if val > 0.0 else 0.0
    except ValueError:
        return 0.0


def probe_video_stream(video_path: Path) -> dict[str, Any]:
    """Probe video stream duration, fps, and dimensions via ffprobe."""
    path = Path(video_path)
    if not path.exists() or not path.is_file():
        raise SceneDetectionError(f"Video file not found: {path}")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        raise SceneDetectionError(f"ffprobe invocation failed: {exc}") from exc

    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        raise SceneDetectionError(f"ffprobe failed for {path.name}: {err}")

    try:
        info = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SceneDetectionError(f"Invalid ffprobe JSON output: {exc}") from exc

    streams = info.get("streams") or []
    if not streams:
        raise SceneDetectionError(f"No video stream found in {path.name}")

    stream = streams[0]
    raw_dur = stream.get("duration") or (info.get("format") or {}).get("duration")
    try:
        duration = float(raw_dur) if raw_dur is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0

    fps = _parse_fps(stream.get("avg_frame_rate")) or _parse_fps(stream.get("r_frame_rate")) or 24.0
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    return {
        "duration": max(0.0, duration),
        "fps": max(1.0, fps),
        "width": max(0, width),
        "height": max(0, height),
    }


def _detect_with_scenedetect(
    video_path: Path,
    *,
    threshold: float,
    min_scene_len_s: float,
    fps: float,
) -> list[dict[str, Any]] | None:
    try:
        scenedetect = importlib.import_module("scenedetect")
        detectors = importlib.import_module("scenedetect.detectors")
    except Exception:
        return None

    open_video = getattr(scenedetect, "open_video", None)
    scene_manager_cls = getattr(scenedetect, "SceneManager", None)
    content_detector_cls = getattr(detectors, "ContentDetector", None)
    if not (callable(open_video) and callable(scene_manager_cls) and callable(content_detector_cls)):
        return None

    video = open_video(str(video_path))
    manager = scene_manager_cls()
    min_scene_frames = max(1, int(round(min_scene_len_s * max(1.0, fps))))
    manager.add_detector(content_detector_cls(threshold=threshold, min_scene_len=min_scene_frames))
    manager.detect_scenes(video)
    scene_list = manager.get_scene_list()
    if not scene_list:
        return []

    scenes: list[dict[str, Any]] = []
    for start_tc, end_tc in scene_list:
        start_s = round(max(0.0, float(start_tc.get_seconds())), 3)
        end_s = round(max(start_s, float(end_tc.get_seconds())), 3)
        scenes.append({"start": start_s, "end": end_s, "type": "cut"})
    return scenes


def _sample_rgb_frames_ffmpeg(
    video_path: Path,
    *,
    sample_fps: float,
    width: int = _FRAME_WIDTH,
    height: int = _FRAME_HEIGHT,
) -> np.ndarray:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps={sample_fps:.4f},scale={width}:{height}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise SceneDetectionError(f"ffmpeg frame sampling failed: {err}")

    frame_bytes = width * height * 3
    buf = proc.stdout
    n_frames = len(buf) // frame_bytes
    if n_frames <= 0:
        return np.empty((0, height, width, 3), dtype=np.uint8)

    usable = n_frames * frame_bytes
    arr = np.frombuffer(buf[:usable], dtype=np.uint8)
    return arr.reshape((n_frames, height, width, 3))


def _frame_diff_scores(frames: np.ndarray) -> np.ndarray:
    if len(frames) < 2:
        return np.empty((0,), dtype=np.float32)

    f = frames.astype(np.float32)
    # Pixel-wise RGB mean absolute difference (0..255)
    mad_scores = np.mean(np.abs(f[1:] - f[:-1]), axis=(1, 2, 3))

    # 16-bin luminance histogram L1 distance scaled to 0..100
    luma = 0.299 * f[:, :, :, 0] + 0.587 * f[:, :, :, 1] + 0.114 * f[:, :, :, 2]
    hist_scores = np.zeros(len(frames) - 1, dtype=np.float32)
    prev_hist = np.histogram(luma[0], bins=16, range=(0.0, 256.0))[0].astype(np.float32)
    prev_hist /= max(1.0, float(prev_hist.sum()))
    for idx in range(1, len(frames)):
        cur_hist = np.histogram(luma[idx], bins=16, range=(0.0, 256.0))[0].astype(np.float32)
        cur_hist /= max(1.0, float(cur_hist.sum()))
        hist_scores[idx - 1] = 50.0 * float(np.sum(np.abs(cur_hist - prev_hist)))
        prev_hist = cur_hist

    return np.maximum(mad_scores, hist_scores)


def _detect_with_frame_diff(
    video_path: Path,
    *,
    duration: float,
    fps: float,
    threshold: float,
    min_scene_len_s: float,
) -> list[dict[str, Any]]:
    sample_fps = min(_MAX_SAMPLE_FPS, max(2.0, fps))
    frames = _sample_rgb_frames_ffmpeg(video_path, sample_fps=sample_fps)
    if len(frames) < 2:
        return [{"start": 0.0, "end": round(duration, 3), "type": "cut"}]

    scores = _frame_diff_scores(frames)
    # Frame index i+1 corresponds to time (i + 1) / sample_fps
    cuts: list[tuple[float, str]] = []
    last_cut_t = 0.0
    n_scores = len(scores)
    i = 0
    while i < n_scores:
        score = float(scores[i])
        if score >= threshold:
            # Determine contiguous elevated span to distinguish sharp cuts from gradual fades
            j = i + 1
            while j < n_scores and float(scores[j]) >= max(8.0, threshold * 0.35):
                j += 1
            span_frames = j - i
            span_s = span_frames / sample_fps

            if span_frames >= 3 and span_s >= 0.25:
                peak_idx = i + int(np.argmax(scores[i:j]))
                cut_t = (peak_idx + 1) / sample_fps
                cut_type = "fade"
            else:
                peak_idx = i + int(np.argmax(scores[i:j]))
                cut_t = (peak_idx + 1) / sample_fps
                cut_type = "cut"

            if (
                cut_t - last_cut_t >= min_scene_len_s
                and duration - cut_t >= min_scene_len_s * 0.5
            ):
                cuts.append((round(cut_t, 3), cut_type))
                last_cut_t = cut_t
            i = j
        else:
            i += 1

    if not cuts:
        return [{"start": 0.0, "end": round(duration, 3), "type": "cut"}]

    scenes: list[dict[str, Any]] = []
    prev_t = 0.0
    prev_type = "cut"
    for cut_t, cut_type in cuts:
        if cut_t > prev_t:
            scenes.append({"start": round(prev_t, 3), "end": round(cut_t, 3), "type": prev_type})
            prev_t = cut_t
            prev_type = cut_type

    dur_r = round(duration, 3)
    if dur_r > prev_t:
        scenes.append({"start": round(prev_t, 3), "end": dur_r, "type": prev_type})

    return scenes or [{"start": 0.0, "end": dur_r, "type": "cut"}]


def detect_scenes(
    video_path: Path,
    *,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
    min_scene_len_s: float = DEFAULT_MIN_SCENE_LEN_S,
    backend: SceneBackend = "auto",
    warnings_out: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Detect scene cuts/fades using PySceneDetect if available or FFmpeg+NumPy frame diff fallback."""
    path = Path(video_path)
    try:
        probe = probe_video_stream(path)
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"Scene detection probe failed ({exc}); returning single fallback scene.")
        return [{"start": 0.0, "end": 0.0, "type": "cut"}]

    duration = float(probe["duration"])
    fps = float(probe["fps"])
    if duration <= 0.0:
        if warnings_out is not None:
            warnings_out.append(f"Video {path.name} reported zero duration; returning single scene.")
        return [{"start": 0.0, "end": 0.0, "type": "cut"}]

    if duration <= min_scene_len_s:
        return [{"start": 0.0, "end": round(duration, 3), "type": "cut"}]

    if backend in ("auto", "scenedetect"):
        try:
            sd_scenes = _detect_with_scenedetect(
                path,
                threshold=threshold,
                min_scene_len_s=min_scene_len_s,
                fps=fps,
            )
            if sd_scenes:
                return sd_scenes
        except Exception as exc:
            if warnings_out is not None:
                warnings_out.append(
                    f"PySceneDetect failed ({exc}); falling back to FFmpeg frame-diff detector."
                )
            if backend == "scenedetect":
                return [{"start": 0.0, "end": round(duration, 3), "type": "cut"}]

    try:
        return _detect_with_frame_diff(
            path,
            duration=duration,
            fps=fps,
            threshold=threshold,
            min_scene_len_s=min_scene_len_s,
        )
    except Exception as exc:
        if warnings_out is not None:
            warnings_out.append(f"Frame-diff scene detection failed ({exc}); returning single full scene.")
        return [{"start": 0.0, "end": round(duration, 3), "type": "cut"}]


def scene_density_1hz(scenes: list[dict[str, Any]], duration_s: float) -> list[float]:
    """Compute 1 Hz normalized scene-cut density series from detected scene intervals."""
    dur = max(0.0, float(duration_s))
    if dur <= 0.0:
        for sc in scenes:
            dur = max(dur, float(sc.get("end", 0.0) or 0.0))
    n_bins = max(1, int(math.ceil(dur))) if dur > 0.0 else 1
    counts = np.zeros(n_bins, dtype=np.float64)

    for sc in scenes:
        start_t = float(sc.get("start", 0.0) or 0.0)
        if start_t > 0.0:
            bin_idx = min(n_bins - 1, max(0, int(math.floor(start_t))))
            counts[bin_idx] += 1.0

    max_count = float(np.max(counts)) if len(counts) > 0 else 0.0
    if max_count <= 0.0:
        return [0.0] * n_bins

    return [round(float(val / max_count), 4) for val in counts]


def build_scenes_document(
    video_path: Path,
    *,
    threshold: float = DEFAULT_SCENE_THRESHOLD,
    min_scene_len_s: float = DEFAULT_MIN_SCENE_LEN_S,
    backend: SceneBackend = "auto",
) -> dict[str, Any]:
    """Build schema-tagged scenes.json payload with warnings."""
    warnings: list[str] = []
    scenes = detect_scenes(
        video_path,
        threshold=threshold,
        min_scene_len_s=min_scene_len_s,
        backend=backend,
        warnings_out=warnings,
    )
    return {
        "schema_version": 1,
        "scenes": scenes,
        "warnings": warnings,
    }
