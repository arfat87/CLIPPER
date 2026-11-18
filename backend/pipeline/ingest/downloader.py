from __future__ import annotations

import ipaddress
import json
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import yt_dlp

from backend.app.config import get_settings
from backend.pipeline.sample_generator import ensure_sample_media
from hitl.models import BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker

_TRACKING_QUERY_PARAMS = {
    "fbclid",
    "si",
    "feature",
    "gclid",
    "igshid",
    "igsh",
    "mibextid",
}

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


class IngestError(RuntimeError):
    """Typed ingest error with machine-readable error code and retryability flag."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        retryable: bool = False,
        platform: str | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.platform = platform
        super().__init__(message or code)


def _is_tracking_param(key: str) -> bool:
    low = key.lower()
    return low.startswith("utm_") or low in _TRACKING_QUERY_PARAMS


def _is_local_path(raw: str) -> bool:
    if not raw or "://" in raw:
        return False
    try:
        return Path(raw).exists()
    except OSError:
        return False


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def detect_platform(url: str) -> str:
    raw = (url or "").strip()
    lower = raw.lower()
    if lower.startswith("sample://") or lower.startswith("file://") or _is_local_path(raw):
        return "sample"
    if lower.startswith("drive://"):
        return "drive"

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower().rstrip(".")

    if (
        _host_matches(host, "youtube.com")
        or _host_matches(host, "youtu.be")
        or _host_matches(host, "youtube-nocookie.com")
    ):
        return "youtube"
    if _host_matches(host, "instagram.com") or _host_matches(host, "instagr.am"):
        return "instagram"
    if (
        _host_matches(host, "facebook.com")
        or _host_matches(host, "fb.watch")
        or _host_matches(host, "fb.com")
    ):
        return "facebook"
    if host in {"drive.google.com", "drive.usercontent.google.com"}:
        return "drive"
    return "direct"


def _extract_video_id(platform: str, raw: str, parsed: Any, query_pairs: list[tuple[str, str]]) -> str | None:
    path_parts = [p for p in (parsed.path or "").split("/") if p]
    query_dict = {k: v for k, v in query_pairs}

    if platform == "sample":
        if raw.lower().startswith("sample://"):
            return raw[len("sample://") :].strip("/") or None
        if raw.lower().startswith("file://"):
            return Path(parsed.path).stem or None
        return Path(raw).stem or None

    if platform == "drive":
        if raw.lower().startswith("drive://"):
            rest = raw[len("drive://") :].strip("/")
            parts = [p for p in rest.split("/") if p]
            if parts and parts[0] in {"file", "files", "d"} and len(parts) >= 2:
                return parts[1]
            return parts[0] if parts else None
        if "id" in query_dict and query_dict["id"]:
            return query_dict["id"]
        if "d" in path_parts:
            idx = path_parts.index("d")
            if idx + 1 < len(path_parts):
                return path_parts[idx + 1]
        return None

    if platform == "youtube":
        host = (parsed.hostname or "").lower().rstrip(".")
        if _host_matches(host, "youtu.be") and path_parts:
            return path_parts[0]
        if "v" in query_dict and query_dict["v"]:
            return query_dict["v"]
        if len(path_parts) >= 2 and path_parts[0] in {"shorts", "live", "embed", "v"}:
            return path_parts[1]
        return None

    if platform == "instagram":
        if len(path_parts) >= 2 and path_parts[0] in {"reel", "reels", "p", "tv"}:
            return path_parts[1]
        return None

    if platform == "facebook":
        host = (parsed.hostname or "").lower().rstrip(".")
        if _host_matches(host, "fb.watch") and path_parts:
            return path_parts[0]
        if "v" in query_dict and query_dict["v"]:
            return query_dict["v"]
        if len(path_parts) >= 2 and path_parts[0] in {"reel", "watch"}:
            return path_parts[1]
        if "videos" in path_parts:
            idx = path_parts.index("videos")
            if idx + 1 < len(path_parts):
                return path_parts[idx + 1]
        return None

    return None


def canonicalize_url(url: str) -> dict[str, Any]:
    """Strip tracking parameters and extract platform, video_id, and dedupe_key."""
    raw = (url or "").strip()
    if not raw:
        raise IngestError("UNSUPPORTED_URL", "Empty ingest URL", retryable=False)

    platform = detect_platform(raw)
    parsed = urlparse(raw)

    if platform == "sample":
        video_id = _extract_video_id(platform, raw, parsed, [])
        canonical_url = f"sample://{video_id}" if raw.lower().startswith("sample://") and video_id else raw
        return {
            "platform": platform,
            "canonical_url": canonical_url,
            "video_id": video_id,
            "dedupe_key": f"sample:{video_id}" if video_id else None,
        }

    if platform == "drive" and raw.lower().startswith("drive://"):
        video_id = _extract_video_id(platform, raw, parsed, [])
        if not video_id:
            raise IngestError("UNSUPPORTED_URL", "Missing Google Drive file id", retryable=False, platform=platform)
        return {
            "platform": platform,
            "canonical_url": f"drive://{video_id}",
            "video_id": video_id,
            "dedupe_key": f"drive:{video_id}",
        }

    if not parsed.scheme:
        parsed = urlparse(f"https://{raw}")

    raw_pairs = parse_qsl(parsed.query, keep_blank_values=False)
    clean_pairs = [(k, v) for k, v in raw_pairs if not _is_tracking_param(k)]
    video_id = _extract_video_id(platform, raw, parsed, clean_pairs)

    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path

    if platform == "youtube" and video_id and _YOUTUBE_ID_RE.match(video_id):
        host = (parsed.hostname or "").lower().rstrip(".")
        if _host_matches(host, "youtu.be") or path in {"", "/", "/watch"}:
            scheme = "https"
            netloc = "www.youtube.com"
            path = "/watch"
            extra_pairs = [(k, v) for k, v in clean_pairs if k != "v"]
            clean_pairs = [("v", video_id), *extra_pairs]
    elif platform == "drive" and video_id:
        scheme = "https"
        netloc = "drive.google.com"
        path = f"/file/d/{video_id}/view"
        clean_pairs = []

    clean_query = urlencode(clean_pairs, doseq=True)
    canonical_url = urlunparse((scheme, netloc, path, "", clean_query, ""))

    return {
        "platform": platform,
        "canonical_url": canonical_url,
        "video_id": video_id,
        "dedupe_key": f"{platform}:{video_id}" if video_id else None,
    }


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_disallowed_ip(ip.ipv4_mapped)
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NETWORK:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or not ip.is_global
    )


def validate_ingest_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate ingest URI and block SSRF targets (non-HTTP/S remote schemes, private/loopback/link-local IPs)."""
    raw = (url or "").strip()
    if not raw:
        raise IngestError("UNSUPPORTED_URL", "Empty ingest URL", retryable=False)

    lower = raw.lower()
    if lower.startswith("sample://"):
        sample_id = raw[len("sample://") :].strip()
        if not sample_id:
            raise IngestError("UNSUPPORTED_URL", "Missing sample id in sample:// URI", retryable=False, platform="sample")
        return raw

    if lower.startswith("drive://"):
        drive_id = raw[len("drive://") :].strip("/")
        if not drive_id:
            raise IngestError("UNSUPPORTED_URL", "Missing file id in drive:// URI", retryable=False, platform="drive")
        return raw

    if _is_local_path(raw):
        try:
            if Path(raw).is_file():
                return raw
        except OSError as exc:
            raise IngestError("UNSUPPORTED_URL", f"Invalid local path: {exc}", retryable=False, platform="sample") from exc

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise IngestError(
            "UNSUPPORTED_URL",
            f"Unsupported URL scheme '{scheme or 'none'}'; only http/https are allowed for remote URLs",
            retryable=False,
        )

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise IngestError("UNSUPPORTED_URL", "Remote URL is missing a hostname", retryable=False)

    if (
        host in _LOCAL_HOSTNAMES
        or host.endswith(".local")
        or host.endswith(".internal")
        or host.endswith(".localhost")
    ):
        raise IngestError(
            "UNSUPPORTED_URL",
            f"Blocked local/internal hostname '{host}'",
            retryable=False,
        )

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _is_disallowed_ip(literal_ip):
            raise IngestError(
                "UNSUPPORTED_URL",
                f"Blocked private or reserved IP address '{host}'",
                retryable=False,
            )
        return raw

    if resolve_dns:
        port = parsed.port or (443 if scheme == "https" else 80)
        try:
            addr_info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror:
            addr_info = []

        for _, _, _, _, sockaddr in addr_info:
            ip_str = str(sockaddr[0]).split("%")[0]
            try:
                resolved_ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _is_disallowed_ip(resolved_ip):
                raise IngestError(
                    "UNSUPPORTED_URL",
                    f"Blocked hostname '{host}' resolving to private/reserved IP '{ip_str}'",
                    retryable=False,
                )

    return raw


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


