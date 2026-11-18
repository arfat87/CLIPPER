from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.app.config import get_settings
from backend.pipeline.highlights.segmenter import (
    CandidateWindow,
    flatten_transcript_words,
    load_highlights_v2_config,
    resolve_window_bounds,
)
from backend.pipeline.providers import (
    LLMError,
    LLMProvider,
    get_llm_provider,
    wrap_untrusted_transcript,
)

# Alias so callers importing wrap_untrusted_data from scorer or providers work seamlessly
wrap_untrusted_data = wrap_untrusted_transcript

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_HASHTAG_CLEAN_RE = re.compile(r"[^\w\u0900-\u09FF]+", re.UNICODE)

DEFAULT_DIMENSION_WEIGHTS: dict[str, float] = {
    "hook": 0.25,
    "payoff": 0.25,
    "standalone": 0.20,
    "emotion": 0.10,
    "shareability": 0.10,
    "quotability": 0.10,
}

MAP_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["moments"],
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "start_word_idx",
                    "end_word_idx",
                    "hook",
                    "payoff",
                    "standalone",
                    "emotion",
                    "shareability",
                    "quotability",
                    "title",
                    "hook_line",
                    "reason",
                    "hashtags",
                ],
                "properties": {
                    "start_word_idx": {"type": "integer", "minimum": 0},
                    "end_word_idx": {"type": "integer", "minimum": 0},
                    "hook": {"type": "integer", "minimum": 0, "maximum": 100},
                    "payoff": {"type": "integer", "minimum": 0, "maximum": 100},
                    "standalone": {"type": "integer", "minimum": 0, "maximum": 100},
                    "emotion": {"type": "integer", "minimum": 0, "maximum": 100},
                    "shareability": {"type": "integer", "minimum": 0, "maximum": 100},
                    "quotability": {"type": "integer", "minimum": 0, "maximum": 100},
                    "brief_match": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "title": {"type": "string", "minLength": 1},
                    "hook_line": {"type": "string"},
                    "reason": {"type": "string"},
                    "hashtags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        }
    },
}

REDUCE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ranked"],
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "final_score", "keep", "reason"],
                "properties": {
                    "id": {"type": "integer", "minimum": 0},
                    "final_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "keep": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "duplicate_of": {"type": ["integer", "null"]},
                },
            },
        }
    },
}


@dataclass(slots=True)
class ScoredMoment:
    """Semantic LLM scoring output for a candidate window or moment."""

    candidate_id: str
    start_word_idx: int
    end_word_idx: int
    start: float
    end: float
    dimensions: dict[str, int]
    brief_match: int | None
    llm_score: float
    title: str
    hook_line: str
    reason: str
    hashtags: list[str]
    keep: bool = True
    duplicate_of: int | None = None
    provider: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "start_word_idx": self.start_word_idx,
            "end_word_idx": self.end_word_idx,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "dimensions": dict(self.dimensions),
            "brief_match": self.brief_match,
            "llm_score": round(self.llm_score, 4),
            "title": self.title,
            "hook_line": self.hook_line,
            "reason": self.reason,
            "hashtags": list(self.hashtags),
            "keep": self.keep,
            "duplicate_of": self.duplicate_of,
            "provider": self.provider,
        }


@dataclass(slots=True)
class MapReduceScoreResult:
    """Output of the map-reduce LLM highlight scorer."""

    moments: dict[str, ScoredMoment] = field(default_factory=dict)
    raw_moments: list[ScoredMoment] = field(default_factory=list)
    score_mode: str = "full"
    provider: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    token_usage: int = 0


def load_prompt_template(name: str) -> str:
    """Load prompt file from `config/prompts/<name>.md` stripping YAML frontmatter."""
    settings = get_settings()
    prompt_path: Path = settings.config_dir / "prompts" / f"{name}.md"
    raw = prompt_path.read_text(encoding="utf-8")
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return raw.strip()


