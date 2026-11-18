from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.pipeline.ingest import downloader
from backend.pipeline.ingest.downloader import (
    IngestError,
    canonicalize_url,
    check_ingest_guards,
    classify_yt_dlp_error,
    detect_platform,
    download_and_normalize,
    probe_media,
    validate_ingest_url,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc123", "youtube"),
        ("https://youtube.com/shorts/abcDEF12345", "youtube"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "youtube"),
        ("https://www.instagram.com/reel/C12345/", "instagram"),
        ("https://instagr.am/p/C99999/", "instagram"),
        ("https://www.facebook.com/watch/?v=123456789", "facebook"),
        ("https://fb.watch/xyz987/", "facebook"),
        ("https://m.fb.com/reel/987654", "facebook"),
        ("https://drive.google.com/file/d/1AbC_dEfGhI2345/view?usp=sharing", "drive"),
        ("drive://1AbC_dEfGhI2345", "drive"),
        ("sample://sample_hinglish_podcast", "sample"),
        ("https://cdn.example.com/media/clip.mp4", "direct"),
        ("https://evil.example.com/?next=youtube.com/watch?v=123", "direct"),
    ],
)
def test_detect_platform_matrix(url: str, expected: str) -> None:
    assert detect_platform(url) == expected


def test_detect_platform_local_file(tmp_path: Path) -> None:
    local_mp4 = tmp_path / "local_upload.mp4"
    local_mp4.write_bytes(b"fake-mp4")
    assert detect_platform(str(local_mp4)) == "sample"


