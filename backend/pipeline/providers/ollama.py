from __future__ import annotations

from typing import Any

import httpx

from backend.app.config import get_settings

from .base import BaseLLMProvider, LLMUnavailableError


class OllamaProvider(BaseLLMProvider):
    """Local Ollama REST (`/api/chat`) JSON provider."""

    provider_name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        resolved_base = (
            base_url
            if base_url is not None
            else (settings.ollama_base_url or settings.ollama_host or "http://localhost:11434")
        )
        self.base_url = resolved_base.strip().rstrip("/")
        if not self.base_url:
            self.base_url = "http://localhost:11434"
        self.model = (model or settings.ollama_model or "qwen2.5:7b-instruct").strip()
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
        url = f"{self.base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": temperature,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            if self._client is not None:
                resp = self._client.post(url, json=payload, timeout=self.timeout)
            else:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    resp = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise LLMUnavailableError(
                f"Ollama returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            body: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise LLMUnavailableError(f"Ollama returned non-JSON response: {exc}") from exc

        message = body.get("message")
        if not isinstance(message, dict):
            raise LLMUnavailableError("Ollama response missing 'message' object")

        text = str(message.get("content", ""))
        if not text.strip():
            raise LLMUnavailableError("Ollama returned empty message content")

        usage: dict[str, int] = {}
        if "prompt_eval_count" in body:
            usage["prompt_tokens"] = int(body["prompt_eval_count"])
        if "eval_count" in body:
            usage["completion_tokens"] = int(body["eval_count"])
        if usage:
            usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)

        return text, usage
