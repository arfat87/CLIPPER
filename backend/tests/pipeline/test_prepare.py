from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.pipeline.ingest.prepare import (
    FINGERPRINT_WINDOW_BYTES,
    PrepareError,
    PrepareResult,
    compute_media_fingerprint,
    prepare_media,
    probe_audio_quality,
)


def _ffprobe_json(path: Path) -> dict[str, Any]:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        text=True,
    )
    return json.loads(out)


def _make_video(
    dest: Path,
    *,
    size: str = "1920x1080",
    src_fps: int = 24,
    duration: float = 2.0,
    audio_filter: str | None = "sine=f=440:r=44100",
    rotate: int | None = None,
    pix_fmt: str = "yuv420p",
) -> Path:
    if rotate is not None:
        raw = dest.with_name(f"{dest.stem}_unrotated.mp4")
        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={src_fps}:duration={duration}",
        ]
        if audio_filter is not None:
            cmd.extend(["-f", "lavfi", "-i", f"{audio_filter},atrim=0:{duration}"])
        cmd.extend(["-c:v", "libx264", "-pix_fmt", pix_fmt])
        if audio_filter is not None:
            cmd.extend(["-c:a", "aac", "-shortest"])
        cmd.append(str(raw))
        subprocess.run(cmd, check=True, capture_output=True)

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(raw),
                "-c",
                "copy",
                "-metadata:s:v:0",
                f"rotate={rotate}",
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
        raw.unlink(missing_ok=True)
        return dest

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={size}:rate={src_fps}:duration={duration}",
    ]
    if audio_filter is not None:
        cmd.extend(["-f", "lavfi", "-i", f"{audio_filter},atrim=0:{duration}"])
    cmd.extend(["-c:v", "libx264", "-pix_fmt", pix_fmt])
    if audio_filter is not None:
        cmd.extend(["-c:a", "aac", "-shortest"])
    cmd.append(str(dest))
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def test_compute_media_fingerprint_deterministic_and_bounded(tmp_path: Path) -> None:
    sample = tmp_path / "video.bin"
    sample.write_bytes(b"head-bytes-" * 1024 + b"tail-bytes-" * 1024)

    fp1 = compute_media_fingerprint(sample)
    fp2 = compute_media_fingerprint(sample)
    assert fp1 == fp2
    assert fp1["algorithm"] == "sha256_head10m_tail10m_size"
    assert fp1["size_bytes"] == sample.stat().st_size
    assert fp1["dedupe_key"] == f"sha256:{fp1['sha256']}"

    # Large file (> 20 MiB): bounded I/O hashes first 10 MiB + last 10 MiB + size
    large_a = tmp_path / "large_a.bin"
    large_b = tmp_path / "large_b.bin"
    large_c = tmp_path / "large_c.bin"
    total_size = 24 * 1024 * 1024

    for path_obj, mid_byte, tail_byte in [
        (large_a, b"\x01", b"\xaa"),
        (large_b, b"\x02", b"\xaa"),  # differs only in middle region beyond 10 MiB windows
        (large_c, b"\x01", b"\xbb"),  # differs in last 10 MiB window
    ]:
        with path_obj.open("wb") as fh:
            fh.write(b"\x10" * FINGERPRINT_WINDOW_BYTES)
            fh.write(mid_byte * (total_size - 2 * FINGERPRINT_WINDOW_BYTES))
            fh.write(tail_byte * FINGERPRINT_WINDOW_BYTES)

    fp_a = compute_media_fingerprint(large_a)
    fp_b = compute_media_fingerprint(large_b)
    fp_c = compute_media_fingerprint(large_c)
    assert fp_a["sha256"] == fp_b["sha256"]
    assert fp_a["sha256"] != fp_c["sha256"]

    with pytest.raises(PrepareError) as exc_info:
        compute_media_fingerprint(tmp_path / "does_not_exist.mp4")
    assert exc_info.value.code == "SOURCE_NOT_FOUND"


