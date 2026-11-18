from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.config import Settings
from backend.pipeline.ingest import downloader
from backend.pipeline.ingest.cookies import (
    redact_cookie_secrets,
    resolve_cookie_file,
    validate_netscape_cookie_file,
)
from backend.pipeline.ingest.downloader import (
    BlockerRequired,
    IngestError,
    classify_yt_dlp_error,
    download_and_normalize,
    probe_media,
)
from hitl.models import BlockerType
from hitl.raise_blocker import list_blockers


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


def _make_fake_ydl_cls(
    sample_mp4: Path,
    fake_info: dict[str, Any],
    recorded_calls: list[dict[str, Any]],
) -> type[Any]:
    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            recorded_calls.append({"url": url, "download": download, "opts": dict(self.opts)})
            if download:
                out_dir = Path(self.opts["outtmpl"]).parent
                shutil.copy2(sample_mp4, out_dir / "raw.mp4")
            return fake_info

        def prepare_filename(self, info: dict[str, Any]) -> str:
            out_dir = Path(self.opts["outtmpl"]).parent
            return str(out_dir / "raw.mp4")

    return FakeYDL


def test_validate_netscape_cookie_file_states_and_non_leaking(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret_val = "ULTRA_SECRET_SESSION_987654"

    # Missing
    missing = validate_netscape_cookie_file(tmp_path / "missing.txt", platform="instagram")
    assert missing.status == "missing"
    assert missing.is_valid is False
    assert missing.valid_path is None

    # Valid with header + HttpOnly entry
    valid_path = tmp_path / "valid.txt"
    valid_path.write_text(
        "# Netscape HTTP Cookie File\n"
        "# This is a comment\n"
        f".instagram.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\t{secret_val}\n"
        f"#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\trur\t{secret_val}_rur\n",
        encoding="utf-8",
    )
    valid_res = validate_netscape_cookie_file(valid_path, platform="instagram")
    assert valid_res.status == "valid"
    assert valid_res.is_valid is True
    assert valid_res.has_header is True
    assert valid_res.cookie_count == 2
    assert valid_res.valid_path == valid_path
    assert secret_val not in valid_res.reason

    # Valid 7-column without header
    no_hdr_path = tmp_path / "no_header.txt"
    no_hdr_path.write_text(
        f".facebook.com\tTRUE\t/\tTRUE\t1893456000\tc_user\t{secret_val}\n",
        encoding="utf-8",
    )
    no_hdr_res = validate_netscape_cookie_file(no_hdr_path, platform="facebook")
    assert no_hdr_res.status == "valid"
    assert no_hdr_res.cookie_count == 1
    assert secret_val not in no_hdr_res.reason

    # Invalid variants (empty, binary, JSON, HTML, malformed columns, comment-only)
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("   \n", encoding="utf-8")
    assert validate_netscape_cookie_file(empty_file).status == "invalid"

    bin_file = tmp_path / "binary.txt"
    bin_file.write_bytes(b"\x00\xff\xfe\x89PNG" + secret_val.encode())
    bin_res = validate_netscape_cookie_file(bin_file)
    assert bin_res.status == "invalid"
    assert secret_val not in bin_res.reason

    json_file = tmp_path / "cookies.json"
    json_file.write_text(json.dumps([{"name": "sessionid", "value": secret_val}]), encoding="utf-8")
    json_res = validate_netscape_cookie_file(json_file)
    assert json_res.status == "invalid"
    assert secret_val not in json_res.reason

    html_file = tmp_path / "login.html"
    html_file.write_text(f"<html><body>{secret_val}</body></html>", encoding="utf-8")
    html_res = validate_netscape_cookie_file(html_file)
    assert html_res.status == "invalid"
    assert secret_val not in html_res.reason

    bad_cols = tmp_path / "bad_cols.txt"
    bad_cols.write_text(f".instagram.com\tTRUE\t/\t{secret_val}\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        bad_res = resolve_cookie_file("instagram", cookie_file=bad_cols)
    assert bad_res.status == "invalid"
    assert bad_res.valid_path is None
    assert secret_val not in bad_res.reason
    assert secret_val not in caplog.text

    comment_only = tmp_path / "comments.txt"
    comment_only.write_text(f"# just a comment {secret_val}\n", encoding="utf-8")
    comment_res = validate_netscape_cookie_file(comment_only)
    assert comment_res.status == "invalid"
    assert secret_val not in comment_res.reason


def test_public_instagram_reel_download_succeeds_without_cookies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sample_mp4 = tmp_path / "fixture_ig.mp4"
    _create_tiny_mp4(sample_mp4)

    empty_secrets = tmp_path / "secrets"
    settings = Settings(secrets_dir=empty_secrets, data_dir=tmp_path / "data")
    monkeypatch.setattr(downloader, "get_settings", lambda: settings)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: SimpleNamespace(total=10**11, used=10**9, free=10**11)
    )

    calls: list[dict[str, Any]] = []
    fake_info = {
        "id": "C_PUB_REEL_1",
        "title": "Public Creator Reel",
        "duration": 1.0,
        "uploader": "creator_public",
        "filesize_approx": 512_000,
    }
    ydl_cls = _make_fake_ydl_cls(sample_mp4, fake_info, calls)

    job_dir = tmp_path / "job_ig_public"
    meta = download_and_normalize(
        "https://www.instagram.com/reel/C_PUB_REEL_1/?igsh=tracking123&utm_source=ig",
        job_dir,
        ydl_cls=ydl_cls,
    )

    assert meta["platform"] == "instagram"
    assert meta["video_id"] == "C_PUB_REEL_1"
    assert meta["dedupe_key"] == "instagram:C_PUB_REEL_1"
    assert (job_dir / "source" / "source.mp4").is_file()
    assert (job_dir / "audio" / "audio.wav").is_file()
    assert len(calls) == 2
    assert all("cookiefile" not in c["opts"] for c in calls)


@pytest.mark.parametrize(
    ("platform", "url", "cookie_name", "secret_val"),
    [
        (
            "instagram",
            "https://www.instagram.com/reel/C_PRIVATE_99/",
            "sessionid",
            "IG_SUPER_SECRET_COOKIE_112233",
        ),
        (
            "facebook",
            "https://www.facebook.com/reel/8877665544/",
            "c_user",
            "FB_SUPER_SECRET_COOKIE_445566",
        ),
    ],
)
def test_valid_netscape_cookies_passed_to_yt_dlp_without_logging_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    platform: str,
    url: str,
    cookie_name: str,
    secret_val: str,
) -> None:
    sample_mp4 = tmp_path / f"fixture_{platform}.mp4"
    _create_tiny_mp4(sample_mp4)

    secrets_dir = tmp_path / "secrets"
    cookies_dir = secrets_dir / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = cookies_dir / f"{platform}.txt"
    cookie_path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".{platform}.com\tTRUE\t/\tTRUE\t1893456000\t{cookie_name}\t{secret_val}\n",
        encoding="utf-8",
    )

    settings = Settings(secrets_dir=secrets_dir, data_dir=tmp_path / "data")
    monkeypatch.setattr(downloader, "get_settings", lambda: settings)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: SimpleNamespace(total=10**11, used=10**9, free=10**11)
    )

    calls: list[dict[str, Any]] = []
    fake_info = {
        "id": "VID_WITH_COOKIES",
        "title": f"Authenticated {platform} Video",
        "duration": 1.0,
        "uploader": "auth_user",
        "filesize_approx": 512_000,
    }
    ydl_cls = _make_fake_ydl_cls(sample_mp4, fake_info, calls)

    with caplog.at_level(logging.DEBUG):
        meta = download_and_normalize(url, tmp_path / f"job_{platform}", ydl_cls=ydl_cls)

    assert meta["platform"] == platform
    assert len(calls) == 2
    assert all(c["opts"].get("cookiefile") == str(cookie_path) for c in calls)
    assert secret_val not in caplog.text

    # Also verify explicit cookie_file override works
    override_path = tmp_path / f"override_{platform}.txt"
    override_path.write_text(
        f".{platform}.com\tTRUE\t/\tTRUE\t1893456000\t{cookie_name}\t{secret_val}_OVR\n",
        encoding="utf-8",
    )
    calls.clear()
    probe = probe_media(url, ydl_cls=ydl_cls, cookie_file=override_path)
    assert probe["platform"] == platform
    assert calls[0]["opts"].get("cookiefile") == str(override_path)