def _raise_cookie_blocker(platform: str, reason: str) -> None:
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
        why=f"yt-dlp encountered an authentication/rate-limit wall on {platform}: {reason[:180]}",
        alternatives=["Upload the video file directly in the Web UI or test with sample:// clips."],
        raise_exc=False,
    )


def classify_yt_dlp_error(exc: Exception | str, platform: str = "direct") -> IngestError:
    """Classify a yt-dlp / ingest exception into a typed IngestError."""
    if isinstance(exc, IngestError):
        return exc

    err_str = str(exc)
    low = err_str.lower()

    if any(tok in low for tok in ("429", "too many requests", "rate limit", "rate-limit", "throttl")):
        return IngestError(
            "RATE_LIMITED",
            f"Rate limited by {platform}: {err_str}",
            retryable=True,
            platform=platform,
        )

    if any(
        tok in low
        for tok in (
            "private video",
            "this video is private",
            "video is private",
            "members-only",
            "members only",
            "subscribers only",
        )
    ):
        return IngestError(
            "PRIVATE_VIDEO",
            f"Video is private on {platform}: {err_str}",
            retryable=False,
            platform=platform,
        )

    if any(
        tok in low
        for tok in (
            "available in your country",
            "blocked it in your country",
            "geo-blocked",
            "geoblocked",
            "geo restricted",
            "geo-restricted",
        )
    ):
        return IngestError(
            "GEO_BLOCKED",
            f"Video is geo-blocked on {platform}: {err_str}",
            retryable=False,
            platform=platform,
        )

    if any(tok in low for tok in ("unsupported url", "is not a valid url", "no suitable extractor")):
        return IngestError(
            "UNSUPPORTED_URL",
            f"Unsupported URL for {platform}: {err_str}",
            retryable=False,
            platform=platform,
        )

    if any(tok in low for tok in ("no space left on device", "enospc", "disk full")):
        return IngestError(
            "DISK_FULL",
            f"Insufficient disk space: {err_str}",
            retryable=False,
            platform=platform,
        )

    if (
        any(
            tok in low
            for tok in (
                "login",
                "log in",
                "sign in",
                "cookies",
                "authentication",
                "401",
                "403",
                "forbidden",
                "not a bot",
                "age-restricted",
            )
        )
        or platform in ("instagram", "facebook")
    ):
        return IngestError(
            "LOGIN_REQUIRED",
            f"Authentication/cookies required for {platform}: {err_str}",
            retryable=False,
            platform=platform,
        )

    return IngestError(
        "DOWNLOAD_FAILED",
        f"Download failed for {platform}: {err_str}",
        retryable=True,
        platform=platform,
    )


