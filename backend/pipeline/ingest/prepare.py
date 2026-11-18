from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

FINGERPRINT_WINDOW_BYTES = 10 * 1024 * 1024  # 10 MiB head + 10 MiB tail
_CLIP_SAMPLE_THRESHOLD = 32700  # ~ -0.018 dBFS in signed 16-bit PCM
_CLIP_RATIO_THRESHOLD = 0.0005  # >= 0.05% clipped samples
_SILENCE_FRAME_MS = 50
_SILENCE_THRESHOLD_DBFS = -50.0
_HIGH_SILENCE_RATIO_THRESHOLD = 0.60
_LOW_VOLUME_THRESHOLD_DB = -35.0
_EBUR128_I_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?|-inf)\s+LUFS", re.MULTILINE)
_EBUR128_THRESH_RE = re.compile(r"Integrated loudness:.*?Threshold:\s*(-?\d+(?:\.\d+)?)\s+LUFS", re.DOTALL)


class PrepareError(RuntimeError):
    """Typed preparation error with machine-readable error code."""

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
class PrepareResult:
    """Structured result of preparing raw media into mezzanine, audio WAV, thumbs, and probes."""

    mezzanine_path: Path
    audio_path: Path
    thumbs_dir: Path
    thumb_paths: list[Path]
    duration: float
    width: int
    height: int
    fps: float
    has_audio_stream: bool
    fingerprint: dict[str, Any]
    stream_info: dict[str, Any]
    audio_probe: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def paths(self) -> dict[str, Any]:
        return {
            "mezzanine_path": self.mezzanine_path,
            "audio_path": self.audio_path,
            "thumbs_dir": self.thumbs_dir,
            "thumb_paths": list(self.thumb_paths),
        }

    @property
    def dimensions(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}

    def to_dict(self) -> dict[str, Any]:
        return {
            "mezzanine_path": str(self.mezzanine_path),
            "audio_path": str(self.audio_path),
            "thumbs_dir": str(self.thumbs_dir),
            "thumb_paths": [str(p) for p in self.thumb_paths],
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "has_audio_stream": self.has_audio_stream,
            "fingerprint": self.fingerprint,
            "stream_info": self.stream_info,
            "audio_probe": self.audio_probe,
            "warnings": list(self.warnings),
        }


def compute_media_fingerprint(path: Path) -> dict[str, Any]:
    """Compute deterministic dedupe/cache key from SHA-256 of first 10 MB + last 10 MB + size."""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise PrepareError("SOURCE_NOT_FOUND", f"Media file does not exist: {file_path}")

    size_bytes = file_path.stat().st_size
    hasher = hashlib.sha256()

    with file_path.open("rb") as fh:
        head = fh.read(FINGERPRINT_WINDOW_BYTES)
        hasher.update(head)
        tail_offset = max(0, size_bytes - FINGERPRINT_WINDOW_BYTES)
        fh.seek(tail_offset)
        tail = fh.read(FINGERPRINT_WINDOW_BYTES)
        hasher.update(tail)

    hasher.update(str(size_bytes).encode("ascii"))
    digest = hasher.hexdigest()

    return {
        "algorithm": "sha256_head10m_tail10m_size",
        "sha256": digest,
        "size_bytes": size_bytes,
        "window_bytes": FINGERPRINT_WINDOW_BYTES,
        "dedupe_key": f"sha256:{digest}",
    }