def sanitize_title(title: str, max_len: int = 70) -> str:
    """Sanitize LLM-generated title: strip URLs, control chars, angle brackets, and cap length <= 70."""
    cleaned = _URL_RE.sub("", title or "")
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("<", "").replace(">", "")
    cleaned = " ".join(cleaned.split()).strip()
    if not cleaned:
        cleaned = "Key Highlight"
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def sanitize_hook_line(hook_line: str, max_len: int = 120) -> str:
    """Sanitize LLM-generated hook line: strip URLs and control characters."""
    cleaned = _URL_RE.sub("", hook_line or "")
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("<", "").replace(">", "")
    cleaned = " ".join(cleaned.split()).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def sanitize_hashtags(hashtags: list[str] | Any, language: str = "en") -> list[str]:
    """Sanitize hashtags: strip URLs, spaces, punctuation, enforce `#` prefix and 3-5 count."""
    sanitized: list[str] = []
    if isinstance(hashtags, list):
        for tag in hashtags:
            if not isinstance(tag, str):
                continue
            no_url = _URL_RE.sub("", tag)
            body = _HASHTAG_CLEAN_RE.sub("", no_url.lstrip("#")).strip()
            if not body:
                continue
            formatted = f"#{body[:32]}"
            if formatted.lower() not in {t.lower() for t in sanitized}:
                sanitized.append(formatted)
            if len(sanitized) >= 5:
                break

    defaults = ["#shorts", "#reels", "#viral", "#clipper", f"#{language}"]
    for d in defaults:
        if len(sanitized) >= 3:
            break
        if d.lower() not in {t.lower() for t in sanitized}:
            sanitized.append(d)
    return sanitized[:5]


def compute_composite_llm_score(
    dimensions: Mapping[str, int | float],
    dimension_weights: Mapping[str, float] | None = None,
    *,
    clip_brief: str | None = None,
    brief_match: int | float | None = None,
) -> float:
    """Compute weighted composite LLM score L in [0, 1] from 6 0-100 dimensions and optional brief_match."""
    weights = dimension_weights or DEFAULT_DIMENSION_WEIGHTS
    total_w = sum(float(v) for v in weights.values()) or 1.0
    weighted_sum = 0.0
    for dim_key, w in weights.items():
        val = float(dimensions.get(dim_key, 0.0))
        clamped = min(100.0, max(0.0, val)) / 100.0
        weighted_sum += float(w) * clamped
    score = weighted_sum / total_w

    # Apply Clip Brief match modulation when an owner brief is active
    if clip_brief and clip_brief.strip():
        bm = 50.0 if brief_match is None else min(100.0, max(0.0, float(brief_match)))
        bm_norm = bm / 100.0
        if bm < 40.0:
            # Demote low brief-matching candidates strongly
            score *= 0.45 + 0.55 * (bm / 40.0)
        else:
            # Blend composite score with brief_match priority
            score = 0.75 * score + 0.25 * bm_norm

    return round(min(1.0, max(0.0, score)), 4)


def build_map_chunks(
    words: list[dict[str, Any]],
    *,
    chunk_minutes: float = 8.0,
    overlap_s: float = 60.0,
) -> list[list[dict[str, Any]]]:
    """Split flat word list into ~8-minute chunks with 60s overlap."""
    if not words:
        return []
    chunk_s = max(60.0, chunk_minutes * 60.0)
    step_s = max(30.0, chunk_s - overlap_s)
    t0 = float(words[0]["s"])
    t_end = float(words[-1]["e"])

    chunks: list[list[dict[str, Any]]] = []
    cur_start = t0
    while cur_start <= t_end:
        cur_end = cur_start + chunk_s
        chunk_words = [
            w for w in words if float(w["e"]) >= cur_start and float(w["s"]) <= cur_end
        ]
        if chunk_words:
            chunks.append(chunk_words)
        if cur_end >= t_end:
            break
        cur_start += step_s

    return chunks


