from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from hitl.models import BlockerType, NeededItem
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

    queue_mode: str = "immediate"  # immediate | huey_sqlite
    redis_url: str = "redis://localhost:6379/0"

    asr_engine: str = "auto"       # auto | faster_whisper | heuristic_fallback
    asr_model_size: str = "small"
    asr_device: str = "auto"
    asr_compute_type: str = "int8"

    llm_provider: str = "auto"     # auto | gemini | ollama | heuristic
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"

    owner_email: str = ""
    notify_smtp_host: str = "smtp.gmail.com"
    notify_smtp_port: int = 587
    notify_smtp_user: str = ""
    notify_smtp_app_password: str = ""
    hitl_fallback_to_file: bool = True

    cookies_youtube: str = ""
    cookies_instagram: str = ""
    cookies_facebook: str = ""

    def load_yaml_config(self, name: str) -> dict[str, Any]:
        p = self.config_dir / f"{name}.yaml"
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

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
        create_or_get_blocker(
            btype=BlockerType.SECRET,
            task_id=task_id,
            title=f"Missing secret: {env_key}",
            needed=[NeededItem(key=env_key, where=".env", how_to_get=how_to_get)],
            why=why,
            alternatives=alternatives or [],
            root_dir=ROOT_DIR,
            raise_exc=strict,
        )
        return ""


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    (s.data_dir / "jobs").mkdir(parents=True, exist_ok=True)
    (s.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    return s
