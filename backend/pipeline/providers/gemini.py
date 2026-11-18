from __future__ import annotations

from typing import Any

import httpx

from backend.app import config as app_config
from hitl.models import BlockerRequired, BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker

from .base import BaseLLMProvider, LLMUnavailableError

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(BaseLLMProvider):
    """Google Gemini REST (`generateContent`) JSON provider."""

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        strict: bool = False,
    ) -> None:
        settings = app_config.get_settings()
        resolved_key = settings.gemini_api_key.strip() if api_key is None else api_key.strip()
        if not resolved_key and strict:
            if api_key is None:
                settings.require_secret(
                    attr_name="gemini_api_key",
                    env_key="GEMINI_API_KEY",
                    task_id="T-5.1",
                    how_to_get="https://aistudio.google.com/app/apikey",
                    why="GEMINI_API_KEY is required when strict=True for GeminiProvider.",
                    strict=True,
                    alternatives=["Configure OLLAMA_BASE_URL and set LLM_PROVIDER=ollama."],
                )
            blocker = create_or_get_blocker(
                btype=BlockerType.SECRET,
                task_id="T-5.1",
                title="Missing secret: GEMINI_API_KEY",
                needed=[
                    NeededItem(
                        key="GEMINI_API_KEY",
                        where=".env",
                        how_to_get="https://aistudio.google.com/app/apikey",
                    )
                ],
                why="GEMINI_API_KEY is required when strict=True for GeminiProvider.",
                alternatives=["Configure OLLAMA_BASE_URL and set LLM_PROVIDER=ollama."],
                root_dir=app_config.ROOT_DIR,
                raise_exc=True,
            )
            raise BlockerRequired(blocker)

        self.api_key = resolved_key
        self.model = (model or settings.gemini_model or "gemini-2.5-flash").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self._client = client

    def _complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        temperature: float,
    ) -> tuple[str, dict[str, int]]:
        del schema  # Full JSON Schema validation is performed locally via jsonschema.
        if not self.api_key:
            raise LLMUnavailableError(
                "GEMINI_API_KEY is not configured for GeminiProvider."
            )

        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

        try:
            if self._client is not None:
                resp = self._client.post(url, json=payload, headers=headers, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    resp = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Gemini request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMUnavailableError(
                f"Gemini API returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            body: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise LLMUnavailableError(f"Gemini API returned non-JSON response: {exc}") from exc

        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise LLMUnavailableError("Gemini API returned no candidates")

        first_candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        content = first_candidate.get("content") if isinstance(first_candidate, dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list) or not parts:
            raise LLMUnavailableError("Gemini API candidate contained no content parts")

        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        if not text.strip():
            raise LLMUnavailableError("Gemini API returned empty text content")

        raw_usage = body.get("usageMetadata") or {}
        usage: dict[str, int] = {}
        if isinstance(raw_usage, dict):
            if "promptTokenCount" in raw_usage:
                usage["prompt_tokens"] = int(raw_usage["promptTokenCount"])
            if "candidatesTokenCount" in raw_usage:
                usage["completion_tokens"] = int(raw_usage["candidatesTokenCount"])
            if "totalTokenCount" in raw_usage:
                usage["total_tokens"] = int(raw_usage["totalTokenCount"])

        return text, usage