def test_canonicalize_url_strips_tracking_and_extracts_ids() -> None:
    yt_watch = canonicalize_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=x&utm_medium=social&fbclid=fb1&si=s1&feature=share&t=42"
    )
    assert yt_watch["platform"] == "youtube"
    assert yt_watch["video_id"] == "dQw4w9WgXcQ"
    assert yt_watch["dedupe_key"] == "youtube:dQw4w9WgXcQ"
    assert yt_watch["canonical_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42"

    yt_short_link = canonicalize_url("https://youtu.be/dQw4w9WgXcQ?si=sharetoken123")
    assert yt_short_link["platform"] == "youtube"
    assert yt_short_link["video_id"] == "dQw4w9WgXcQ"
    assert yt_short_link["canonical_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    yt_shorts = canonicalize_url("https://www.youtube.com/shorts/AbCdEf12345?feature=share")
    assert yt_shorts["video_id"] == "AbCdEf12345"
    assert yt_shorts["canonical_url"] == "https://www.youtube.com/shorts/AbCdEf12345"

    ig = canonicalize_url("https://www.instagram.com/reel/Cxyz123/?utm_source=ig_web_copy_link&igsh=abc")
    assert ig["platform"] == "instagram"
    assert ig["video_id"] == "Cxyz123"
    assert ig["dedupe_key"] == "instagram:Cxyz123"
    assert "utm_source" not in ig["canonical_url"]
    assert "igsh" not in ig["canonical_url"]

    fb = canonicalize_url("https://www.facebook.com/watch/?v=99887766&fbclid=IwAR123")
    assert fb["platform"] == "facebook"
    assert fb["video_id"] == "99887766"
    assert "fbclid" not in fb["canonical_url"]

    drv = canonicalize_url("https://drive.google.com/open?id=1AbC_dEfGhI2345&utm_campaign=test")
    assert drv["platform"] == "drive"
    assert drv["video_id"] == "1AbC_dEfGhI2345"
    assert drv["dedupe_key"] == "drive:1AbC_dEfGhI2345"
    assert drv["canonical_url"] == "https://drive.google.com/file/d/1AbC_dEfGhI2345/view"

    direct = canonicalize_url("https://cdn.example.com/video.mp4?token=keepme&utm_source=nl&fbclid=dropme")
    assert direct["platform"] == "direct"
    assert direct["video_id"] is None
    assert direct["canonical_url"] == "https://cdn.example.com/video.mp4?token=keepme"


@pytest.mark.parametrize(
    "blocked_url",
    [
        "",
        "   ",
        "file:///etc/passwd",
        "ftp://example.com/video.mp4",
        "gopher://127.0.0.1:6379/_INFO",
        "javascript:alert(1)",
        "http://127.0.0.1/admin",
        "http://localhost:8000/secret.mp4",
        "http://10.0.0.5/internal.mp4",
        "http://172.16.0.10/internal.mp4",
        "http://192.168.1.1/cam.mp4",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/cgnat.mp4",
        "http://0.0.0.0/video.mp4",
        "http://[::1]/video.mp4",
        "http://[::ffff:127.0.0.1]/video.mp4",
        "http://[fe80::1]/video.mp4",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://nas.local/video.mp4",
    ],
)
def test_validate_ingest_url_blocks_ssrf_and_unsafe_schemes(blocked_url: str) -> None:
    with pytest.raises(IngestError) as exc_info:
        validate_ingest_url(blocked_url, resolve_dns=False)
    assert exc_info.value.code == "UNSUPPORTED_URL"
    assert exc_info.value.retryable is False


def test_validate_ingest_url_blocks_dns_resolving_to_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(host: str, port: Any, type: int = 0) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(IngestError) as exc_info:
        validate_ingest_url("https://rebind.example.com/video.mp4", resolve_dns=True)
    assert exc_info.value.code == "UNSUPPORTED_URL"


def test_validate_ingest_url_allows_public_sample_and_local_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_getaddrinfo(host: str, port: Any, type: int = 0) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_ingest_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert validate_ingest_url("sample://sample_hinglish_podcast") == "sample://sample_hinglish_podcast"
    assert validate_ingest_url("drive://1AbC_dEfGhI2345") == "drive://1AbC_dEfGhI2345"

    local_mp4 = tmp_path / "upload.mp4"
    local_mp4.write_bytes(b"mp4")
    assert validate_ingest_url(str(local_mp4)) == str(local_mp4)


def test_check_ingest_guards_duration_and_disk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: SimpleNamespace(total=10_000, used=1_000, free=9_000))

    # Exactly 3x fits cleanly
    res = check_ingest_guards(duration_s=3600.0, estimated_bytes=3_000, target_dir=tmp_path, max_video_hours=3)
    assert res["ok"] is True
    assert res["required_bytes"] == 9_000

    # 3x + 1 exceeds free space
    with pytest.raises(IngestError) as disk_exc:
        check_ingest_guards(duration_s=120.0, estimated_bytes=3_001, target_dir=tmp_path, max_video_hours=3)
    assert disk_exc.value.code == "DISK_FULL"
    assert disk_exc.value.retryable is False

    # Duration over MAX_VIDEO_HOURS fails with TOO_LONG
    with pytest.raises(IngestError) as dur_exc:
        check_ingest_guards(duration_s=3 * 3600 + 1, estimated_bytes=100, target_dir=tmp_path, max_video_hours=3)
    assert dur_exc.value.code == "TOO_LONG"
    assert dur_exc.value.retryable is False


@pytest.mark.parametrize(
    ("message", "platform", "expected_code", "expected_retryable"),
    [
        ("HTTP Error 429: Too Many Requests", "youtube", "RATE_LIMITED", True),
        ("ERROR: Private video. Sign in if you've been granted access", "youtube", "PRIVATE_VIDEO", False),
        ("Video unavailable. The uploader has not made this video available in your country", "youtube", "GEO_BLOCKED", False),
        ("ERROR: Unsupported URL: https://example.com/nope", "direct", "UNSUPPORTED_URL", False),
        ("OSError: [Errno 28] No space left on device", "direct", "DISK_FULL", False),
        ("Sign in to confirm you’re not a bot", "youtube", "LOGIN_REQUIRED", False),
        ("HTTP Error 403: Forbidden", "instagram", "LOGIN_REQUIRED", False),
        ("Requested content is not available, login required", "facebook", "LOGIN_REQUIRED", False),
    ],
)
def test_classify_yt_dlp_error(
    message: str,
    platform: str,
    expected_code: str,
    expected_retryable: bool,
) -> None:
    err = classify_yt_dlp_error(RuntimeError(message), platform=platform)
    assert isinstance(err, IngestError)
    assert err.code == expected_code
    assert err.retryable is expected_retryable


def _create_tiny_mp4(dest: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def test_probe_media_and_download_with_mocked_yt_dlp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sample_mp4 = tmp_path / "fixture_raw.mp4"
    _create_tiny_mp4(sample_mp4)

    calls: list[dict[str, Any]] = []
    fake_info = {
        "id": "dQw4w9WgXcQ",
        "title": "Mocked Creator Keynote",
        "duration": 1.0,
        "uploader": "TestChannel",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "filesize_approx": 1024 * 1024,
        "chapters": [
            {"title": "Intro", "start_time": 0.0, "end_time": 0.5},
            {"title": "Hook", "start_time": 0.5, "end_time": 1.0},
        ],
        "heatmap": [
            {"start_time": 0.0, "end_time": 0.5, "value": 0.25},
            {"start_time": 0.5, "end_time": 1.0, "value": 0.98},
        ],
        "subtitles": {"en": [{"ext": "vtt"}]},
    }

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            calls.append({"url": url, "download": download, "opts": self.opts})
            if download:
                out_dir = Path(self.opts["outtmpl"]).parent
                shutil.copy2(sample_mp4, out_dir / "raw.mp4")
            return fake_info

        def prepare_filename(self, info: dict[str, Any]) -> str:
            out_dir = Path(self.opts["outtmpl"]).parent
            return str(out_dir / "raw.mp4")

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: SimpleNamespace(total=10**11, used=10**9, free=10**11))

    probe = probe_media("https://youtu.be/dQw4w9WgXcQ?si=tracking123", ydl_cls=FakeYDL)
    assert probe["platform"] == "youtube"
    assert probe["video_id"] == "dQw4w9WgXcQ"
    assert probe["dedupe_key"] == "youtube:dQw4w9WgXcQ"
    assert probe["canonical_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert probe["title"] == "Mocked Creator Keynote"
    assert len(probe["chapters"]) == 2
    assert probe["heatmap"][1]["value"] == 0.98
    assert probe["subtitles_available"] == ["en"]

    job_dir = tmp_path / "job_1"
    meta = download_and_normalize(
        "https://youtu.be/dQw4w9WgXcQ?si=tracking123&utm_source=test",
        job_dir,
        ydl_cls=FakeYDL,
    )
    assert (job_dir / "source" / "source.mp4").is_file()
    assert (job_dir / "audio" / "audio.wav").is_file()
    meta_on_disk = json.loads((job_dir / "source" / "metadata.json").read_text(encoding="utf-8"))

    assert meta["title"] == "Mocked Creator Keynote"
    assert meta_on_disk["video_id"] == "dQw4w9WgXcQ"
    assert meta_on_disk["dedupe_key"] == "youtube:dQw4w9WgXcQ"
    assert meta_on_disk["chapters"] == fake_info["chapters"]
    assert meta_on_disk["heatmap"] == fake_info["heatmap"]
    assert any(c["download"] is True for c in calls)
    assert all("si=" not in c["url"] and "utm_source" not in c["url"] for c in calls)


def test_download_aborts_before_download_on_guard_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    download_attempted = False

    class GuardFailYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> GuardFailYDL:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            nonlocal download_attempted
            if download:
                download_attempted = True
            return {
                "id": "long123",
                "title": "10 Hour Livestream",
                "duration": 10 * 3600,
                "filesize_approx": 1024,
            }

    with pytest.raises(IngestError) as exc_info:
        download_and_normalize("https://www.youtube.com/watch?v=long123", tmp_path / "job_long", ydl_cls=GuardFailYDL)
    assert exc_info.value.code == "TOO_LONG"
    assert download_attempted is False


def test_login_wall_raises_blocker_without_leaking_cookies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cookie_file = tmp_path / "ig_cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tTOP_SECRET_COOKIE_VALUE\n", encoding="utf-8")

    monkeypatch.setattr(downloader, "_cookie_file_for_platform", lambda _p: cookie_file)

    blocker_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        downloader,
        "create_or_get_blocker",
        lambda **kwargs: blocker_calls.append(kwargs),
    )

    class AuthWallYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> AuthWallYDL:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            raise RuntimeError("HTTP Error 403: Login required")

    with pytest.raises(IngestError) as exc_info:
        probe_media("https://www.instagram.com/reel/C12345/", ydl_cls=AuthWallYDL)

    assert exc_info.value.code == "LOGIN_REQUIRED"
    assert len(blocker_calls) == 1
    serialized_blocker = json.dumps(blocker_calls, default=str)
    assert "TOP_SECRET_COOKIE_VALUE" not in serialized_blocker
    assert "TOP_SECRET_COOKIE_VALUE" not in str(exc_info.value)
