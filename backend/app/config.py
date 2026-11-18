from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hitl.models import BlockerRequired, BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = ROOT_DIR / "data"
    assets_dir: Path = ROOT_DIR / "assets"
    config_dir: Path = ROOT_DIR / "config"
    secrets_dir: Path = ROOT_DIR / "secrets"
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'clipper.db'}"

    queue_mode: str = "immediate"  # immediate | huey_sqlite | huey_redis
    queue_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'huey.db'}"
    redis_url: str = "redis://localhost:6379/0"
    storage_backend: str = "local"  # local | s3 | r2
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "auto"

    admin_enabled: bool = True
    admin_password_hash: str = ""

    asr_engine: str = "auto"       # auto | faster_whisper | heuristic_fallback
    asr_model_size: str = "small"
    asr_device: str = "auto"
    asr_compute_type: str = "int8"

    llm_provider: str = "auto"     # auto | gemini | ollama | heuristic
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_host: str = ""
    ollama_model: str = "qwen2.5:7b-instruct"
    hf_token: str = ""

    max_video_hours: int = 3

    drive_enabled: bool = False
    drive_sa_file: Path = ROOT_DIR / "secrets" / "drive_sa.json"
    drive_root_folder_id: str = ""
    drive_poll_minutes: int = 5

    owner_email: str = ""
    notify_enabled: bool = False
    notify_smtp_host: str = "smtp.gmail.com"
    notify_smtp_port: int = 587
    notify_smtp_user: str = ""
    notify_smtp_app_password: str = ""
    notify_imap_host: str = "imap.gmail.com"
    notify_imap_port: int = 993
    hitl_fallback_to_file: bool = True

    cookies_youtube: str = ""
    cookies_instagram: str = ""
    cookies_facebook: str = ""

    @model_validator(mode="after")
    def _sync_ollama_urls(self) -> Settings:
        host = self.ollama_host.strip()
        base = self.ollama_base_url.strip()
        if (
            "ollama_host" in self.model_fields_set
            and host
            and "ollama_base_url" not in self.model_fields_set
        ):
            self.ollama_base_url = host
        elif not host:
            self.ollama_host = base or "http://localhost:11434"
        if not self.ollama_base_url.strip():
            self.ollama_base_url = self.ollama_host or "http://localhost:11434"
        return self

    def load_yaml_config(self, name: str, spec_v2: bool = False) -> dict[str, Any]:
        clean_name = name[:-5] if name.endswith(".yaml") else name
        v1_path = self.config_dir / f"{clean_name}.yaml"
        v2_path = self.config_dir / "spec_v2" / f"{clean_name}.yaml"
        candidates = [v2_path, v1_path] if spec_v2 else [v1_path, v2_path]
        for p in candidates:
            if p.exists():
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return {}

    def load_spec_v2_yaml_config(self, name: str) -> dict[str, Any]:
        return self.load_yaml_config(name, spec_v2=True)

    def require_secret(
        self,
        attr_name: str,
        env_key: str,
        task_id: str,
        how_to_get: str,
        why: str,
        strict: bool = False,
        alternatives: list[str] | None = None,
    ) -> str:
        val = getattr(self, attr_name, "")
        if val and str(val).strip():
            return str(val).strip()
        blocker = create_or_get_blocker(
            btype=BlockerType.SECRET,
            task_id=task_id,
            title=f"Missing secret: {env_key}",
            needed=[NeededItem(key=env_key, where=".env", how_to_get=how_to_get)],
            why=why,
            alternatives=alternatives or [],
            root_dir=ROOT_DIR,
            raise_exc=strict,
        )
        if strict:
            raise BlockerRequired(blocker)
        return ""


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    (s.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    return s