def _nearest_existing_dir(path: Path) -> Path:
    cur = path
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    return cur if cur.exists() else Path(".")


def check_ingest_guards(
    duration_s: float | None,
    estimated_bytes: int | None = None,
    *,
    target_dir: Path | None = None,
    max_video_hours: float | None = None,
) -> dict[str, Any]:
    """Enforce MAX_VIDEO_HOURS duration cap and >= 3x estimated size free disk guard."""
    settings = get_settings()
    max_hours = float(settings.max_video_hours if max_video_hours is None else max_video_hours)
    max_duration_s = max_hours * 3600.0

    if duration_s is not None and float(duration_s) > max_duration_s:
        raise IngestError(
            "TOO_LONG",
            f"Video duration {float(duration_s):.1f}s exceeds MAX_VIDEO_HOURS ({max_hours:g}h / {max_duration_s:.0f}s)",
            retryable=False,
        )

    disk_target = _nearest_existing_dir(target_dir or settings.data_dir)
    free_bytes = int(shutil.disk_usage(disk_target).free)
    required_bytes = int(estimated_bytes) * 3 if estimated_bytes and int(estimated_bytes) > 0 else 0

    if required_bytes > 0 and free_bytes < required_bytes:
        raise IngestError(
            "DISK_FULL",
            f"Insufficient free disk space: {free_bytes} bytes free, need >= {required_bytes} bytes (3x estimated media size)",
            retryable=False,
        )

    return {
        "ok": True,
        "max_duration_s": max_duration_s,
        "free_bytes": free_bytes,
        "required_bytes": required_bytes,
    }