def test_prepare_landscape_1920x1080_normalizes_mezzanine_wav_and_thumbs(tmp_path: Path) -> None:
    src = _make_video(tmp_path / "landscape.mp4", size="1920x1080", src_fps=24, duration=2.0)
    job_dir = tmp_path / "job_landscape"

    res = prepare_media(src, job_dir)
    assert isinstance(res, PrepareResult)
    assert res.mezzanine_path == job_dir / "source" / "mezzanine.mp4"
    assert res.audio_path == job_dir / "audio" / "audio.wav"
    assert res.thumbs_dir == job_dir / "media" / "thumbs"
    assert res.mezzanine_path.is_file()
    assert res.audio_path.is_file()
    assert len(res.thumb_paths) >= 1
    assert all(p.is_file() and p.parent == res.thumbs_dir for p in res.thumb_paths)
    assert res.has_audio_stream is True
    assert res.warnings == []

    mezz_probe = _ffprobe_json(res.mezzanine_path)
    v_stream = next(s for s in mezz_probe["streams"] if s["codec_type"] == "video")
    a_stream = next(s for s in mezz_probe["streams"] if s["codec_type"] == "audio")
    assert v_stream["codec_name"] == "h264"
    assert v_stream["pix_fmt"] == "yuv420p"
    assert v_stream["r_frame_rate"] == "30/1"
    assert v_stream["avg_frame_rate"] == "30/1"
    assert int(v_stream["width"]) == 1080
    assert int(v_stream["height"]) == 608
    assert int(v_stream["width"]) % 2 == 0 and int(v_stream["height"]) % 2 == 0
    assert max(int(v_stream["width"]), int(v_stream["height"])) <= 1080
    assert a_stream["codec_name"] == "aac"

    wav_probe = _ffprobe_json(res.audio_path)
    wav_stream = next(s for s in wav_probe["streams"] if s["codec_type"] == "audio")
    assert wav_stream["codec_name"] == "pcm_s16le"
    assert int(wav_stream["sample_rate"]) == 16000
    assert int(wav_stream["channels"]) == 1


def test_prepare_portrait_and_rotated_metadata_video(tmp_path: Path) -> None:
    # 1) Native 1080x1920 portrait
    portrait_src = _make_video(tmp_path / "portrait.mp4", size="1080x1920", src_fps=25, duration=1.5)
    res_portrait = prepare_media(portrait_src, tmp_path / "job_portrait")
    assert res_portrait.width == 608
    assert res_portrait.height == 1080
    assert res_portrait.width % 2 == 0 and res_portrait.height % 2 == 0
    assert max(res_portrait.width, res_portrait.height) <= 1080

    # 2) Landscape 1920x1080 tagged with rotate=90 metadata -> auto-rotated to portrait & metadata cleared
    rotated_src = _make_video(
        tmp_path / "rotated.mp4",
        size="1920x1080",
        src_fps=24,
        duration=1.5,
        rotate=90,
    )
    res_rotated = prepare_media(rotated_src, tmp_path / "job_rotated")

    mezz_probe = _ffprobe_json(res_rotated.mezzanine_path)
    v_stream = next(s for s in mezz_probe["streams"] if s["codec_type"] == "video")
    assert v_stream["codec_name"] == "h264"
    assert v_stream["pix_fmt"] == "yuv420p"
    assert v_stream["r_frame_rate"] == "30/1"
    assert int(v_stream["width"]) == 608
    assert int(v_stream["height"]) == 1080
    assert "rotate" not in (v_stream.get("tags") or {})
    assert not any(
        isinstance(sd, dict) and "rotation" in sd
        for sd in (v_stream.get("side_data_list") or [])
    )


def test_prepare_no_audio_stream_synthesizes_silent_aac_and_warns(tmp_path: Path) -> None:
    src = _make_video(
        tmp_path / "silent_video.mp4",
        size="641x361",  # also verifies odd source dimensions become even
        src_fps=25,
        duration=1.5,
        audio_filter=None,
        pix_fmt="yuv444p",
    )
    job_dir = tmp_path / "job_no_audio"
    res = prepare_media(src, job_dir)

    assert res.has_audio_stream is False
    assert res.width % 2 == 0 and res.height % 2 == 0
    assert "no_audio_stream" in res.warnings
    assert res.audio_probe["warnings"] == ["no_audio_stream"]

    mezz_probe = _ffprobe_json(res.mezzanine_path)
    a_streams = [s for s in mezz_probe["streams"] if s["codec_type"] == "audio"]
    assert len(a_streams) == 1
    assert a_streams[0]["codec_name"] == "aac"

    wav_probe = _ffprobe_json(res.audio_path)
    wav_stream = next(s for s in wav_probe["streams"] if s["codec_type"] == "audio")
    assert wav_stream["codec_name"] == "pcm_s16le"
    assert int(wav_stream["sample_rate"]) == 16000
    assert int(wav_stream["channels"]) == 1