def _run_ffprobe(media_path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise PrepareError("CORRUPT_MEDIA", f"ffprobe failed for {media_path.name}: {detail}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PrepareError("CORRUPT_MEDIA", f"Invalid ffprobe JSON output: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("streams"), list):
        raise PrepareError("CORRUPT_MEDIA", "ffprobe returned no stream metadata")

    return data


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


def _extract_rotation(video_stream: dict[str, Any]) -> int:
    tags = video_stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(float(str(tags["rotate"])))
        except (TypeError, ValueError):
            pass

    for side_data in video_stream.get("side_data_list") or []:
        if isinstance(side_data, dict) and "rotation" in side_data:
            try:
                return int(float(str(side_data["rotation"])))
            except (TypeError, ValueError):
                pass
    return 0


def _inspect_source_streams(probe_data: dict[str, Any]) -> dict[str, Any]:
    streams = probe_data.get("streams") or []
    format_info = probe_data.get("format") or {}

    video_stream: dict[str, Any] | None = None
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition") or {}
        if disposition.get("attached_pic") == 1:
            continue
        if int(stream.get("width") or 0) > 0 and int(stream.get("height") or 0) > 0:
            video_stream = stream
            break

    if video_stream is None:
        raise PrepareError("NO_VIDEO_STREAM", "Source media contains no valid video stream")

    audio_stream: dict[str, Any] | None = next(
        (s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"),
        None,
    )

    duration = 0.0
    for candidate in (format_info.get("duration"), video_stream.get("duration")):
        if candidate is not None:
            try:
                val = float(candidate)
                if math.isfinite(val) and val > 0:
                    duration = val
                    break
            except (TypeError, ValueError):
                continue

    if duration <= 0.0:
        nb_frames = video_stream.get("nb_frames")
        fps_val = _parse_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
        if nb_frames and fps_val > 0:
            try:
                duration = float(nb_frames) / fps_val
            except (TypeError, ValueError):
                duration = 0.0

    if duration <= 0.0:
        raise PrepareError("CORRUPT_MEDIA", "Source media has missing or zero duration")

    return {
        "video_index": int(video_stream.get("index", 0)),
        "audio_index": int(audio_stream["index"]) if audio_stream is not None else None,
        "has_audio_stream": audio_stream is not None,
        "source_width": int(video_stream["width"]),
        "source_height": int(video_stream["height"]),
        "source_rotation": _extract_rotation(video_stream),
        "source_codec": str(video_stream.get("codec_name") or ""),
        "source_duration": duration,
    }


def _build_mezzanine_vf(max_long_side: int, fps: int) -> str:
    even_max = max(2, (max_long_side // 2) * 2)
    w_expr = f"if(gte(iw\\,ih)\\,trunc(min(iw\\,{even_max})/2)*2\\,-2)"
    h_expr = f"if(gte(iw\\,ih)\\,-2\\,trunc(min(ih\\,{even_max})/2)*2)"
    return f"fps={fps},scale={w_expr}:{h_expr},setsar=1"


def _build_thumb_vf(effective_fps: float, thumb_max_side: int = 480) -> str:
    w_expr = f"if(gte(iw\\,ih)\\,min(iw\\,{thumb_max_side})\\,-2)"
    h_expr = f"if(gte(iw\\,ih)\\,-2\\,min(ih\\,{thumb_max_side}))"
    return f"fps={effective_fps:.6f},scale={w_expr}:{h_expr}"


def _run_ffmpeg(cmd: list[str], error_code: str = "TRANSCODE_FAILED") -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise PrepareError(error_code, f"FFmpeg command failed: {detail[-500:]}")
    return proc


def _measure_ebur128_lufs(wav_path: Path) -> tuple[float | None, bool]:
    """Measure ITU-R BS.1770 integrated loudness via FFmpeg ebur128 filter."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostats",
            "-i",
            str(wav_path),
            "-af",
            "ebur128=peak=sample",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None, False

    matches = _EBUR128_I_RE.findall(proc.stderr)
    if not matches:
        return None, False

    raw_val = matches[-1]
    lufs = -70.0 if raw_val == "-inf" else float(raw_val)

    thresh_match = _EBUR128_THRESH_RE.search(proc.stderr)
    gated = True
    if thresh_match is not None and float(thresh_match.group(1)) == 0.0:
        gated = False

    return lufs, gated


def probe_audio_quality(wav_path: Path, *, has_audio_stream: bool = True) -> dict[str, Any]:
    """Analyze 16 kHz PCM WAV for loudness, sample peak, clipping, and silence ratio."""
    audio_file = Path(wav_path)
    if not audio_file.exists() or not audio_file.is_file() or audio_file.stat().st_size == 0:
        raise PrepareError("INVALID_AUDIO", f"WAV file is missing or empty: {audio_file}")

    try:
        with wave.open(str(audio_file), "rb") as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()

            if sampwidth != 2 or nchannels < 1 or framerate <= 0:
                raise PrepareError(
                    "INVALID_AUDIO",
                    f"Expected 16-bit PCM WAV, got sampwidth={sampwidth}, channels={nchannels}, rate={framerate}",
                )

            frame_len = max(1, int(round(framerate * (_SILENCE_FRAME_MS / 1000.0))))
            chunk_frames = frame_len * 200  # ~10s per chunk

            total_samples = 0
            sum_squares = 0.0
            max_abs_sample = 0
            clipped_samples = 0
            total_windows = 0
            silent_windows = 0

            while True:
                raw = wf.readframes(chunk_frames)
                if not raw:
                    break
                samples_i16 = np.frombuffer(raw, dtype=np.int16)
                if nchannels > 1:
                    samples_i16 = samples_i16.reshape(-1, nchannels)[:, 0]

                if samples_i16.size == 0:
                    continue

                abs_i32 = np.abs(samples_i16.astype(np.int32))
                chunk_max = int(np.max(abs_i32))
                if chunk_max > max_abs_sample:
                    max_abs_sample = chunk_max

                clipped_samples += int(np.sum(abs_i32 >= _CLIP_SAMPLE_THRESHOLD))
                norm_f64 = samples_i16.astype(np.float64) / 32768.0
                sum_squares += float(np.sum(norm_f64 * norm_f64))
                total_samples += int(samples_i16.size)

                n_full = norm_f64.size // frame_len
                if n_full > 0:
                    windows = norm_f64[: n_full * frame_len].reshape(n_full, frame_len)
                    win_rms = np.sqrt(np.mean(windows * windows, axis=1))
                    win_db = 20.0 * np.log10(np.maximum(win_rms, 1e-12))
                    silent_windows += int(np.sum(win_db < _SILENCE_THRESHOLD_DBFS))
                    total_windows += n_full

                rem = norm_f64.size % frame_len
                if rem > 0:
                    tail_slice = norm_f64[n_full * frame_len :]
                    tail_rms = float(np.sqrt(np.mean(tail_slice * tail_slice)))
                    tail_db = 20.0 * math.log10(max(tail_rms, 1e-12))
                    if tail_db < _SILENCE_THRESHOLD_DBFS:
                        silent_windows += 1
                    total_windows += 1
    except wave.Error as exc:
        raise PrepareError("INVALID_AUDIO", f"Corrupt WAV file {audio_file.name}: {exc}") from exc

    duration_s = float(nframes / framerate) if framerate > 0 else 0.0
    if total_samples > 0:
        rms = math.sqrt(sum_squares / total_samples)
        rms_dbfs = round(max(-120.0, 20.0 * math.log10(rms)), 3) if rms > 0.0 else -120.0
        peak_dbfs = (
            round(max(-120.0, 20.0 * math.log10(max_abs_sample / 32768.0)), 3)
            if max_abs_sample > 0
            else -120.0
        )
        clipping_ratio = round(clipped_samples / total_samples, 6)
        silence_ratio = round(silent_windows / total_windows, 4) if total_windows > 0 else 1.0
    else:
        rms_dbfs = -120.0
        peak_dbfs = -120.0
        clipping_ratio = 0.0
        silence_ratio = 1.0

    is_clipping = bool(clipping_ratio >= _CLIP_RATIO_THRESHOLD)

    ebur_lufs, ebur_gated = _measure_ebur128_lufs(audio_file)
    if ebur_lufs is not None and ebur_gated and duration_s >= 0.45:
        integrated_lufs = round(ebur_lufs, 2)
    else:
        integrated_lufs = round(rms_dbfs, 2)

    warnings: list[str] = []
    if not has_audio_stream:
        warnings.append("no_audio_stream")
    else:
        if integrated_lufs < _LOW_VOLUME_THRESHOLD_DB:
            warnings.append("very_low_volume")
        if is_clipping:
            warnings.append("audio_clipping")
        if silence_ratio >= _HIGH_SILENCE_RATIO_THRESHOLD:
            warnings.append("high_silence_ratio")

    return {
        "has_audio_stream": has_audio_stream,
        "sample_rate": framerate,
        "channels": nchannels,
        "duration": round(duration_s, 3),
        "integrated_lufs": integrated_lufs,
        "rms_dbfs": rms_dbfs,
        "peak_dbfs": peak_dbfs,
        "clipping_ratio": clipping_ratio,
        "is_clipping": is_clipping,
        "silence_ratio": silence_ratio,
        "warnings": warnings,
    }


def _compute_effective_thumb_fps(duration: float, thumb_fps: float, max_thumbs: int) -> float:
    if duration <= 0 or thumb_fps <= 0 or max_thumbs <= 0:
        return thumb_fps
    if duration * thumb_fps <= max_thumbs:
        return thumb_fps
    # Slight safety margin prevents boundary rounding from emitting max_thumbs + 1
    return max(1e-4, (max_thumbs / duration) * 0.98)


def prepare_media(
    source_path: Path,
    job_dir: Path,
    *,
    max_long_side: int = 1080,
    fps: int = 30,
    thumb_fps: float = 1.0,
    max_thumbs: int = 120,
) -> PrepareResult:
    """Validate local/downloaded media, normalize to mezzanine MP4 + 16kHz WAV + thumbs, and probe audio."""
    src = Path(source_path)
    jdir = Path(job_dir)

    if max_long_side < 2 or fps < 1 or thumb_fps <= 0 or max_thumbs < 1:
        raise PrepareError("INVALID_ARGS", "Invalid prepare_media normalization parameters")
    if not src.exists() or not src.is_file():
        raise PrepareError("SOURCE_NOT_FOUND", f"Source media file not found: {src}")
    if src.stat().st_size == 0:
        raise PrepareError("EMPTY_MEDIA", f"Source media file is empty: {src}")

    fingerprint = compute_media_fingerprint(src)
    src_probe = _run_ffprobe(src)
    src_info = _inspect_source_streams(src_probe)
    has_audio_stream = bool(src_info["has_audio_stream"])

    jdir.mkdir(parents=True, exist_ok=True)
    source_out_dir = jdir / "source"
    audio_out_dir = jdir / "audio"
    media_out_dir = jdir / "media"

    final_mezzanine = source_out_dir / "mezzanine.mp4"
    final_audio = audio_out_dir / "audio.wav"
    final_thumbs_dir = media_out_dir / "thumbs"

    with tempfile.TemporaryDirectory(prefix=".prepare_stage_", dir=jdir) as tmp_dir_str:
        stage_dir = Path(tmp_dir_str)
        stage_mezzanine = stage_dir / "mezzanine.mp4"
        stage_audio = stage_dir / "audio.wav"
        stage_thumbs_dir = stage_dir / "thumbs"
        stage_thumbs_dir.mkdir(parents=True, exist_ok=True)

        vf = _build_mezzanine_vf(max_long_side=max_long_side, fps=fps)
        video_idx = src_info["video_index"]

        if has_audio_stream:
            audio_idx = src_info["audio_index"]
            mezzanine_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-map",
                f"0:{video_idx}",
                "-map",
                f"0:{audio_idx}",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-vsync",
                "cfr",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ac",
                "2",
                "-ar",
                "44100",
                "-movflags",
                "+faststart",
                str(stage_mezzanine),
            ]
        else:
            mezzanine_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map",
                f"0:{video_idx}",
                "-map",
                "1:a:0",
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-vsync",
                "cfr",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(stage_mezzanine),
            ]

        _run_ffmpeg(mezzanine_cmd, error_code="TRANSCODE_FAILED")

        if has_audio_stream:
            wav_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-map",
                f"0:{src_info['audio_index']}",
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(stage_audio),
            ]
        else:
            wav_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(stage_mezzanine),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(stage_audio),
            ]
        _run_ffmpeg(wav_cmd, error_code="AUDIO_EXTRACT_FAILED")

        mezz_probe = _run_ffprobe(stage_mezzanine)
        mezz_streams = mezz_probe.get("streams") or []
        mezz_video = next((s for s in mezz_streams if s.get("codec_type") == "video"), None)
        mezz_audio = next((s for s in mezz_streams if s.get("codec_type") == "audio"), None)
        if mezz_video is None or mezz_audio is None:
            raise PrepareError("TRANSCODE_FAILED", "Prepared mezzanine.mp4 is missing required streams")

        mezz_duration = float((mezz_probe.get("format") or {}).get("duration") or src_info["source_duration"])
        mezz_width = int(mezz_video["width"])
        mezz_height = int(mezz_video["height"])
        mezz_fps = _parse_rate(mezz_video.get("r_frame_rate") or mezz_video.get("avg_frame_rate")) or float(fps)

        eff_thumb_fps = _compute_effective_thumb_fps(mezz_duration, thumb_fps, max_thumbs)
        thumb_vf = _build_thumb_vf(eff_thumb_fps)
        thumb_pattern = str(stage_thumbs_dir / "thumb_%05d.jpg")
        thumb_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(stage_mezzanine),
            "-vf",
            thumb_vf,
            "-frames:v",
            str(max_thumbs),
            "-q:v",
            "4",
            thumb_pattern,
        ]
        _run_ffmpeg(thumb_cmd, error_code="THUMB_EXTRACT_FAILED")

        staged_thumbs = sorted(stage_thumbs_dir.glob("thumb_*.jpg"))
        if not staged_thumbs:
            fallback_thumb = stage_thumbs_dir / "thumb_00001.jpg"
            fallback_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(stage_mezzanine),
                "-frames:v",
                "1",
                "-vf",
                "scale=if(gte(iw\\,ih)\\,min(iw\\,480)\\,-2):if(gte(iw\\,ih)\\,-2\\,min(ih\\,480))",
                "-q:v",
                "4",
                str(fallback_thumb),
            ]
            _run_ffmpeg(fallback_cmd, error_code="THUMB_EXTRACT_FAILED")
            staged_thumbs = sorted(stage_thumbs_dir.glob("thumb_*.jpg"))

        audio_probe = probe_audio_quality(stage_audio, has_audio_stream=has_audio_stream)

        source_out_dir.mkdir(parents=True, exist_ok=True)
        audio_out_dir.mkdir(parents=True, exist_ok=True)
        media_out_dir.mkdir(parents=True, exist_ok=True)

        os.replace(stage_mezzanine, final_mezzanine)
        os.replace(stage_audio, final_audio)
        if final_thumbs_dir.exists():
            os.replace(final_thumbs_dir, stage_dir / "old_thumbs")
        os.replace(stage_thumbs_dir, final_thumbs_dir)

    final_thumb_paths = [final_thumbs_dir / thumb.name for thumb in staged_thumbs]

    stream_info: dict[str, Any] = {
        "video": {
            "codec_name": mezz_video.get("codec_name"),
            "pix_fmt": mezz_video.get("pix_fmt"),
            "width": mezz_width,
            "height": mezz_height,
            "r_frame_rate": mezz_video.get("r_frame_rate"),
            "avg_frame_rate": mezz_video.get("avg_frame_rate"),
            "rotation": _extract_rotation(mezz_video),
            "side_data_list": mezz_video.get("side_data_list") or [],
        },
        "audio": {
            "codec_name": mezz_audio.get("codec_name"),
            "sample_rate": int(mezz_audio.get("sample_rate") or 0),
            "channels": int(mezz_audio.get("channels") or 0),
            "synthesized": not has_audio_stream,
        },
        "source": {
            "width": src_info["source_width"],
            "height": src_info["source_height"],
            "rotation": src_info["source_rotation"],
            "codec_name": src_info["source_codec"],
            "has_audio_stream": has_audio_stream,
        },
    }

    return PrepareResult(
        mezzanine_path=final_mezzanine,
        audio_path=final_audio,
        thumbs_dir=final_thumbs_dir,
        thumb_paths=final_thumb_paths,
        duration=round(mezz_duration, 3),
        width=mezz_width,
        height=mezz_height,
        fps=round(mezz_fps, 3),
        has_audio_stream=has_audio_stream,
        fingerprint=fingerprint,
        stream_info=stream_info,
        audio_probe=audio_probe,
        warnings=list(audio_probe["warnings"]),
    )