def _estimate_media_bytes(info: dict[str, Any]) -> int | None:
    for key in ("filesize", "filesize_approx"):
        val = info.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return int(val)

    req_formats = info.get("requested_formats") or []
    if req_formats:
        total = 0
        for fmt in req_formats:
            size = fmt.get("filesize") or fmt.get("filesize_approx")
            if isinstance(size, (int, float)) and size > 0:
                total += int(size)
        if total > 0:
            return total

    formats = info.get("formats") or []
    sizes = [
        int(f.get("filesize") or f.get("filesize_approx") or 0)
        for f in formats
        if isinstance(f, dict) and (f.get("filesize") or f.get("filesize_approx"))
    ]
    if sizes:
        return max(sizes)

    duration = info.get("duration")
    tbr = info.get("tbr")
    if isinstance(duration, (int, float)) and isinstance(tbr, (int, float)) and duration > 0 and tbr > 0:
        return int((tbr * 1000.0 / 8.0) * float(duration))

    return None


def _normalize_chapters(raw_chapters: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_chapters, list):
        return []
    out: list[dict[str, Any]] = []
    for ch in raw_chapters:
        if not isinstance(ch, dict):
            continue
        start = ch.get("start_time")
        end = ch.get("end_time")
        if start is None or end is None:
            continue
        out.append(
            {
                "title": str(ch.get("title") or ""),
                "start_time": float(start),
                "end_time": float(end),
            }
        )
    return out


def _normalize_heatmap(raw_heatmap: Any) -> list[dict[str, float]]:
    if not isinstance(raw_heatmap, list):
        return []
    out: list[dict[str, float]] = []
    for item in raw_heatmap:
        if not isinstance(item, dict):
            continue
        start = item.get("start_time")
        end = item.get("end_time")
        val = item.get("value")
        if start is None or end is None or val is None:
            continue
        out.append(
            {
                "start_time": float(start),
                "end_time": float(end),
                "value": float(val),
            }
        )
    return out


def _resolve_sample_info(url: str) -> dict[str, Any]:
    samples = ensure_sample_media()
    sample_id = url.replace("sample://", "").replace("file://", "").strip()
    matched = next((s for s in samples if s["id"] == sample_id or s["mp4_path"] == url), None)
    if not matched and _is_local_path(url):
        raw_mp4 = Path(url)
        duration = _probe_duration(raw_mp4)
        size_bytes = raw_mp4.stat().st_size if raw_mp4.exists() else None
        return {
            "raw_mp4": raw_mp4,
            "title": raw_mp4.stem,
            "duration": duration,
            "estimated_bytes": size_bytes,
            "transcript_hint": None,
        }

    if not matched:
        matched = samples[0]
    raw_mp4 = Path(matched["mp4_path"])
    size_bytes = raw_mp4.stat().st_size if raw_mp4.exists() else None
    return {
        "raw_mp4": raw_mp4,
        "title": matched["title"],
        "duration": float(matched["duration"]),
        "estimated_bytes": size_bytes,
        "transcript_hint": matched.get("transcript_path"),
    }


def probe_media(url: str, ydl_cls: Any = None) -> dict[str, Any]:
    """Probe media metadata (title, duration, chapters, heatmap, estimated_bytes) without downloading."""
    validate_ingest_url(url)
    canon = canonicalize_url(url)
    platform = canon["platform"]

    if platform == "sample":
        sample_info = _resolve_sample_info(url)
        return {
            "platform": platform,
            "video_id": canon["video_id"],
            "dedupe_key": canon["dedupe_key"],
            "title": sample_info["title"],
            "duration": float(sample_info["duration"]),
            "uploader": "local",
            "source_url": url,
            "canonical_url": canon["canonical_url"],
            "chapters": [],
            "heatmap": [],
            "estimated_bytes": sample_info["estimated_bytes"],
            "subtitles_available": [],
            "transcript_hint": sample_info["transcript_hint"],
        }

    ydl_factory = ydl_cls or yt_dlp.YoutubeDL
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    cookie_path = _cookie_file_for_platform(platform)
    if cookie_path:
        ydl_opts["cookiefile"] = str(cookie_path)

    try:
        with ydl_factory(ydl_opts) as ydl:
            info = ydl.extract_info(canon["canonical_url"], download=False) or {}
    except Exception as exc:
        err = classify_yt_dlp_error(exc, platform=platform)
        if err.code == "LOGIN_REQUIRED":
            _raise_cookie_blocker(platform, str(exc))
        raise err from exc

    video_id = str(info.get("id")) if info.get("id") else canon["video_id"]
    dedupe_key = f"{platform}:{video_id}" if video_id else canon["dedupe_key"]

    return {
        "platform": platform,
        "video_id": video_id,
        "dedupe_key": dedupe_key,
        "title": info.get("title") or "Downloaded Video",
        "duration": float(info.get("duration") or 0.0),
        "uploader": info.get("uploader") or info.get("channel"),
        "source_url": url,
        "canonical_url": canon["canonical_url"],
        "webpage_url": info.get("webpage_url") or canon["canonical_url"],
        "chapters": _normalize_chapters(info.get("chapters")),
        "heatmap": _normalize_heatmap(info.get("heatmap")),
        "estimated_bytes": _estimate_media_bytes(info),
        "subtitles_available": sorted((info.get("subtitles") or {}).keys()),
        "thumbnails": info.get("thumbnails") or [],
        "transcript_hint": None,
    }


