from __future__ import annotations

import importlib
import importlib.util
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np

ASPECT_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}

DEFAULT_BASE_FPS = 5.0
DEFAULT_DENSE_FPS = 10.0
DEFAULT_DENSE_WINDOW_S = 1.0
DEFAULT_EMA_ALPHA = 0.15
DEFAULT_DEAD_ZONE = 0.05
DEFAULT_MAX_PAN_PER_S = 0.35
DEFAULT_HYSTERESIS_S = 1.0
DEFAULT_HEADROOM_RATIO = 0.40
DEFAULT_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_FALLBACK_TIMEOUT_S = 1.5
DEFAULT_CONTINUITY_BONUS = 1.25


class ReframeError(RuntimeError):
    """Typed reframe error with machine-readable error code."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message or code)


@dataclass(slots=True)
class FaceDetection:
    """Normalized face/subject detection on a single sampled frame."""

    bbox_norm: tuple[float, float, float, float]
    confidence: float
    mouth_motion: float = 0.0
    face_id: int | None = None

    @property
    def center(self) -> tuple[float, float]:
        x, y, w, h = self.bbox_norm
        return (x + w * 0.5, y + h * 0.5)

    @property
    def area(self) -> float:
        _, _, w, h = self.bbox_norm
        return max(0.0, float(w)) * max(0.0, float(h))


@dataclass(slots=True)
class SampledFrame:
    """Sampled RGB video frame with clip-relative timestamp in seconds."""

    timestamp: float
    image: np.ndarray


@dataclass(slots=True)
class CropBox:
    """Clamped crop window in source pixel and normalized coordinates."""

    x: float
    y: float
    w: float
    h: float
    x_norm: float
    y_norm: float
    w_norm: float
    h_norm: float
    cx: float
    cy: float
    zoom: float
    out_w: int
    out_h: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "x_norm": self.x_norm,
            "y_norm": self.y_norm,
            "w_norm": self.w_norm,
            "h_norm": self.h_norm,
            "cx": self.cx,
            "cy": self.cy,
            "zoom": self.zoom,
            "out_w": self.out_w,
            "out_h": self.out_h,
        }


@dataclass(slots=True)
class ReframeKeyframe:
    """Single keyframe in `reframe_path.json`."""

    t: float
    cx: float
    cy: float
    zoom: float = 1.0
    layout: str = "fill"

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": round(float(self.t), 3),
            "cx": round(float(self.cx), 6),
            "cy": round(float(self.cy), 6),
            "zoom": round(float(self.zoom), 4),
            "layout": self.layout,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReframeKeyframe:
        return cls(
            t=float(data["t"]),
            cx=float(data["cx"]),
            cy=float(data["cy"]),
            zoom=float(data.get("zoom", 1.0)),
            layout=str(data.get("layout", "fill")),
        )


@dataclass(slots=True)
class ReframePath:
    """Serializable reframe trajectory (`reframe_path.json`)."""

    schema_version: int = 1
    fps: int = 30
    source_w: int = 1920
    source_h: int = 1080
    out_aspect: str = "9:16"
    keyframes: list[ReframeKeyframe] = field(default_factory=list)
    cuts: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "fps": int(self.fps),
            "source_w": int(self.source_w),
            "source_h": int(self.source_h),
            "out_aspect": self.out_aspect,
            "keyframes": [kf.to_dict() for kf in self.keyframes],
            "cuts": [round(float(c), 3) for c in self.cuts],
            "warnings": list(self.warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save_json(self, path: Path) -> Path:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.to_json(), encoding="utf-8")
        return out_path

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReframePath:
        raw_keyframes = data.get("keyframes") or []
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            fps=int(data.get("fps", 30)),
            source_w=int(data["source_w"]),
            source_h=int(data["source_h"]),
            out_aspect=str(data.get("out_aspect", "9:16")),
            keyframes=[ReframeKeyframe.from_dict(kf) for kf in raw_keyframes],
            cuts=[float(c) for c in (data.get("cuts") or [])],
            warnings=[str(w) for w in (data.get("warnings") or [])],
        )

    @classmethod
    def from_json(cls, raw: str) -> ReframePath:
        data = json.loads(raw)
        if not isinstance(data, Mapping):
            raise ReframeError("INVALID_JSON", "reframe_path JSON must be an object")
        return cls.from_dict(data)

    @classmethod
    def load_json(cls, path: Path) -> ReframePath:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


@runtime_checkable
class FaceDetector(Protocol):
    """Protocol for frame-level face/subject detectors."""

    def detect(self, frame: np.ndarray, timestamp: float) -> list[FaceDetection]: ...


def _to_grayscale(frame: np.ndarray) -> np.ndarray:
    arr = np.asarray(frame)
    if arr.ndim == 2:
        return arr.astype(np.float32)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        rgb = arr[:, :, :3].astype(np.float32)
        return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    raise ReframeError("INVALID_FRAME", f"Unsupported frame shape: {arr.shape}")


class MotionBoxDetector:
    """Dependency-free NumPy motion-box detector for CPU-only / lightweight environments."""

    def __init__(
        self,
        *,
        diff_threshold: float = 18.0,
        min_area_norm: float = 0.004,
        max_area_norm: float = 0.75,
        cut_change_ratio: float = 0.65,
        confidence_base: float = 0.78,
    ) -> None:
        self.diff_threshold = float(diff_threshold)
        self.min_area_norm = float(min_area_norm)
        self.max_area_norm = float(max_area_norm)
        self.cut_change_ratio = float(cut_change_ratio)
        self.confidence_base = float(confidence_base)
        self._prev_gray: np.ndarray | None = None

    def reset(self) -> None:
        self._prev_gray = None

    def detect(self, frame: np.ndarray, timestamp: float) -> list[FaceDetection]:
        gray = _to_grayscale(frame)
        height, width = gray.shape
        if height < 2 or width < 2:
            return []

        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            return []

        diff = np.abs(gray - self._prev_gray)
        self._prev_gray = gray

        active = diff > self.diff_threshold
        active_ratio = float(np.mean(active))
        if active_ratio <= 0.0 or active_ratio > self.cut_change_ratio:
            return []

        row_counts = np.sum(active, axis=1)
        col_counts = np.sum(active, axis=0)
        min_row_px = max(2, int(width * 0.01))
        min_col_px = max(2, int(height * 0.01))

        ys = np.where(row_counts >= min_row_px)[0]
        xs = np.where(col_counts >= min_col_px)[0]
        if ys.size == 0 or xs.size == 0:
            ys, xs = np.where(active)
        if ys.size == 0 or xs.size == 0:
            return []

        y0 = int(ys[0])
        y1 = int(ys[-1]) + 1
        x0 = int(xs[0])
        x1 = int(xs[-1]) + 1

        x_norm = max(0.0, min(1.0, x0 / width))
        y_norm = max(0.0, min(1.0, y0 / height))
        w_norm = max(0.0, min(1.0 - x_norm, (x1 - x0) / width))
        h_norm = max(0.0, min(1.0 - y_norm, (y1 - y0) / height))
        box_area = w_norm * h_norm
        if box_area < self.min_area_norm or box_area > self.max_area_norm:
            return []

        lower_y0 = y0 + max(0, (y1 - y0) // 2)
        lower_patch = diff[lower_y0:y1, x0:x1]
        mouth_motion = float(np.mean(lower_patch) / 255.0) if lower_patch.size > 0 else 0.0

        box_density = float(np.mean(active[y0:y1, x0:x1]))
        confidence = min(0.99, self.confidence_base + 0.2 * min(1.0, box_density * 2.0))

        return [
            FaceDetection(
                bbox_norm=(x_norm, y_norm, w_norm, h_norm),
                confidence=round(confidence, 4),
                mouth_motion=round(mouth_motion, 4),
                face_id=1,
            )
        ]


class MediaPipeFaceDetector:
    """Optional MediaPipe face detector adapter (loaded lazily when installed)."""

    def __init__(self, *, min_detection_confidence: float = 0.5, max_num_faces: int = 4) -> None:
        self.min_detection_confidence = float(min_detection_confidence)
        self.max_num_faces = int(max_num_faces)
        self._face_mesh: Any = None
        self._face_detection: Any = None
        self._prev_mouth_open: dict[int, float] = {}
        self._prev_centers: dict[int, tuple[float, float]] = {}
        self._next_id = 1

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("mediapipe") is not None

    def _ensure_initialized(self) -> None:
        if self._face_mesh is not None or self._face_detection is not None:
            return
        if not self.is_available():
            raise ReframeError("DETECTOR_UNAVAILABLE", "mediapipe is not installed")
        mp = importlib.import_module("mediapipe")
        solutions = getattr(mp, "solutions", None)
        if solutions is None:
            raise ReframeError("DETECTOR_UNAVAILABLE", "mediapipe.solutions is unavailable")
        if hasattr(solutions, "face_mesh"):
            self._face_mesh = solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=self.max_num_faces,
                refine_landmarks=False,
                min_detection_confidence=self.min_detection_confidence,
            )
        elif hasattr(solutions, "face_detection"):
            self._face_detection = solutions.face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=self.min_detection_confidence,
            )
        else:
            raise ReframeError("DETECTOR_UNAVAILABLE", "mediapipe face modules unavailable")

    def _match_face_id(self, cx: float, cy: float) -> int:
        best_id: int | None = None
        best_dist = 0.20
        for fid, (px, py) in self._prev_centers.items():
            dist = math.hypot(cx - px, cy - py)
            if dist < best_dist:
                best_dist = dist
                best_id = fid
        if best_id is None:
            best_id = self._next_id
            self._next_id += 1
        return best_id

    def detect(self, frame: np.ndarray, timestamp: float) -> list[FaceDetection]:
        self._ensure_initialized()
        rgb = np.asarray(frame)
        if rgb.ndim != 3 or rgb.shape[2] < 3:
            raise ReframeError("INVALID_FRAME", f"Expected RGB frame, got shape {rgb.shape}")
        rgb_img = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8)

        detections: list[FaceDetection] = []
        new_centers: dict[int, tuple[float, float]] = {}

        if self._face_mesh is not None:
            result = self._face_mesh.process(rgb_img)
            landmarks_list = getattr(result, "multi_face_landmarks", None) or []
            for face_lm in landmarks_list:
                xs = [float(lm.x) for lm in face_lm.landmark]
                ys = [float(lm.y) for lm in face_lm.landmark]
                if not xs or not ys:
                    continue
                x0 = max(0.0, min(1.0, min(xs)))
                y0 = max(0.0, min(1.0, min(ys)))
                x1 = max(x0, min(1.0, max(xs)))
                y1 = max(y0, min(1.0, max(ys)))
                w = x1 - x0
                h = y1 - y0
                if w <= 0.0 or h <= 0.0:
                    continue

                cx, cy = x0 + w * 0.5, y0 + h * 0.5
                fid = self._match_face_id(cx, cy)
                new_centers[fid] = (cx, cy)

                mouth_motion = 0.0
                if len(face_lm.landmark) > 14:
                    upper_lip = face_lm.landmark[13]
                    lower_lip = face_lm.landmark[14]
                    mouth_open = abs(float(lower_lip.y) - float(upper_lip.y)) / max(h, 1e-4)
                    prev_open = self._prev_mouth_open.get(fid, mouth_open)
                    mouth_motion = min(1.0, abs(mouth_open - prev_open) * 10.0)
                    self._prev_mouth_open[fid] = mouth_open

                detections.append(
                    FaceDetection(
                        bbox_norm=(x0, y0, w, h),
                        confidence=0.9,
                        mouth_motion=round(mouth_motion, 4),
                        face_id=fid,
                    )
                )
        elif self._face_detection is not None:
            result = self._face_detection.process(rgb_img)
            raw_dets = getattr(result, "detections", None) or []
            for det in raw_dets:
                rel = det.location_data.relative_bounding_box
                x0 = max(0.0, min(1.0, float(rel.xmin)))
                y0 = max(0.0, min(1.0, float(rel.ymin)))
                w = max(0.0, min(1.0 - x0, float(rel.width)))
                h = max(0.0, min(1.0 - y0, float(rel.height)))
                if w <= 0.0 or h <= 0.0:
                    continue
                score = float(det.score[0]) if det.score else 0.75
                cx, cy = x0 + w * 0.5, y0 + h * 0.5
                fid = self._match_face_id(cx, cy)
                new_centers[fid] = (cx, cy)
                detections.append(
                    FaceDetection(
                        bbox_norm=(x0, y0, w, h),
                        confidence=score,
                        mouth_motion=0.0,
                        face_id=fid,
                    )
                )

        if new_centers:
            self._prev_centers = new_centers
        return detections


class OpenCVFaceDetector:
    """Optional OpenCV Haar/DNN face detector adapter (loaded lazily when installed)."""

    def __init__(
        self,
        *,
        dnn_prototxt: Path | None = None,
        dnn_model: Path | None = None,
        confidence_threshold: float = 0.5,
    ) -> None:
        self.dnn_prototxt = Path(dnn_prototxt) if dnn_prototxt else None
        self.dnn_model = Path(dnn_model) if dnn_model else None
        self.confidence_threshold = float(confidence_threshold)
        self._net: Any = None
        self._cascade: Any = None
        self._prev_gray: np.ndarray | None = None

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("cv2") is not None

    def _ensure_initialized(self) -> Any:
        if not self.is_available():
            raise ReframeError("DETECTOR_UNAVAILABLE", "opencv-python (cv2) is not installed")
        cv2 = importlib.import_module("cv2")
        if self._net is None and self._cascade is None:
            if (
                self.dnn_prototxt is not None
                and self.dnn_model is not None
                and self.dnn_prototxt.exists()
                and self.dnn_model.exists()
            ):
                self._net = cv2.dnn.readNetFromCaffe(str(self.dnn_prototxt), str(self.dnn_model))
            else:
                cascade_path = str(
                    Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
                )
                cascade = cv2.CascadeClassifier(cascade_path)
                if cascade.empty():
                    raise ReframeError(
                        "DETECTOR_UNAVAILABLE",
                        f"Failed to load OpenCV cascade: {cascade_path}",
                    )
                self._cascade = cascade
        return cv2

    def detect(self, frame: np.ndarray, timestamp: float) -> list[FaceDetection]:
        cv2 = self._ensure_initialized()
        arr = np.asarray(frame)
        if arr.ndim != 3 or arr.shape[2] < 3:
            raise ReframeError("INVALID_FRAME", f"Expected RGB frame, got shape {arr.shape}")

        height, width = arr.shape[:2]
        gray = _to_grayscale(arr)
        prev_gray = self._prev_gray
        self._prev_gray = gray

        detections: list[FaceDetection] = []
        if self._net is not None:
            bgr = arr[:, :, [2, 1, 0]]
            blob = cv2.dnn.blobFromImage(bgr, 1.0, (300, 300), (104.0, 177.0, 123.0))
            self._net.setInput(blob)
            out = self._net.forward()
            for i in range(out.shape[2]):
                conf = float(out[0, 0, i, 2])
                if conf < self.confidence_threshold:
                    continue
                x0 = max(0.0, min(1.0, float(out[0, 0, i, 3])))
                y0 = max(0.0, min(1.0, float(out[0, 0, i, 4])))
                x1 = max(x0, min(1.0, float(out[0, 0, i, 5])))
                y1 = max(y0, min(1.0, float(out[0, 0, i, 6])))
                w_norm, h_norm = x1 - x0, y1 - y0
                if w_norm <= 0.0 or h_norm <= 0.0:
                    continue
                mouth_motion = _estimate_lower_box_motion(
                    gray, prev_gray, x0, y0, w_norm, h_norm
                )
                detections.append(
                    FaceDetection(
                        bbox_norm=(x0, y0, w_norm, h_norm),
                        confidence=conf,
                        mouth_motion=mouth_motion,
                        face_id=None,
                    )
                )
        elif self._cascade is not None:
            gray_u8 = np.clip(gray, 0, 255).astype(np.uint8)
            faces = self._cascade.detectMultiScale(
                gray_u8, scaleFactor=1.1, minNeighbors=4, minSize=(24, 24)
            )
            for fx, fy, fw, fh in faces:
                x0 = max(0.0, min(1.0, float(fx) / width))
                y0 = max(0.0, min(1.0, float(fy) / height))
                w_norm = max(0.0, min(1.0 - x0, float(fw) / width))
                h_norm = max(0.0, min(1.0 - y0, float(fh) / height))
                if w_norm <= 0.0 or h_norm <= 0.0:
                    continue
                mouth_motion = _estimate_lower_box_motion(
                    gray, prev_gray, x0, y0, w_norm, h_norm
                )
                detections.append(
                    FaceDetection(
                        bbox_norm=(x0, y0, w_norm, h_norm),
                        confidence=0.82,
                        mouth_motion=mouth_motion,
                        face_id=None,
                    )
                )

        return detections


def _estimate_lower_box_motion(
    gray: np.ndarray,
    prev_gray: np.ndarray | None,
    x_norm: float,
    y_norm: float,
    w_norm: float,
    h_norm: float,
) -> float:
    if prev_gray is None or prev_gray.shape != gray.shape:
        return 0.0
    height, width = gray.shape
    x0 = max(0, int(x_norm * width))
    x1 = min(width, max(x0 + 1, int((x_norm + w_norm) * width)))
    y0 = max(0, int((y_norm + h_norm * 0.55) * height))
    y1 = min(height, max(y0 + 1, int((y_norm + h_norm) * height)))
    patch = np.abs(gray[y0:y1, x0:x1] - prev_gray[y0:y1, x0:x1])
    return round(float(np.mean(patch) / 255.0), 4) if patch.size > 0 else 0.0


class _CascadeFaceDetector:
    """Auto detector: MediaPipe -> OpenCV -> dependency-free MotionBoxDetector."""

    def __init__(self, *, confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD) -> None:
        self.confidence_threshold = float(confidence_threshold)
        self._mp = MediaPipeFaceDetector() if MediaPipeFaceDetector.is_available() else None
        self._cv = OpenCVFaceDetector() if OpenCVFaceDetector.is_available() else None
        self._motion = MotionBoxDetector()

    def detect(self, frame: np.ndarray, timestamp: float) -> list[FaceDetection]:
        if self._mp is not None:
            dets = self._mp.detect(frame, timestamp)
            if any(d.confidence >= self.confidence_threshold for d in dets):
                return dets
        if self._cv is not None:
            dets = self._cv.detect(frame, timestamp)
            if any(d.confidence >= self.confidence_threshold for d in dets):
                return dets
        return self._motion.detect(frame, timestamp)


def create_face_detector(
    backend: Literal["auto", "mediapipe", "opencv", "motion_box"] = "auto",
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> FaceDetector:
    """Create a face detector backend, defaulting to auto fallback without required extras."""
    if backend == "auto":
        return _CascadeFaceDetector(confidence_threshold=confidence_threshold)
    if backend == "mediapipe":
        return MediaPipeFaceDetector(min_detection_confidence=confidence_threshold)
    if backend == "opencv":
        return OpenCVFaceDetector(confidence_threshold=confidence_threshold)
    if backend == "motion_box":
        return MotionBoxDetector()
    raise ReframeError("INVALID_DETECTOR", f"Unsupported detector backend: {backend}")


def _parse_rate(rate_str: str | None) -> float:
    if not rate_str or rate_str == "0/0":
        return 0.0
    if "/" in rate_str:
        num_s, den_s = rate_str.split("/", 1)
        try:
            den = float(den_s)
            return float(num_s) / den if den != 0 else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate_str)
    except ValueError:
        return 0.0


def probe_video_metadata(video_path: Path) -> dict[str, Any]:
    """Probe width, height, fps, and duration of a video via ffprobe."""
    src = Path(video_path)
    if not src.exists() or not src.is_file():
        raise ReframeError("SOURCE_NOT_FOUND", f"Video file not found: {src}")

    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(src),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit code {proc.returncode}"
        raise ReframeError("CORRUPT_MEDIA", f"ffprobe failed for {src.name}: {detail}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ReframeError("CORRUPT_MEDIA", f"Invalid ffprobe output: {exc}") from exc

    streams = data.get("streams") or []
    video_stream = next(
        (
            s
            for s in streams
            if isinstance(s, dict)
            and s.get("codec_type") == "video"
            and int(s.get("width") or 0) > 0
            and int(s.get("height") or 0) > 0
        ),
        None,
    )
    if video_stream is None:
        raise ReframeError("NO_VIDEO_STREAM", f"No video stream found in {src}")

    width = int(video_stream["width"])
    height = int(video_stream["height"])
    fps = _parse_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")) or 30.0
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
    return {
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "duration": round(duration, 3),
    }


def sample_clip_frames(
    video_path: Path,
    *,
    start: float = 0.0,
    end: float | None = None,
    base_fps: float = DEFAULT_BASE_FPS,
    dense_fps: float = DEFAULT_DENSE_FPS,
    cuts: Sequence[float] = (),
    dense_window_s: float = DEFAULT_DENSE_WINDOW_S,
    analysis_width: int = 320,
) -> list[SampledFrame]:
    """Sample RGB frames from a clip via FFmpeg at `base_fps` (and `dense_fps` around cuts)."""
    if base_fps <= 0 or dense_fps <= 0:
        raise ReframeError("INVALID_ARGS", "base_fps and dense_fps must be > 0")
    if dense_fps < base_fps:
        dense_fps = base_fps

    meta = probe_video_metadata(video_path)
    src_w = int(meta["width"])
    src_h = int(meta["height"])

    scale_w = max(2, (min(src_w, analysis_width) // 2) * 2)
    scale_h = max(2, (int(round(src_h * (scale_w / src_w))) // 2) * 2)

    cmd = ["ffmpeg", "-v", "error"]
    if start > 0.0:
        cmd.extend(["-ss", f"{start:.3f}"])
    cmd.extend(["-i", str(video_path)])
    if end is not None:
        clip_dur = max(0.0, float(end) - float(start))
        if clip_dur <= 0.0:
            raise ReframeError("INVALID_ARGS", "Clip end must be greater than start")
        cmd.extend(["-t", f"{clip_dur:.3f}"])

    cmd.extend(
        [
            "-vf",
            f"fps={dense_fps:.6f},scale={scale_w}:{scale_h}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        stderr_txt = proc.stderr.decode("utf-8", errors="replace").strip()
        raise ReframeError("FRAME_SAMPLE_FAILED", f"FFmpeg frame sampling failed: {stderr_txt}")

    raw = proc.stdout
    frame_size = scale_w * scale_h * 3
    if frame_size <= 0 or len(raw) < frame_size:
        return []

    n_frames = len(raw) // frame_size
    cut_list = [float(c) for c in cuts]
    base_dt = 1.0 / base_fps
    dense_dt = 1.0 / dense_fps

    sampled: list[SampledFrame] = []
    last_kept_t = -1e9
    for idx in range(n_frames):
        t = round(idx / dense_fps, 3)
        near_cut = any(abs(t - c) <= dense_window_s + 1e-6 for c in cut_list)
        min_step = dense_dt if near_cut else base_dt
        if idx > 0 and (t - last_kept_t) < min_step - 1e-6:
            continue

        offset = idx * frame_size
        arr = (
            np.frombuffer(raw, dtype=np.uint8, count=frame_size, offset=offset)
            .reshape((scale_h, scale_w, 3))
            .copy()
        )
        sampled.append(SampledFrame(timestamp=t, image=arr))
        last_kept_t = t

    return sampled


def _validate_aspect(out_aspect: str) -> tuple[int, int]:
    if out_aspect not in ASPECT_RESOLUTIONS:
        raise ReframeError(
            "INVALID_ASPECT",
            f"Unsupported aspect ratio {out_aspect!r}; expected one of {tuple(ASPECT_RESOLUTIONS)}",
        )
    return ASPECT_RESOLUTIONS[out_aspect]


def _base_crop_dims(
    source_w: int,
    source_h: int,
    out_aspect: str,
    zoom: float,
) -> tuple[float, float, int, int]:
    if source_w <= 0 or source_h <= 0:
        raise ReframeError("INVALID_DIMENSIONS", f"Invalid source size: {source_w}x{source_h}")
    out_w, out_h = _validate_aspect(out_aspect)
    safe_zoom = max(1.0, float(zoom))
    if not math.isfinite(safe_zoom):
        safe_zoom = 1.0

    target_ratio = out_w / out_h
    src_ratio = source_w / source_h
    if src_ratio >= target_ratio:
        base_h = float(source_h)
        base_w = base_h * target_ratio
    else:
        base_w = float(source_w)
        base_h = base_w / target_ratio

    crop_w = min(float(source_w), base_w / safe_zoom)
    crop_h = min(float(source_h), base_h / safe_zoom)
    return crop_w, crop_h, out_w, out_h


def clamp_center(
    cx: float,
    cy: float,
    zoom: float,
    source_w: int,
    source_h: int,
    out_aspect: str = "9:16",
) -> tuple[float, float]:
    """Clamp normalized center `(cx, cy)` so crop window stays inside `[0, source_w] x [0, source_h]`."""
    crop_w, crop_h, _, _ = _base_crop_dims(source_w, source_h, out_aspect, zoom)
    half_w_norm = (crop_w / source_w) * 0.5
    half_h_norm = (crop_h / source_h) * 0.5

    safe_cx = 0.5 if not math.isfinite(cx) else float(cx)
    safe_cy = 0.5 if not math.isfinite(cy) else float(cy)

    min_cx = min(0.5, half_w_norm)
    max_cx = max(0.5, 1.0 - half_w_norm)
    min_cy = min(0.5, half_h_norm)
    max_cy = max(0.5, 1.0 - half_h_norm)

    clamped_cx = min(max(safe_cx, min_cx), max_cx)
    clamped_cy = min(max(safe_cy, min_cy), max_cy)
    return clamped_cx, clamped_cy


def compute_crop_box(
    cx: float,
    cy: float,
    zoom: float,
    source_w: int,
    source_h: int,
    out_aspect: str = "9:16",
) -> CropBox:
    """Compute strictly bounded pixel and normalized crop coordinates for target aspect ratio."""
    safe_zoom = max(1.0, float(zoom)) if math.isfinite(zoom) else 1.0
    crop_w, crop_h, out_w, out_h = _base_crop_dims(source_w, source_h, out_aspect, safe_zoom)
    clamped_cx, clamped_cy = clamp_center(cx, cy, safe_zoom, source_w, source_h, out_aspect)

    x = clamped_cx * source_w - crop_w * 0.5
    y = clamped_cy * source_h - crop_h * 0.5
    x = max(0.0, min(x, float(source_w) - crop_w))
    y = max(0.0, min(y, float(source_h) - crop_h))

    if x + crop_w > source_w:
        crop_w = max(0.0, float(source_w) - x)
    if y + crop_h > source_h:
        crop_h = max(0.0, float(source_h) - y)

    x_norm = max(0.0, min(1.0, x / source_w))
    y_norm = max(0.0, min(1.0, y / source_h))
    w_norm = max(0.0, min(1.0 - x_norm, crop_w / source_w))
    h_norm = max(0.0, min(1.0 - y_norm, crop_h / source_h))

    return CropBox(
        x=x,
        y=y,
        w=crop_w,
        h=crop_h,
        x_norm=x_norm,
        y_norm=y_norm,
        w_norm=w_norm,
        h_norm=h_norm,
        cx=clamped_cx,
        cy=clamped_cy,
        zoom=safe_zoom,
        out_w=out_w,
        out_h=out_h,
    )


def compute_headroom_target(
    bbox_norm: tuple[float, float, float, float],
    *,
    zoom: float = 1.0,
    source_w: int = 1920,
    source_h: int = 1080,
    out_aspect: str = "9:16",
    headroom_ratio: float = DEFAULT_HEADROOM_RATIO,
) -> tuple[float, float]:
    """Compute crop center `(cx, cy)` placing face center at `headroom_ratio` of crop height."""
    fx, fy, fw, fh = bbox_norm
    face_cx = fx + fw * 0.5
    face_cy = fy + fh * 0.5

    _, crop_h, _, _ = _base_crop_dims(source_w, source_h, out_aspect, zoom)
    crop_h_norm = crop_h / source_h
    target_cy = face_cy + (0.5 - headroom_ratio) * crop_h_norm
    return clamp_center(face_cx, target_cy, zoom, source_w, source_h, out_aspect)


def score_face_detection(
    det: FaceDetection,
    *,
    is_active: bool = False,
    continuity_bonus: float = DEFAULT_CONTINUITY_BONUS,
) -> float:
    """Active-speaker score: `face_area * (0.5 + mouth_motion) * continuity`."""
    continuity = continuity_bonus if is_active else 1.0
    return det.area * (0.5 + max(0.0, float(det.mouth_motion))) * continuity


def compute_jitter_metric(
    keyframes: Sequence[ReframeKeyframe | Mapping[str, Any]],
    cuts: Sequence[float] = (),
) -> float:
    """Measure mean frame-to-frame center delta inside continuous scenes (excluding cut jumps)."""
    if len(keyframes) < 2:
        return 0.0

    cut_list = [float(c) for c in cuts]
    deltas: list[float] = []
    for idx in range(1, len(keyframes)):
        prev_kf = keyframes[idx - 1]
        curr_kf = keyframes[idx]
        if isinstance(prev_kf, Mapping):
            t_prev, cx_prev, cy_prev = float(prev_kf["t"]), float(prev_kf["cx"]), float(prev_kf["cy"])
        else:
            t_prev, cx_prev, cy_prev = float(prev_kf.t), float(prev_kf.cx), float(prev_kf.cy)

        if isinstance(curr_kf, Mapping):
            t_curr, cx_curr, cy_curr = float(curr_kf["t"]), float(curr_kf["cx"]), float(curr_kf["cy"])
        else:
            t_curr, cx_curr, cy_curr = float(curr_kf.t), float(curr_kf.cx), float(curr_kf.cy)

        if any(t_prev < c <= t_curr for c in cut_list):
            continue
        deltas.append(math.hypot(cx_curr - cx_prev, cy_curr - cy_prev))

    if not deltas:
        return 0.0
    return float(sum(deltas) / len(deltas))


def _assign_track_ids(
    detections: Sequence[FaceDetection],
    prev_tracks: dict[str, tuple[float, float]],
    next_auto_id: int,
    max_match_dist: float = 0.20,
) -> tuple[list[tuple[str, FaceDetection]], dict[str, tuple[float, float]], int]:
    """Associate detections with persistent track IDs across consecutive frames."""
    tracked: list[tuple[str, FaceDetection]] = []
    new_tracks: dict[str, tuple[float, float]] = {}
    used_prev: set[str] = set()

    for det in detections:
        if det.face_id is not None:
            tid = f"id:{det.face_id}"
            tracked.append((tid, det))
            new_tracks[tid] = det.center
            used_prev.add(tid)
            continue

        cx, cy = det.center
        best_tid: str | None = None
        best_dist = max_match_dist
        for prev_tid, (px, py) in prev_tracks.items():
            if prev_tid in used_prev or prev_tid.startswith("id:"):
                continue
            dist = math.hypot(cx - px, cy - py)
            if dist < best_dist:
                best_dist = dist
                best_tid = prev_tid

        if best_tid is None:
            best_tid = f"auto:{next_auto_id}"
            next_auto_id += 1
        else:
            used_prev.add(best_tid)

        tracked.append((best_tid, det))
        new_tracks[best_tid] = (cx, cy)

    return tracked, new_tracks, next_auto_id


def build_reframe_path(
    *,
    video_path: Path | None = None,
    frames: Sequence[SampledFrame] | None = None,
    detections_by_frame: Sequence[tuple[float, Sequence[FaceDetection]]] | None = None,
    detector: FaceDetector | None = None,
    start: float = 0.0,
    end: float | None = None,
    cuts: Sequence[float] = (),
    speaker_changes: Sequence[float] = (),
    source_w: int | None = None,
    source_h: int | None = None,
    fps: int = 30,
    out_aspect: str = "9:16",
    force_layout: Literal["fill", "fit_blur"] | None = None,
    fallback_mode: Literal["fit_blur", "center"] = "fit_blur",
    base_fps: float = DEFAULT_BASE_FPS,
    dense_fps: float = DEFAULT_DENSE_FPS,
    dense_window_s: float = DEFAULT_DENSE_WINDOW_S,
    ema_alpha: float = DEFAULT_EMA_ALPHA,
    dead_zone: float = DEFAULT_DEAD_ZONE,
    max_pan_per_s: float = DEFAULT_MAX_PAN_PER_S,
    hysteresis_s: float = DEFAULT_HYSTERESIS_S,
    headroom_ratio: float = DEFAULT_HEADROOM_RATIO,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    fallback_timeout_s: float = DEFAULT_FALLBACK_TIMEOUT_S,
    continuity_bonus: float = DEFAULT_CONTINUITY_BONUS,
    emphasis_windows: Sequence[tuple[float, float]] = (),
    emphasis_zoom: float = 1.0,
) -> ReframePath:
    """Track active speaker, smooth crop trajectory, reset at scene cuts, and emit `ReframePath`."""
    _validate_aspect(out_aspect)
    if force_layout is not None and force_layout not in ("fill", "fit_blur"):
        raise ReframeError("INVALID_LAYOUT", f"Unsupported forced layout: {force_layout!r}")

    sorted_cuts = sorted({round(float(c), 3) for c in cuts if float(c) >= 0.0})
    dense_markers = sorted({*sorted_cuts, *(round(float(s), 3) for s in speaker_changes)})

    if detections_by_frame is not None:
        src_w = int(source_w or 1920)
        src_h = int(source_h or 1080)
        frame_detections: list[tuple[float, Sequence[FaceDetection]]] = [
            (float(t), list(dets)) for t, dets in detections_by_frame
        ]
    elif frames is not None:
        sampled_frames = list(frames)
        if source_w is not None and source_h is not None:
            src_w, src_h = int(source_w), int(source_h)
        elif sampled_frames:
            h_img, w_img = sampled_frames[0].image.shape[:2]
            src_w, src_h = int(source_w or w_img), int(source_h or h_img)
        else:
            src_w, src_h = int(source_w or 1920), int(source_h or 1080)

        active_detector = detector or create_face_detector(
            "auto", confidence_threshold=confidence_threshold
        )
        frame_detections = [
            (float(f.timestamp), active_detector.detect(f.image, float(f.timestamp)))
            for f in sampled_frames
        ]
    elif video_path is not None:
        meta = probe_video_metadata(video_path)
        src_w = int(source_w or meta["width"])
        src_h = int(source_h or meta["height"])
        sampled_frames = sample_clip_frames(
            video_path,
            start=start,
            end=end,
            base_fps=base_fps,
            dense_fps=dense_fps,
            cuts=dense_markers,
            dense_window_s=dense_window_s,
        )
        active_detector = detector or create_face_detector(
            "auto", confidence_threshold=confidence_threshold
        )
        frame_detections = [
            (float(f.timestamp), active_detector.detect(f.image, float(f.timestamp)))
            for f in sampled_frames
        ]
    else:
        raise ReframeError(
            "INVALID_ARGS",
            "Must provide one of `video_path`, `frames`, or `detections_by_frame`",
        )

    warnings: list[str] = []
    if src_h < 720:
        warnings.append(
            f"upscaling_warning: source height {src_h}px < 720px may degrade {out_aspect} output quality"
        )

    keyframes: list[ReframeKeyframe] = []

    ema_initialized = False
    curr_cx = 0.5
    curr_cy = 0.5
    curr_zoom = 1.0
    prev_t = 0.0

    active_track_id: str | None = None
    challenger_track_id: str | None = None
    challenger_since: float | None = None
    prev_tracks: dict[str, tuple[float, float]] = {}
    next_auto_id = 1
    low_conf_since: float | None = None
    last_good_target: tuple[float, float] | None = None

    ref_dt = 1.0 / max(base_fps, 1e-3)

    for idx, (t, raw_dets) in enumerate(frame_detections):
        is_cut = idx > 0 and any(prev_t < c <= t for c in sorted_cuts)
        if is_cut:
            ema_initialized = False
            active_track_id = None
            challenger_track_id = None
            challenger_since = None
            prev_tracks = {}
            low_conf_since = None
            last_good_target = None

        in_emphasis = emphasis_zoom > 1.0 and any(
            float(w_s) <= t <= float(w_e) for w_s, w_e in emphasis_windows
        )
        target_zoom = min(1.25, max(1.0, float(emphasis_zoom))) if in_emphasis else 1.0

        in_safety_blend = False
        if force_layout == "fit_blur":
            raw_cx, raw_cy = clamp_center(0.5, 0.5, 1.0, src_w, src_h, out_aspect)
            target_zoom = 1.0
            frame_layout = "fit_blur"
            in_safety_blend = True
        else:
            valid_dets = [d for d in raw_dets if d.confidence >= confidence_threshold]
            tracked, prev_tracks, next_auto_id = _assign_track_ids(
                valid_dets, prev_tracks, next_auto_id
            )

            if tracked:
                low_conf_since = None
                scored = [
                    (
                        score_face_detection(
                            det,
                            is_active=(tid == active_track_id),
                            continuity_bonus=continuity_bonus,
                        ),
                        tid,
                        det,
                    )
                    for tid, det in tracked
                ]
                scored.sort(key=lambda item: item[0], reverse=True)
                _, best_tid, best_det = scored[0]

                chosen_det: FaceDetection | None
                if active_track_id is None:
                    active_track_id = best_tid
                    chosen_det = best_det
                    challenger_track_id = None
                    challenger_since = None
                elif best_tid == active_track_id:
                    chosen_det = best_det
                    challenger_track_id = None
                    challenger_since = None
                else:
                    if challenger_track_id == best_tid:
                        since = challenger_since if challenger_since is not None else t
                        if (t - since) >= hysteresis_s - 1e-6:
                            active_track_id = best_tid
                            chosen_det = best_det
                            challenger_track_id = None
                            challenger_since = None
                        else:
                            chosen_det = next(
                                (d for tid, d in tracked if tid == active_track_id), None
                            )
                    else:
                        challenger_track_id = best_tid
                        challenger_since = t
                        chosen_det = next(
                            (d for tid, d in tracked if tid == active_track_id), None
                        )

                if chosen_det is not None:
                    raw_cx, raw_cy = compute_headroom_target(
                        chosen_det.bbox_norm,
                        zoom=target_zoom,
                        source_w=src_w,
                        source_h=src_h,
                        out_aspect=out_aspect,
                        headroom_ratio=headroom_ratio,
                    )
                    last_good_target = (raw_cx, raw_cy)
                elif last_good_target is not None:
                    raw_cx, raw_cy = last_good_target
                else:
                    raw_cx, raw_cy = clamp_center(0.5, 0.5, target_zoom, src_w, src_h, out_aspect)

                frame_layout = force_layout if force_layout is not None else "fill"
            else:
                challenger_track_id = None
                challenger_since = None
                if low_conf_since is None:
                    low_conf_since = t
                low_conf_dur = t - low_conf_since
                if low_conf_dur > fallback_timeout_s + 1e-6:
                    raw_cx, raw_cy = clamp_center(0.5, 0.5, 1.0, src_w, src_h, out_aspect)
                    target_zoom = 1.0
                    in_safety_blend = True
                    if force_layout == "fill" or fallback_mode == "center":
                        frame_layout = "fill"
                    else:
                        frame_layout = "fit_blur"
                else:
                    if last_good_target is not None:
                        raw_cx, raw_cy = last_good_target
                    else:
                        raw_cx, raw_cy = clamp_center(
                            0.5, 0.5, target_zoom, src_w, src_h, out_aspect
                        )
                    frame_layout = force_layout if force_layout is not None else "fill"

        raw_cx, raw_cy = clamp_center(raw_cx, raw_cy, target_zoom, src_w, src_h, out_aspect)

        if not ema_initialized:
            curr_cx, curr_cy, curr_zoom = raw_cx, raw_cy, target_zoom
            ema_initialized = True
        else:
            dt = max(1e-4, t - prev_t)
            alpha_step = 1.0 - (1.0 - ema_alpha) ** (dt / ref_dt)
            alpha_step = max(0.0, min(1.0, alpha_step))

            dx = raw_cx - curr_cx
            dy = raw_cy - curr_cy
            if in_safety_blend:
                eff_cx = raw_cx
                eff_cy = raw_cy
            else:
                eff_cx = curr_cx if abs(dx) <= dead_zone else raw_cx
                eff_cy = curr_cy if abs(dy) <= dead_zone else raw_cy

            next_cx = curr_cx + alpha_step * (eff_cx - curr_cx)
            next_cy = curr_cy + alpha_step * (eff_cy - curr_cy)
            next_zoom = curr_zoom + alpha_step * (target_zoom - curr_zoom)

            max_step = max_pan_per_s * dt
            curr_cx = curr_cx + max(-max_step, min(max_step, next_cx - curr_cx))
            curr_cy = curr_cy + max(-max_step, min(max_step, next_cy - curr_cy))
            curr_zoom = next_zoom

        curr_cx, curr_cy = clamp_center(curr_cx, curr_cy, curr_zoom, src_w, src_h, out_aspect)
        keyframes.append(
            ReframeKeyframe(
                t=round(t, 3),
                cx=round(curr_cx, 6),
                cy=round(curr_cy, 6),
                zoom=round(curr_zoom, 4),
                layout=frame_layout,
            )
        )
        prev_t = t

    return ReframePath(
        schema_version=1,
        fps=int(fps),
        source_w=src_w,
        source_h=src_h,
        out_aspect=out_aspect,
        keyframes=keyframes,
        cuts=sorted_cuts,
        warnings=warnings,
    )
