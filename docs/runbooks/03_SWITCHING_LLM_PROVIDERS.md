# Runbook 3 — Switching LLM Providers (Ollama <-> Gemini <-> OpenAI)

## Overview
CLIPPER's `LLMProvider` (`backend/pipeline/llm/provider.py`) supports zero-cost local inference via **Ollama**, cloud inference via **Google Gemini** or **OpenAI**, and deterministic **`signals_only` heuristic fallback** when no LLM is reachable.

## Switching Providers via `.env`

### 1. Local Ollama (Default — Zero Cost)
```dotenv
CLIPPER_LLM_PROVIDER=ollama
CLIPPER_OLLAMA_BASE_URL=http://localhost:11434
CLIPPER_OLLAMA_MODEL=qwen2.5:7b-instruct
```

### 2. Google Gemini API
```dotenv
CLIPPER_LLM_PROVIDER=gemini
GEMINI_API_KEY=your_api_key_here
CLIPPER_GEMINI_MODEL=gemini-2.5-flash
```

### 3. OpenAI-Compatible API
```dotenv
CLIPPER_LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
CLIPPER_OPENAI_MODEL=gpt-4o-mini
```

### 4. Offline Signals-Only Degraded Mode
If both Ollama and cloud keys are absent, CLIPPER automatically degrades to `score_mode="signals_only"` (using audio energy, speech rate, laughter/reaction bursts, scene cuts, and face presence) and records a transparent warning on the job without crashing.