def test_invalid_cookie_file_ignored_safely_without_leaking_contents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sample_mp4 = tmp_path / "fixture_ig_invalid_cookie.mp4"
    _create_tiny_mp4(sample_mp4)

    secret_val = "DO_NOT_LEAK_BAD_COOKIE_TOKEN_XYZ"
    cookies_dir = tmp_path / "custom_cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    bad_cookie = cookies_dir / "instagram.txt"
    bad_cookie.write_text(f'{{"sessionid": "{secret_val}"}}', encoding="utf-8")

    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: SimpleNamespace(total=10**11, used=10**9, free=10**11)
    )

    calls: list[dict[str, Any]] = []
    fake_info = {
        "id": "C_PUBLIC_2",
        "title": "Public Reel Despite Bad Cookies",
        "duration": 1.0,
        "filesize_approx": 256_000,
    }
    ydl_cls = _make_fake_ydl_cls(sample_mp4, fake_info, calls)

    with caplog.at_level(logging.WARNING):
        meta = download_and_normalize(
            "https://www.instagram.com/reel/C_PUBLIC_2/",
            tmp_path / "job_bad_cookie",
            ydl_cls=ydl_cls,
            cookies_dir=cookies_dir,
        )

    assert meta["platform"] == "instagram"
    assert all("cookiefile" not in c["opts"] for c in calls)
    assert "Ignoring invalid cookie file" in caplog.text
    assert secret_val not in caplog.text