def download_and_normalize(url: str, job_dir: Path, *, ydl_cls: Any = None) -> dict[str, Any]:
    """Download video from YouTube/Instagram/Facebook or local/sample URI, normalize to mezzanine MP4 + 16kHz WAV."""
    validate_ingest_url(url)
    canon = canonicalize_url(url)
    platform = canon["platform"]

    source_dir = job_dir / "source"
    audio_dir = job_dir / "audio"
    source_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    mezzanine_mp4 = source_dir / "source.mp4"
    audio_wav = audio_dir / "audio.wav"
    meta_json = source_dir / "metadata.json"

    if platform == "sample":
        sample_info = _resolve_sample_info(url)
        raw_mp4 = sample_info["raw_mp4"]
        title = sample_info["title"]
        duration = float(sample_info["duration"])
        transcript_hint = sample_info["transcript_hint"]

        check_ingest_guards(duration, sample_info["estimated_bytes"], target_dir=job_dir)
        shutil.copy2(raw_mp4, mezzanine_mp4)
        _extract_wav(mezzanine_mp4, audio_wav)
        meta = {
            "platform": platform,
            "video_id": canon["video_id"],
            "dedupe_key": canon["dedupe_key"],
            "title": title,
            "duration": duration,
            "source_url": url,
            "canonical_url": canon["canonical_url"],
            "chapters": [],
            "heatmap": [],
            "transcript_hint": transcript_hint,
        }
        meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    probe = probe_media(url, ydl_cls=ydl_cls)
    check_ingest_guards(probe.get("duration"), probe.get("estimated_bytes"), target_dir=job_dir)

    # Real URL download via yt-dlp
    ydl_factory = ydl_cls or yt_dlp.YoutubeDL
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
        with ydl_factory(ydl_opts) as ydl:
            info = ydl.extract_info(canon["canonical_url"], download=True) or {}
            downloaded = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
            if not downloaded.exists():
                candidates = list(source_dir.glob("raw.*"))
                if candidates:
                    downloaded = candidates[0]
    except Exception as exc:
        err = classify_yt_dlp_error(exc, platform=platform)
        if err.code == "LOGIN_REQUIRED":
            _raise_cookie_blocker(platform, str(exc))
        raise err from exc

    if not downloaded.exists():
        raise IngestError(
            "DOWNLOAD_FAILED",
            f"Downloaded media file was not created for {canon['canonical_url']}",
            retryable=True,
            platform=platform,
        )

    _normalize_mp4(downloaded, mezzanine_mp4)
    _extract_wav(mezzanine_mp4, audio_wav)
    duration = float(info.get("duration") or probe.get("duration") or _probe_duration(mezzanine_mp4))
    chapters = _normalize_chapters(info.get("chapters")) or probe.get("chapters") or []
    heatmap = _normalize_heatmap(info.get("heatmap")) or probe.get("heatmap") or []

    meta = {
        "platform": platform,
        "video_id": info.get("id") or probe.get("video_id"),
        "dedupe_key": probe.get("dedupe_key"),
        "title": info.get("title") or probe.get("title") or "Downloaded Video",
        "duration": duration,
        "source_url": url,
        "canonical_url": canon["canonical_url"],
        "uploader": info.get("uploader") or info.get("channel") or probe.get("uploader"),
        "chapters": chapters,
        "heatmap": heatmap,
        "subtitles_available": probe.get("subtitles_available") or [],
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