def test_prepare_audio_probe_warnings_quiet_clipping_and_silence(tmp_path: Path) -> None:
    # 1) Very low volume
    quiet_src = _make_video(
        tmp_path / "quiet.mp4",
        size="640x360",
        duration=2.0,
        audio_filter="sine=f=440:r=44100,volume=-25dB",
    )
    quiet_res = prepare_media(quiet_src, tmp_path / "job_quiet")
    assert "very_low_volume" in quiet_res.warnings
    assert quiet_res.audio_probe["integrated_lufs"] < -35.0

    # 2) Clipping audio
    clip_src = _make_video(
        tmp_path / "clipping.mp4",
        size="640x360",
        duration=2.0,
        audio_filter="aevalsrc=clip(4*sin(2*PI*440*t)\\,-1\\,1):s=44100:d=2",
    )
    clip_res = prepare_media(clip_src, tmp_path / "job_clip")
    assert "audio_clipping" in clip_res.warnings
    assert clip_res.audio_probe["is_clipping"] is True
    assert clip_res.audio_probe["clipping_ratio"] > 0.0
    assert clip_res.audio_probe["peak_dbfs"] >= -0.1

    # 3) High silence ratio (1.5s tone + 4.5s silence = 75% silence)
    silence_src = _make_video(
        tmp_path / "mostly_silent.mp4",
        size="640x360",
        duration=6.0,
        audio_filter="aevalsrc=if(lt(t\\,1.5)\\,0.3*sin(2*PI*440*t)\\,0):s=44100:d=6",
    )
    silence_res = prepare_media(silence_src, tmp_path / "job_silence")
    assert "high_silence_ratio" in silence_res.warnings
    assert silence_res.audio_probe["silence_ratio"] >= 0.6


def test_prepare_thumbs_bounded_by_max_thumbs_and_idempotent(tmp_path: Path) -> None:
    src = _make_video(tmp_path / "long_enough.mp4", size="640x360", duration=6.0)
    job_dir = tmp_path / "job_thumbs"

    res1 = prepare_media(src, job_dir, thumb_fps=1.0, max_thumbs=3)
    assert 1 <= len(res1.thumb_paths) <= 3
    assert [p.name for p in res1.thumb_paths] == [
        f"thumb_{idx:05d}.jpg" for idx in range(1, len(res1.thumb_paths) + 1)
    ]

    # Idempotent re-run with smaller max_thumbs does not leave stale thumb files
    res2 = prepare_media(src, job_dir, thumb_fps=1.0, max_thumbs=2)
    assert 1 <= len(res2.thumb_paths) <= 2
    on_disk = sorted((job_dir / "media" / "thumbs").glob("thumb_*.jpg"))
    assert on_disk == res2.thumb_paths


def test_prepare_rejects_corrupt_empty_missing_and_audio_only_files(tmp_path: Path) -> None:
    # Missing
    with pytest.raises(PrepareError) as missing_exc:
        prepare_media(tmp_path / "missing.mp4", tmp_path / "j1")
    assert missing_exc.value.code == "SOURCE_NOT_FOUND"

    # Empty
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    with pytest.raises(PrepareError) as empty_exc:
        prepare_media(empty_file, tmp_path / "j2")
    assert empty_exc.value.code == "EMPTY_MEDIA"

    # Corrupt garbage
    corrupt_file = tmp_path / "corrupt.mp4"
    corrupt_file.write_bytes(b"not-a-valid-mp4-container-at-all")
    job_corrupt = tmp_path / "j3"
    with pytest.raises(PrepareError) as corrupt_exc:
        prepare_media(corrupt_file, job_corrupt)
    assert corrupt_exc.value.code == "CORRUPT_MEDIA"
    assert not (job_corrupt / "source" / "mezzanine.mp4").exists()

    # Audio-only (no video stream)
    audio_only = tmp_path / "only_audio.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440:r=16000:d=1",
            "-acodec",
            "pcm_s16le",
            str(audio_only),
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(PrepareError) as novid_exc:
        prepare_media(audio_only, tmp_path / "j4")
    assert novid_exc.value.code == "NO_VIDEO_STREAM"

    # Invalid WAV for probe_audio_quality
    with pytest.raises(PrepareError) as bad_wav_exc:
        probe_audio_quality(corrupt_file)
    assert bad_wav_exc.value.code == "INVALID_AUDIO"