@pytest.mark.parametrize(
    ("error_msg", "platform", "expected_code"),
    [
        ("HTTP Error 401: Unauthorized", "instagram", "LOGIN_REQUIRED"),
        ("HTTP Error 403: Forbidden", "facebook", "LOGIN_REQUIRED"),
        ("ERROR: [Instagram] checkpoint_required: verify your account", "instagram", "LOGIN_REQUIRED"),
        ("ERROR: challenge_required - please log in", "facebook", "LOGIN_REQUIRED"),
        ("ERROR: Your session expired or cookies are no longer valid", "instagram", "LOGIN_REQUIRED"),
        ("ERROR: Instagram sent an empty media response", "instagram", "LOGIN_REQUIRED"),
        ("ERROR: This account is private", "instagram", "PRIVATE_VIDEO"),
        ("ERROR: Video not available in your region", "facebook", "GEO_BLOCKED"),
        ("HTTP Error 429: Too Many Requests", "instagram", "RATE_LIMITED"),
    ],
)
def test_classify_ig_fb_errors_and_redact_secrets(
    error_msg: str, platform: str, expected_code: str
) -> None:
    err = classify_yt_dlp_error(RuntimeError(error_msg), platform=platform)
    assert err.code == expected_code

    secret_msg = "HTTP Error 403: Cookie: sessionid=LEAKED_SECRET_999; xs=LEAKED_XS_111"
    redacted = redact_cookie_secrets(secret_msg)
    assert "LEAKED_SECRET_999" not in redacted
    assert "LEAKED_XS_111" not in redacted
    classified = classify_yt_dlp_error(RuntimeError(secret_msg), platform="instagram")
    assert "LEAKED_SECRET_999" not in str(classified)
    assert "LEAKED_XS_111" not in str(classified)


@pytest.mark.parametrize(
    ("platform", "url", "expected_key", "ydl_error"),
    [
        (
            "instagram",
            "https://www.instagram.com/reel/C_GATED_IG/",
            "COOKIES_INSTAGRAM",
            "HTTP Error 403: Login required; sessionid=RAW_SECRET_IG_TOKEN",
        ),
        (
            "facebook",
            "https://www.facebook.com/watch/?v=1234567890",
            "COOKIES_FACEBOOK",
            "ERROR: checkpoint_required - verify your account; c_user=RAW_SECRET_FB_TOKEN",
        ),
    ],
)
def test_login_wall_raises_blocker_required_and_persists_credential_blocker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    platform: str,
    url: str,
    expected_key: str,
    ydl_error: str,
) -> None:
    secrets_dir = tmp_path / "secrets"
    cookies_dir = secrets_dir / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = cookies_dir / f"{platform}.txt"
    cookie_val = f"SUPER_SECRET_{platform.upper()}_COOKIE"
    cookie_path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".{platform}.com\tTRUE\t/\tTRUE\t1893456000\tsessionid\t{cookie_val}\n",
        encoding="utf-8",
    )

    settings = Settings(
        secrets_dir=secrets_dir,
        data_dir=tmp_path / "data",
        notify_enabled=False,
        hitl_fallback_to_file=True,
    )
    monkeypatch.setattr(downloader, "get_settings", lambda: settings)

    class AuthWallYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> AuthWallYDL:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            raise RuntimeError(ydl_error)

    state_dir = tmp_path / ".clipper-state"
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(BlockerRequired) as exc_info:
            download_and_normalize(
                url,
                tmp_path / f"job_{platform}_gated",
                ydl_cls=AuthWallYDL,
                root_dir=tmp_path,
                state_dir=state_dir,
            )

    blocker = exc_info.value.blocker
    assert blocker.type == BlockerType.CREDENTIAL
    assert blocker.task_id == "T-2.2"
    assert blocker.needed[0].key == expected_key
    assert blocker.needed[0].where == f"secrets/cookies/{platform}.txt"
    assert "Netscape" in blocker.needed[0].how_to_get

    persisted = list_blockers(tmp_path, state_dir=state_dir)
    assert len(persisted) == 1
    assert persisted[0].id == blocker.id
    yaml_path = state_dir / "blockers" / f"{blocker.id}.yaml"
    assert yaml_path.is_file()
    yaml_text = yaml_path.read_text(encoding="utf-8")

    # Verify NO raw cookie or error-echoed token leaks anywhere
    for raw_secret in (cookie_val, "RAW_SECRET_IG_TOKEN", "RAW_SECRET_FB_TOKEN"):
        assert raw_secret not in yaml_text
        assert raw_secret not in caplog.text
        assert raw_secret not in str(exc_info.value)

    # Also verify non-raising mode attaches blocker_id cleanly onto IngestError
    with pytest.raises(IngestError) as ingest_exc:
        probe_media(
            url,
            ydl_cls=AuthWallYDL,
            raise_blocker=False,
            root_dir=tmp_path,
            state_dir=state_dir,
        )
    assert ingest_exc.value.code == "LOGIN_REQUIRED"
    assert ingest_exc.value.blocker_id == blocker.id
