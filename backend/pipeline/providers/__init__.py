from __future__ import annotations

from backend.app.config import get_settings

from .base import (
    MAX_TEMPERATURE,
    UNTRUSTED_DATA_SYSTEM_INSTRUCTION,
    UNTRUSTED_DATA_TAG,
    BaseLLMProvider,
    LLMError,
    LLMProvider,
    LLMResult,
    LLMUnavailableError,
    SchemaValidationError,
    generate_json,
    parse_and_validate_json,
    strip_markdown_fences,
    wrap_untrusted_transcript,
)
from .gemini import GeminiProvider
from .ollama import OllamaProvider

__all__ = [
    "BaseLLMProvider",
    "GeminiProvider",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "LLMUnavailableError",
    "MAX_TEMPERATURE",
    "OllamaProvider",
    "SchemaValidationError",
    "UNTRUSTED_DATA_SYSTEM_INSTRUCTION",
    "UNTRUSTED_DATA_TAG",
    "generate_json",
    "get_llm_provider",
    "parse_and_validate_json",
    "strip_markdown_fences",
    "wrap_untrusted_transcript",
]


def get_llm_provider(
    strict: bool = False,
    *,
    provider: str | None = None,
) -> LLMProvider:
    """Resolve an LLMProvider instance from CLIPPER settings."""
    settings = get_settings()
    choice = (provider or settings.llm_provider or "auto").strip().lower()

    if choice == "gemini":
        if not settings.gemini_api_key.strip():
            if strict:
                return GeminiProvider(strict=True)
            raise LLMUnavailableError(
                "GEMINI_API_KEY is missing; configure GEMINI_API_KEY or set LLM_PROVIDER=ollama."
            )
        return GeminiProvider(
            api_key=settings.gemini_api_key.strip(),
            model=settings.gemini_model,
            strict=strict,
        )

    if choice == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url or settings.ollama_host,
            model=settings.ollama_model,
        )

    if choice == "auto":
        if settings.gemini_api_key.strip():
            return GeminiProvider(
                api_key=settings.gemini_api_key.strip(),
                model=settings.gemini_model,
                strict=strict,
            )
        if (settings.ollama_base_url or settings.ollama_host).strip():
            return OllamaProvider(
                base_url=settings.ollama_base_url or settings.ollama_host,
                model=settings.ollama_model,
            )
        if strict:
            return GeminiProvider(strict=True)
        raise LLMUnavailableError("No LLM provider configured (missing GEMINI_API_KEY and OLLAMA_BASE_URL).")

    if choice in {"heuristic", "none", "off"}:
        raise LLMUnavailableError(f"LLM provider is disabled (llm_provider={choice!r}).")

    raise LLMUnavailableError(f"Unknown LLM provider: {choice!r}")
