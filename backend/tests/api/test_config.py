from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import config as config_mod
from backend.app.config import ROOT_DIR, Settings, get_settings
from hitl.models import BlockerRequired, BlockerType


def test_settings_defaults_and_spec_v2_fields(monkeypatch: pytest.MonkeyPatch):
    for key in (
        "APP_ENV",
        "QUEUE_URL",
        "STORAGE_BACKEND",
        "ADMIN_ENABLED",
        "ADMIN_PASSWORD_HASH",
        "OLLAMA_HOST",
        "OLLAMA_BASE_URL",
        "HF_TOKEN",
        "DRIVE_ENABLED",
        "DRIVE_SA_FILE",
        "DRIVE_ROOT_FOLDER_ID",
        "DRIVE_POLL_MINUTES",
        "MAX_VIDEO_HOURS",
        "NOTIFY_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)

    s = Settings(_env_file=None)
    assert s.app_env == "local"
    assert s.storage_backend == "local"
    assert s.queue_url.endswith("huey.db")
    assert s.admin_enabled is True
    assert s.admin_password_hash == ""
    assert s.ollama_base_url == "http://localhost:11434"
    assert s.ollama_host == "http://localhost:11434"
    assert s.hf_token == ""
    assert s.drive_enabled is False
    assert s.drive_sa_file == ROOT_DIR / "secrets" / "drive_sa.json"
    assert s.drive_root_folder_id == ""
    assert s.drive_poll_minutes == 5
    assert s.max_video_hours == 3
    assert s.notify_enabled is False


def test_settings_env_overrides_and_ollama_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("QUEUE_URL", "sqlite:///tmp/test_huey.db")
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("ADMIN_ENABLED", "false")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "$argon2id$v=19$m=65536,t=3,p=4$test")
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama-remote:11434")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setenv("DRIVE_ENABLED", "true")
    monkeypatch.setenv("DRIVE_SA_FILE", str(tmp_path / "sa.json"))
    monkeypatch.setenv("DRIVE_ROOT_FOLDER_ID", "folder_123")
    monkeypatch.setenv("DRIVE_POLL_MINUTES", "15")
    monkeypatch.setenv("MAX_VIDEO_HOURS", "6")
    monkeypatch.setenv("NOTIFY_ENABLED", "true")

    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.data_dir == tmp_path / "data"
        assert (tmp_path / "data" / "jobs").is_dir()
        assert (tmp_path / "data" / "uploads").is_dir()
        assert s.queue_url == "sqlite:///tmp/test_huey.db"
        assert s.storage_backend == "s3"
        assert s.admin_enabled is False
        assert s.admin_password_hash.startswith("$argon2id$")
        assert s.ollama_host == "http://ollama-remote:11434"
        assert s.ollama_base_url == "http://ollama-remote:11434"
        assert s.hf_token == "hf_test_token"
        assert s.drive_enabled is True
        assert s.drive_sa_file == tmp_path / "sa.json"
        assert s.drive_root_folder_id == "folder_123"
        assert s.drive_poll_minutes == 15
        assert s.max_video_hours == 6
        assert s.notify_enabled is True
    finally:
        get_settings.cache_clear()


def test_ollama_base_url_fallback_to_ollama_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:22434")
    s = Settings(_env_file=None)
    assert s.ollama_base_url == "http://127.0.0.1:22434"
    assert s.ollama_host == "http://127.0.0.1:22434"


def test_load_yaml_config_v1_and_spec_v2(tmp_path: Path):
    s = Settings(_env_file=None)

    asr = s.load_yaml_config("asr")
    assert asr["default_language"] == "auto"
    assert "hinglish" in asr["supported_languages"]

    highlights = s.load_yaml_config("highlights")
    assert "quality_threshold" in highlights
    assert "weights" in highlights

    render = s.load_yaml_config("render")
    assert render["default_aspect"] == "9:16"
    assert "resolutions" in render

    drive = s.load_yaml_config("drive")
    assert drive["version"] == 1
    assert "routing" in drive

    platforms = s.load_yaml_config("platforms")
    assert platforms["version"] == 1
    assert "youtube_shorts" in platforms
    assert "safe_areas" in platforms

    # Spec v2 configs load cleanly via helper or flag without breaking v1 defaults
    asr_v2 = s.load_spec_v2_yaml_config("asr")
    assert asr_v2["version"] == 1
    assert "engines" in asr_v2

    highlights_v2 = s.load_yaml_config("highlights", spec_v2=True)
    assert highlights_v2["version"] == 1
    assert "selection" in highlights_v2

    render_v2 = s.load_spec_v2_yaml_config("render")
    assert render_v2["version"] == 1
    assert "profiles" in render_v2

    # Fallback when file only exists in spec_v2 or only in root config dir
    assert s.load_spec_v2_yaml_config("drive")["version"] == 1
    assert s.load_yaml_config("does_not_exist") == {}

    custom_cfg_dir = tmp_path / "config"
    (custom_cfg_dir / "spec_v2").mkdir(parents=True)
    (custom_cfg_dir / "spec_v2" / "only_v2.yaml").write_text("version: 2\n", encoding="utf-8")
    custom_s = Settings(_env_file=None, config_dir=custom_cfg_dir)
    assert custom_s.load_yaml_config("only_v2") == {"version": 2}


def test_require_secret_strict_raises_blocker_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(config_mod, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("NOTIFY_SMTP_USER", raising=False)
    monkeypatch.delenv("NOTIFY_SMTP_APP_PASSWORD", raising=False)
    monkeypatch.delenv("OWNER_EMAIL", raising=False)

    s = Settings(_env_file=None, gemini_api_key="")
    with pytest.raises(BlockerRequired) as exc_info:
        s.require_secret(
            attr_name="gemini_api_key",
            env_key="GEMINI_API_KEY",
            task_id="T-0.5",
            how_to_get="https://aistudio.google.com/app/apikey",
            why="Required for Gemini highlight scoring test.",
            strict=True,
            alternatives=["Set LLM_PROVIDER=ollama"],
        )

    blocker = exc_info.value.blocker
    assert blocker.type == BlockerType.SECRET
    assert blocker.task_id == "T-0.5"
    assert blocker.needed[0].key == "GEMINI_API_KEY"
    blocker_file_v2 = tmp_path / ".clipper-state" / "blockers" / f"{blocker.id}.yaml"
    blocker_file_v1 = tmp_path / ".agent" / "blockers" / f"{blocker.id}.yaml"
    assert blocker_file_v2.exists() or blocker_file_v1.exists()
    assert (tmp_path / "BLOCKERS.md").exists()

    # Non-strict mode returns empty string cleanly
    s_hf = Settings(_env_file=None, hf_token="")
    assert (
        s_hf.require_secret(
            attr_name="hf_token",
            env_key="HF_TOKEN",
            task_id="T-0.5",
            how_to_get="https://huggingface.co/settings/tokens",
            why="Optional gated ASR models.",
            strict=False,
        )
        == ""
    )

    # Configured secret returns stripped value without raising
    s_ok = Settings(_env_file=None, gemini_api_key="  test-key-123  ")
    assert (
        s_ok.require_secret(
            attr_name="gemini_api_key",
            env_key="GEMINI_API_KEY",
            task_id="T-0.5",
            how_to_get="https://aistudio.google.com/app/apikey",
            why="Required for Gemini.",
            strict=True,
        )
        == "test-key-123"
    )
