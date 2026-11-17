from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yt_dlp

from backend.app.config import get_settings
from backend.pipeline.sample_generator import ensure_sample_media
from hitl.models import BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker


def detect_platform(url: str) -> str:
    u = url.strip().lower()
    if u.startswith("sample://") or u.startswith("file://") or Path(url).exists():
        return "sample"
    if re.search(r"(youtube\.com|youtu\.be)", u):
        return "youtube"
    if re.search(r"(instagram\.com|instagr\.am)", u):
        return "instagram"
    if re.search(r"(facebook\.com|fb\.watch|fb\.com)", u):
        return "facebook"
    return "direct"


def _cookie_file_for_platform(platform: str) -> Path | None:
    settings = get_settings()
    mapping = {
        "youtube": settings.cookies_youtube,
        "instagram": settings.cookies_instagram,
        "facebook": settings.cookies_facebook,
    }
    configured = mapping.get(platform, "")
    if configured and Path(configured).exists():
        return Path(configured)
    default_path = settings.secrets_dir / "cookies" / f"{platform}.txt"
    if default_path.exists():
        return default_path
    return None


def download_and_normalize(url: str, job_dir: Path) -> dict[str, Any]:
    """Download video from YouTube/Instagram/Facebook or local/sample URI, normalize to mezzanine MP4 + 16kHz WAV."""
    platform = detect_platform(url)
    source_dir = job_dir / "source"
    audio_dir = job_dir / "audio"
    source_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    mezzanine_mp4 = source_dir / "source.mp4"
    audio_wav = audio_dir / "audio.wav"
    meta_json = source_dir / "metadata.json"

    if platform == "sample":
        samples = ensure_sample_media()
        sample_id = url.replace("sample://", "").replace("file://", "").strip()
        matched = next((s for s in samples if s["id"] == sample_id or s["mp4_path"] == url), None)
        if not matched and Path(url).exists():
            raw_mp4 = Path(url)
            title = raw_mp4.stem
            duration = _probe_duration(raw_mp4)
            transcript_hint = None
        else:
            if not matched:
                matched = samples[0]
            raw_mp4 = Path(matched["mp4_path"])
            title = matched["title"]
            duration = float(matched["duration"])
            transcript_hint = matched.get("transcript_path")

        shutil.copy2(raw_mp4, mezzanine_mp4)
        _extract_wav(mezzanine_mp4, audio_wav)
        meta = {
            "platform": platform,
            "title": title,
            "duration": duration,
            "source_url": url,
            "heatmap": [],
            "transcript_hint": transcript_hint,
        }
        meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    # Real URL download via yt-dlp
    raw_out = source_dir / "raw.%(ext)s"
    ydl_opts: dict[str, Any] = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
        "outtmpl": str(raw_out),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    cookie_path = _cookie_file_for_platform(platform)
    if cookie_path:
        ydl_opts["cookiefile"] = str(cookie_path)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloaded = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
            if not downloaded.exists():
                candidates = list(source_dir.glob("raw.*"))
                if candidates:
                    downloaded = candidates[0]
    except Exception as exc:
        err_str = str(exc)
        if platform in ("instagram", "facebook") or "login" in err_str.lower() or "403" in err_str:
            create_or_get_blocker(
                btype=BlockerType.CREDENTIAL,
                task_id="T-2.2",
                title=f"Browser cookies required for {platform.title()} download",
                needed=[
                    NeededItem(
                        key=f"COOKIES_{platform.upper()}",
                        where=f"secrets/cookies/{platform}.txt",
                        how_to_get=f"Export Netscape cookies.txt from a logged-in {platform.title()} browser tab.",
                    )
                ],
                why=f"yt-dlp encountered an authentication/rate-limit wall on {platform}: {err_str[:180]}",
                alternatives=["Upload the video file directly in the Web UI or test with sample:// clips."],
                raise_exc=False,
            )
        raise RuntimeError(f"Download failed for {url}: {err_str}") from exc

    _normalize_mp4(downloaded, mezzanine_mp4)
    _extract_wav(mezzanine_mp4, audio_wav)
    duration = float(info.get("duration") or _probe_duration(mezzanine_mp4))
    heatmap = info.get("heatmap") or []

    meta = {
        "platform": platform,
        "title": info.get("title") or "Downloaded Video",
        "duration": duration,
        "source_url": url,
        "uploader": info.get("uploader"),
        "heatmap": heatmap,
        "transcript_hint": None,
    }
    meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _probe_duration(mp4_path: Path) -> float:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(proc.stdout.strip())
    except Exception:
        return 60.0


def _normalize_mp4(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        return
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(dst),
        ],
        check=True,
        capture_output=True,
    )


def _extract_wav(src_mp4: Path, dst_wav: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src_mp4),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(dst_wav),
        ],
        check=True,
        capture_output=True,
    )