def _format_numbered_words(chunk_words: list[dict[str, Any]]) -> str:
    return " ".join(f"[{w['idx']}]{w['w']}" for w in chunk_words)


def _match_moment_to_candidate(
    start_idx: int,
    end_idx: int,
    candidates: list[CandidateWindow],
) -> CandidateWindow | None:
    """Find the candidate window with highest word-index IoU against [start_idx, end_idx]."""
    best: CandidateWindow | None = None
    best_iou = -1.0
    for c in candidates:
        inter = max(0, min(end_idx, c.w1) - max(start_idx, c.w0) + 1)
        union = max(1, (end_idx - start_idx + 1) + (c.w1 - c.w0 + 1) - inter)
        iou = inter / union
        if iou > best_iou:
            best_iou = iou
            best = c
    return best


def score_candidates_map_reduce(
    transcript: dict[str, Any],
    candidates: list[CandidateWindow],
    *,
    provider: LLMProvider | None = None,
    clip_brief: str | None = None,
    genre: str | None = None,
    topics: list[dict[str, Any]] | None = None,
    video_summary: str | None = None,
    config: dict[str, Any] | None = None,
) -> MapReduceScoreResult:
    """Run Map-Reduce LLM semantic scoring with prompt-injection defense and schema validation."""
    cfg = config if config is not None else load_highlights_v2_config()
    llm_cfg = dict(cfg.get("llm", {}))
    dim_weights = dict(llm_cfg.get("dimension_weights", DEFAULT_DIMENSION_WEIGHTS))
    chunk_minutes = float(llm_cfg.get("map_chunk_minutes", 8.0))
    overlap_s = float(llm_cfg.get("map_overlap_s", 60.0))
    reduce_top_n = int(llm_cfg.get("reduce_top_n", 30))
    temperature = min(0.3, max(0.0, float(llm_cfg.get("temperature", 0.2))))
    max_tokens_budget = int(llm_cfg.get("max_tokens_per_job", 400000))

    language = str(transcript.get("language", "en"))
    script = str(transcript.get("script", "latn"))
    bounds = resolve_window_bounds(genre=genre, config=cfg)

    warnings: list[str] = []
    if provider is None:
        try:
            provider = get_llm_provider(strict=False)
        except LLMError as exc:
            warnings.append(f"LLM unavailable ({exc}); degrading to signals_only mode.")
            return MapReduceScoreResult(
                moments={},
                raw_moments=[],
                score_mode="signals_only",
                provider="none",
                warnings=warnings,
                token_usage=0,
            )

    all_words = flatten_transcript_words(transcript)
    if not all_words or not candidates:
        return MapReduceScoreResult(
            moments={},
            raw_moments=[],
            score_mode="full",
            provider=provider.provider_name,
            warnings=warnings,
            token_usage=0,
        )

    max_word_idx = len(all_words) - 1
    chunks = build_map_chunks(
        all_words,
        chunk_minutes=chunk_minutes,
        overlap_s=overlap_s,
    )

    map_template = load_prompt_template("highlight_map")
    reduce_template = load_prompt_template("highlight_reduce")
    brief_str = (clip_brief or "").strip()

    collected_moments: list[ScoredMoment] = []
    total_tokens = 0

    for chunk_idx, chunk_words in enumerate(chunks):
        if total_tokens >= max_tokens_budget:
            warnings.append("Reached max_tokens_per_job budget during map stage.")
            break

        c_start = float(chunk_words[0]["s"])
        c_end = float(chunk_words[-1]["e"])
        topic_label = f"Chunk {chunk_idx + 1}/{len(chunks)} ({c_start:.1f}s - {c_end:.1f}s)"
        if topics:
            matching_topics = [
                str(t.get("title", ""))
                for t in topics
                if isinstance(t, dict)
                and max(0.0, min(c_end, float(t.get("end", 0.0))) - max(c_start, float(t.get("start", 0.0)))) > 0
            ]
            if matching_topics:
                topic_label += f" | Topics: {', '.join(matching_topics)}"

        numbered_words = _format_numbered_words(chunk_words)
        # Prompt template has placeholders; keep untrusted transcript data wrapped in data_text
        prompt_text = (
            map_template.replace("{{language}}", language)
            .replace("{{script}}", script)
            .replace("{{min_len}}", str(int(bounds["min_len_s"])))
            .replace("{{max_len}}", str(int(bounds["max_len_s"])))
            .replace("{{clip_brief_or_empty}}", brief_str)
            .replace("{{topic_title_and_position}}", topic_label)
            .replace("{{numbered_words}}", "[SEE_WRAPPED_UNTRUSTED_TRANSCRIPT_DATA_BELOW]")
        )
        untrusted_payload = (
            f"<owner_brief>{brief_str}</owner_brief>\n"
            f"<chunk_context>{topic_label}</chunk_context>\n"
            f"<transcript>\n{numbered_words}\n</transcript>"
        )

        try:
            res = provider.generate_json(
                prompt=prompt_text,
                data_text=untrusted_payload,
                schema=MAP_RESPONSE_SCHEMA,
                temperature=temperature,
            )
        except LLMError as exc:
            warnings.append(f"LLM map failed ({exc}); falling back to signals_only mode.")
            return MapReduceScoreResult(
                moments={},
                raw_moments=[],
                score_mode="signals_only",
                provider=provider.provider_name,
                warnings=warnings,
                token_usage=total_tokens,
            )

        total_tokens += int(res.usage.get("total_tokens", 0))
        raw_list = res.data.get("moments", [])
        if not isinstance(raw_list, list):
            continue

        for item in raw_list:
            if not isinstance(item, dict):
                continue
            s_idx = int(item.get("start_word_idx", -1))
            e_idx = int(item.get("end_word_idx", -1))
            # Validate word indices against transcript bounds
            if s_idx < 0 or e_idx < s_idx or e_idx > max_word_idx:
                warnings.append(
                    f"Dropped out-of-bounds LLM moment indices [{s_idx}, {e_idx}] (max={max_word_idx})."
                )
                continue

            dims = {
                "hook": int(item.get("hook", 0)),
                "payoff": int(item.get("payoff", 0)),
                "standalone": int(item.get("standalone", 0)),
                "emotion": int(item.get("emotion", 0)),
                "shareability": int(item.get("shareability", 0)),
                "quotability": int(item.get("quotability", 0)),
            }
            bm_raw = item.get("brief_match")
            bm_val: int | None = int(bm_raw) if isinstance(bm_raw, (int, float)) else None

            l_score = compute_composite_llm_score(
                dims,
                dim_weights,
                clip_brief=brief_str,
                brief_match=bm_val,
            )
            matched_cand = _match_moment_to_candidate(s_idx, e_idx, candidates)
            cand_id = matched_cand.id if matched_cand is not None else f"llm_{s_idx}_{e_idx}"
            m_start = float(all_words[s_idx]["s"])
            m_end = float(all_words[e_idx]["e"])

            title = sanitize_title(str(item.get("title", "")))
            hook_line = sanitize_hook_line(
                str(item.get("hook_line", ""))
                or " ".join(str(w["w"]) for w in all_words[s_idx : min(e_idx + 1, s_idx + 10)])
            )
            reason = sanitize_hook_line(str(item.get("reason", "Strong standalone moment.")), max_len=200)
            hashtags = sanitize_hashtags(item.get("hashtags", []), language=language)

            collected_moments.append(
                ScoredMoment(
                    candidate_id=cand_id,
                    start_word_idx=s_idx,
                    end_word_idx=e_idx,
                    start=m_start,
                    end=m_end,
                    dimensions=dims,
                    brief_match=bm_val,
                    llm_score=l_score,
                    title=title,
                    hook_line=hook_line,
                    reason=reason,
                    hashtags=hashtags,
                    provider=provider.provider_name,
                )
            )

    # Sort map moments descending by composite L score and take top reduce_top_n
    collected_moments.sort(key=lambda m: m.llm_score, reverse=True)
    top_for_reduce = collected_moments[:reduce_top_n]

    # Run Reduce pass if we have moments and token budget
    if top_for_reduce and total_tokens < max_tokens_budget:
        summary_str = (video_summary or "").strip()
        if not summary_str and topics:
            summary_str = "; ".join(
                str(t.get("title", "")) for t in topics if isinstance(t, dict)
            )
        if not summary_str:
            summary_str = f"{language} video with {len(all_words)} words."

        candidates_payload = []
        for idx, m in enumerate(top_for_reduce):
            snippet_words = all_words[m.start_word_idx : min(m.end_word_idx + 1, m.start_word_idx + 40)]
            candidates_payload.append(
                {
                    "id": idx,
                    "candidate_id": m.candidate_id,
                    "start": round(m.start, 2),
                    "end": round(m.end, 2),
                    "initial_score": int(round(m.llm_score * 100)),
                    "brief_match": m.brief_match,
                    "title": m.title,
                    "text_preview": " ".join(str(w["w"]) for w in snippet_words),
                }
            )
        candidates_json_str = json.dumps(candidates_payload, ensure_ascii=False)
        reduce_prompt = (
            reduce_template.replace("{{n}}", str(len(top_for_reduce)))
            .replace("{{summary}}", summary_str)
            .replace("{{clip_brief_or_empty}}", brief_str)
            .replace("{{candidates_json}}", "[SEE_WRAPPED_UNTRUSTED_CANDIDATES_BELOW]")
        )
        reduce_untrusted = (
            f"<video_summary>{summary_str}</video_summary>\n"
            f"<owner_brief>{brief_str}</owner_brief>\n"
            f"<candidates>\n{candidates_json_str}\n</candidates>"
        )
        try:
            red_res = provider.generate_json(
                prompt=reduce_prompt,
                data_text=reduce_untrusted,
                schema=REDUCE_RESPONSE_SCHEMA,
                temperature=temperature,
            )
            total_tokens += int(red_res.usage.get("total_tokens", 0))
            ranked_items = red_res.data.get("ranked", [])
            if isinstance(ranked_items, list):
                for r_item in ranked_items:
                    if not isinstance(r_item, dict):
                        continue
                    rid = int(r_item.get("id", -1))
                    if 0 <= rid < len(top_for_reduce):
                        target_m = top_for_reduce[rid]
                        final_s = min(100.0, max(0.0, float(r_item.get("final_score", target_m.llm_score * 100)))) / 100.0
                        # Blend map composite score with reduce whole-video score
                        blended = 0.5 * target_m.llm_score + 0.5 * final_s
                        if brief_str and target_m.brief_match is not None and target_m.brief_match < 40:
                            blended = min(blended, target_m.llm_score)
                        target_m.llm_score = round(blended, 4)
                        target_m.keep = bool(r_item.get("keep", True))
                        dup_raw = r_item.get("duplicate_of")
                        target_m.duplicate_of = int(dup_raw) if isinstance(dup_raw, int) else None
                        if r_item.get("reason"):
                            target_m.reason = sanitize_hook_line(str(r_item["reason"]), max_len=200)
        except LLMError as exc:
            warnings.append(f"LLM reduce pass failed ({exc}); keeping map-stage scores.")

    # Map moments back to candidate IDs (keep highest-scoring moment per candidate)
    by_candidate: dict[str, ScoredMoment] = {}
    for m in top_for_reduce:
        if not m.keep or m.duplicate_of is not None:
            continue
        existing = by_candidate.get(m.candidate_id)
        if existing is None or m.llm_score > existing.llm_score:
            by_candidate[m.candidate_id] = m

    return MapReduceScoreResult(
        moments=by_candidate,
        raw_moments=top_for_reduce,
        score_mode="full",
        provider=provider.provider_name,
        warnings=warnings,
        token_usage=total_tokens,
    )
