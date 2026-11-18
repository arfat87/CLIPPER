from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, overload

from backend.pipeline.asr.engine import detect_word_script

SENTENCE_END_PUNCT = (".", "!", "?", "।", "॥")
CLAUSE_PAUSE_PUNCT = (",", ";", ":", "—", "–")

CONJUNCTION_WORDS: frozenset[str] = frozenset(
    {
        # English
        "and",
        "but",
        "or",
        "so",
        "because",
        "yet",
        "nor",
        "while",
        "although",
        "though",
        # Hinglish / Roman Hindi
        "aur",
        "lekin",
        "magar",
        "par",
        "kyunki",
        "kyuki",
        "ya",
        "toh",
        "ki",
        # Hindi Devanagari
        "और",
        "लेकिन",
        "मगर",
        "पर",
        "परंतु",
        "क्योंकि",
        "या",
        "तो",
        "कि",
        # Bengali
        "এবং",
        "ও",
        "কিন্তু",
        "তবে",
        "বা",
        "অথবা",
        "কারণ",
        "যে",
    }
)

UNIT_WORDS: frozenset[str] = frozenset(
    {
        "ms",
        "s",
        "sec",
        "secs",
        "second",
        "seconds",
        "min",
        "mins",
        "minute",
        "minutes",
        "hr",
        "hrs",
        "hour",
        "hours",
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
        "km",
        "m",
        "cm",
        "mm",
        "kg",
        "g",
        "mg",
        "lb",
        "lbs",
        "kb",
        "mb",
        "gb",
        "tb",
        "px",
        "fps",
        "hz",
        "khz",
        "mhz",
        "ghz",
        "%",
        "percent",
        "pct",
        "rs",
        "inr",
        "usd",
        "eur",
        "k",
        "b",
        "lakh",
        "lakhs",
        "crore",
        "crores",
        "million",
        "billion",
        "x",
    }
)

_NUMBER_RE = re.compile(r"^[\$₹€£]?\d+(?:[.,]\d+)?%?$|^[\u0966-\u096f\u09e6-\u09ef]+$")
_PROPER_CAP_RE = re.compile(r"^[A-Z][a-zA-Z0-9'’-]*$")
_COMMON_SENTENCE_STARTERS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "here", "there",
        "when", "where", "while", "why", "how", "what", "who", "which",
        "if", "as", "in", "on", "at", "by", "for", "from", "with", "to",
        "we", "you", "they", "he", "she", "it", "i", "my", "our", "your",
        "and", "but", "or", "so", "yet", "then", "now", "today", "yesterday",
        "tomorrow", "also", "first", "second", "next", "finally", "every",
        "many", "some", "all", "no", "not", "just", "only", "very", "here's",
    }
)


@dataclass(slots=True)
class CaptionWord:
    """Single word token in a caption page with relative clip timestamps."""

    w: str
    s: float
    e: float
    script: str = "latn"
    p: float | None = None
    flags: list[str] = field(default_factory=list)
    duration_cs: int | None = None
    orig_w: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "w": self.w,
            "s": round(self.s, 3),
            "e": round(self.e, 3),
            "script": self.script,
        }
        if self.p is not None:
            payload["p"] = self.p
        if self.flags:
            payload["flags"] = list(self.flags)
        if self.duration_cs is not None:
            payload["duration_cs"] = self.duration_cs
        return payload


@dataclass(slots=True)
class CaptionPage:
    """Wrapped multi-line (or step-wise) caption page ready for ASS/SRT rendering."""

    start: float
    end: float
    lines: list[list[CaptionWord]]
    words: list[CaptionWord]
    mode: str = "pages"
    karaoke_cs: list[int] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(max(0.0, self.end - self.start), 3)

    @property
    def text(self) -> str:
        return "\n".join(" ".join(w.w for w in line) for line in self.lines)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "mode": self.mode,
            "lines": [[w.to_dict() for w in line] for line in self.lines],
            "words": [w.to_dict() for w in self.words],
        }
        if self.karaoke_cs:
            payload["karaoke_cs"] = list(self.karaoke_cs)
        return payload


@dataclass(slots=True)
class ChunkerOpts:
    """Script-aware chunking limits and timing parameters."""

    max_words_per_line: int = 3
    max_lines: int = 2
    max_chars: int | dict[str, int] = 22
    indic_char_factor: float = 0.8
    case: str = "none"
    min_duration: float = 0.35
    max_duration: float = 2.5
    silence_gap_threshold: float = 0.8
    mode: str = "pages"

    @classmethod
    def from_template_or_opts(
        cls,
        template_or_opts: ChunkerOpts | dict[str, Any] | None,
        *,
        mode_override: str | None = None,
    ) -> ChunkerOpts:
        if isinstance(template_or_opts, ChunkerOpts):
            opts = ChunkerOpts(
                max_words_per_line=template_or_opts.max_words_per_line,
                max_lines=template_or_opts.max_lines,
                max_chars=template_or_opts.max_chars,
                indic_char_factor=template_or_opts.indic_char_factor,
                case=template_or_opts.case,
                min_duration=template_or_opts.min_duration,
                max_duration=template_or_opts.max_duration,
                silence_gap_threshold=template_or_opts.silence_gap_threshold,
                mode=mode_override or template_or_opts.mode,
            )
            return opts

        tmpl = dict(template_or_opts or {})
        layout = tmpl.get("layout") or {}
        text_cfg = tmpl.get("text") or {}
        anim_cfg = tmpl.get("animation") or {}

        max_wpl = max(1, int(layout.get("max_words_per_line", 3)))
        max_lines = max(1, int(layout.get("max_lines", 2)))
        max_chars = layout.get("max_chars", tmpl.get("max_chars", 22))
        case_mode = str(text_cfg.get("case", tmpl.get("case", "none")) or "none").lower()

        raw_mode = mode_override
        if not raw_mode:
            anim_mode = str(anim_cfg.get("mode", "")).lower()
            anim_type = str(anim_cfg.get("type", "")).lower()
            if anim_mode in ("pages", "word_by_word", "karaoke"):
                raw_mode = anim_mode
            elif anim_type == "karaoke_fill":
                raw_mode = "karaoke"
            else:
                raw_mode = "pages"

        return cls(
            max_words_per_line=max_wpl,
            max_lines=max_lines,
            max_chars=max_chars,
            case=case_mode,
            min_duration=float(tmpl.get("min_duration", 0.35)),
            max_duration=float(tmpl.get("max_duration", 2.5)),
            silence_gap_threshold=float(tmpl.get("silence_gap_threshold", 0.8)),
            mode=raw_mode,
        )


def _get_word_text(w: dict[str, Any] | CaptionWord) -> str:
    if isinstance(w, CaptionWord):
        return w.w
    return str(w.get("w", ""))


def _get_orig_text(w: dict[str, Any] | CaptionWord) -> str:
    if isinstance(w, CaptionWord):
        return w.orig_w or w.w
    return str(w.get("orig_w") or w.get("w", ""))


def _get_word_script(w: dict[str, Any] | CaptionWord) -> str:
    if isinstance(w, CaptionWord):
        return w.script or detect_word_script(w.w)
    sc = w.get("script")
    return str(sc) if sc else detect_word_script(str(w.get("w", "")))


def _resolve_char_budgets(
    max_chars_budget: int | dict[str, int],
    indic_factor: float = 0.8,
) -> tuple[float, dict[str, float]]:
    if isinstance(max_chars_budget, dict):
        latn_b = float(max(1, int(max_chars_budget.get("latn", 22))))
        deva_b = float(max(1, int(max_chars_budget.get("deva", round(latn_b * indic_factor)))))
        beng_b = float(max(1, int(max_chars_budget.get("beng", round(latn_b * indic_factor)))))
    else:
        latn_b = float(max(1, int(max_chars_budget)))
        deva_b = float(max(1, int(round(latn_b * indic_factor))))
        beng_b = float(max(1, int(round(latn_b * indic_factor))))
    return latn_b, {"latn": latn_b, "deva": deva_b, "beng": beng_b}


def _weighted_line_chars(
    line: list[Any],
    max_chars_budget: int | dict[str, int],
    indic_factor: float = 0.8,
) -> float:
    """Compute effective character load normalized to the Latin budget."""
    if not line:
        return 0.0
    latn_b, budgets = _resolve_char_budgets(max_chars_budget, indic_factor)
    total = 0.0
    for idx, w in enumerate(line):
        if idx > 0:
            total += 1.0  # space between words
        txt = _get_word_text(w)
        sc = _get_word_script(w)
        scale = latn_b / budgets.get(sc, latn_b)
        total += len(txt) * scale
    return total


def _strip_punct(token: str) -> str:
    return token.strip(".,!?;:\"'()[]{}।॥—–-")


def _are_sticky_pair(prev_w: dict[str, Any] | CaptionWord, next_w: dict[str, Any] | CaptionWord) -> bool:
    """Return True if two adjacent tokens form a number+unit or capitalized proper-name pair."""
    prev_orig = _get_orig_text(prev_w).strip()
    next_orig = _get_orig_text(next_w).strip()
    if not prev_orig or not next_orig:
        return False

    # Never stick across sentence termination
    if prev_orig.endswith(SENTENCE_END_PUNCT):
        return False

    prev_clean = _strip_punct(prev_orig)
    next_clean = _strip_punct(next_orig)
    if not prev_clean or not next_clean:
        return False

    # 1) Number + unit (e.g., "100 ms", "50 %", "10 min")
    if _NUMBER_RE.match(prev_clean) and (
        next_clean.lower() in UNIT_WORDS or next_orig.startswith("%")
    ):
        return True

    # 2) Consecutive capitalized proper names in Latin script (e.g., "Elon Musk", "New York")
    if (
        detect_word_script(prev_clean) == "latn"
        and detect_word_script(next_clean) == "latn"
        and _PROPER_CAP_RE.match(prev_clean)
        and _PROPER_CAP_RE.match(next_clean)
        and prev_clean.lower() not in _COMMON_SENTENCE_STARTERS
        and next_clean.lower() not in _COMMON_SENTENCE_STARTERS
        and not prev_orig.endswith(CLAUSE_PAUSE_PUNCT)
    ):
        return True

    return False


def _boundary_break_cost(
    left_w: dict[str, Any] | CaptionWord,
    right_w: dict[str, Any] | CaptionWord,
) -> float:
    """Negative cost = preferred break; positive cost = discouraged break."""
    left_txt = _get_word_text(left_w).strip()
    right_txt = _get_word_text(right_w).strip()
    right_clean = _strip_punct(right_txt).lower()

    cost = 0.0
    if left_txt.endswith(SENTENCE_END_PUNCT):
        cost -= 30.0
    elif left_txt.endswith(CLAUSE_PAUSE_PUNCT):
        cost -= 15.0

    if right_clean in CONJUNCTION_WORDS:
        cost -= 8.0

    if _are_sticky_pair(left_w, right_w):
        cost += 500.0

    return cost


@overload
def balance_lines(
    words: list[CaptionWord],
    max_lines: int,
    max_chars_budget: int | dict[str, int],
    max_wpl: int,
) -> list[list[CaptionWord]]: ...


@overload
def balance_lines(
    words: list[dict[str, Any]],
    max_lines: int,
    max_chars_budget: int | dict[str, int],
    max_wpl: int,
) -> list[list[dict[str, Any]]]: ...


def balance_lines(
    words: list[Any],
    max_lines: int,
    max_chars_budget: int | dict[str, int],
    max_wpl: int,
) -> list[list[Any]]:
    """Wrap words into <= max_lines balanced lines respecting script char budget & break preferences."""
    if not words:
        return []

    max_lines = max(1, int(max_lines))
    max_wpl = max(1, int(max_wpl))
    latn_budget, _ = _resolve_char_budgets(max_chars_budget)
    n = len(words)

    def _line_valid(i: int, j: int) -> bool:
        cnt = j - i
        if cnt < 1 or cnt > max_wpl:
            return False
        if cnt == 1:
            return True  # Never split a single word even if it exceeds char budget
        return _weighted_line_chars(words[i:j], max_chars_budget) <= latn_budget + 1e-6

    # Try exact DP for k = 1..max_lines to prefer minimum raggedness & clean break points
    best_lines: list[list[Any]] | None = None
    best_total_cost = math.inf

    total_weighted = _weighted_line_chars(words, max_chars_budget)

    for k in range(1, max_lines + 1):
        target_len = min(latn_budget, total_weighted / k)
        # dp[lines_used][pos] = (cost, prev_pos)
        dp: list[dict[int, tuple[float, int]]] = [{} for _ in range(k + 1)]
        dp[0][0] = (0.0, -1)

        for l_idx in range(1, k + 1):
            for i, (base_cost, _) in dp[l_idx - 1].items():
                for j in range(i + 1, min(n, i + max_wpl) + 1):
                    if not _line_valid(i, j):
                        break
                    w_len = _weighted_line_chars(words[i:j], max_chars_budget)
                    raggedness = (target_len - w_len) ** 2
                    break_cost = _boundary_break_cost(words[j - 1], words[j]) if j < n else 0.0
                    # Slight preference for fewer lines only when fit is otherwise similar
                    cand_cost = base_cost + raggedness + break_cost
                    prev = dp[l_idx].get(j)
                    if prev is None or cand_cost < prev[0]:
                        dp[l_idx][j] = (cand_cost, i)

        if n in dp[k]:
            # Small line-count regularizer (prefer 1 line if a 1-line wrap fits comfortably)
            total_cost = dp[k][n][0] + (k - 1) * 2.0
            if total_cost < best_total_cost:
                best_total_cost = total_cost
                # Reconstruct lines
                slices: list[list[Any]] = []
                cur = n
                for l_idx in range(k, 0, -1):
                    prev_idx = dp[l_idx][cur][1]
                    slices.append(list(words[prev_idx:cur]))
                    cur = prev_idx
                slices.reverse()
                best_lines = slices

    if best_lines is not None:
        return best_lines

    # Graceful greedy fallback if words exceed max_lines capacity
    lines: list[list[Any]] = []
    idx = 0
    while idx < n:
        end = idx + 1
        while end < n and (end - idx) < max_wpl:
            if _weighted_line_chars(words[idx : end + 1], max_chars_budget) <= latn_budget + 1e-6:
                end += 1
            else:
                break
        lines.append(list(words[idx:end]))
        idx = end
    return lines


def _can_wrap_within_limits(words: list[CaptionWord], opts: ChunkerOpts) -> bool:
    if len(words) > opts.max_words_per_line * opts.max_lines:
        return False
    lines = balance_lines(words, opts.max_lines, opts.max_chars, opts.max_words_per_line)
    if len(lines) > opts.max_lines:
        return False
    latn_budget, _ = _resolve_char_budgets(opts.max_chars, opts.indic_char_factor)
    for line in lines:
        if len(line) > opts.max_words_per_line:
            return False
        if len(line) > 1 and _weighted_line_chars(line, opts.max_chars, opts.indic_char_factor) > latn_budget + 1e-6:
            return False
    return True


def _prepare_words(
    words: list[dict[str, Any] | CaptionWord],
    opts: ChunkerOpts,
    clip_start_offset: float,
) -> list[CaptionWord]:
    prepared: list[CaptionWord] = []
    for raw in words:
        if isinstance(raw, CaptionWord):
            raw_txt = unicodedata.normalize("NFC", raw.w.strip())
            orig_txt = unicodedata.normalize("NFC", (raw.orig_w or raw.w).strip())
            s_val = float(raw.s) - clip_start_offset
            e_val = float(raw.e) - clip_start_offset
            sc = raw.script or detect_word_script(raw_txt)
            p_val = raw.p
            flags_val = list(raw.flags)
        else:
            raw_txt = unicodedata.normalize("NFC", str(raw.get("w", "")).strip())
            orig_txt = unicodedata.normalize("NFC", str(raw.get("orig_w") or raw.get("w", "")).strip())
            s_val = float(raw.get("s", 0.0)) - clip_start_offset
            e_val = float(raw.get("e", s_val)) - clip_start_offset
            sc = str(raw.get("script") or detect_word_script(raw_txt))
            raw_p = raw.get("p")
            p_val = float(raw_p) if isinstance(raw_p, (int, float)) else None
            flags_val = list(raw.get("flags") or [])

        if not raw_txt:
            continue

        s_clamped = max(0.0, round(s_val, 3))
        e_clamped = max(s_clamped + 0.01, round(e_val, 3))

        # Apply case transform ONLY to Latin script; Indic scripts stay untouched
        display_txt = raw_txt
        if sc == "latn":
            if opts.case == "upper":
                display_txt = raw_txt.upper()
            elif opts.case == "lower":
                display_txt = raw_txt.lower()

        prepared.append(
            CaptionWord(
                w=display_txt,
                s=s_clamped,
                e=e_clamped,
                script=sc,
                p=p_val,
                flags=flags_val,
                orig_w=orig_txt,
            )
        )

    # Ensure monotonic non-overlapping word timestamps
    for idx in range(1, len(prepared)):
        if prepared[idx].s < prepared[idx - 1].e:
            if prepared[idx].e > prepared[idx - 1].e + 0.01:
                prepared[idx].s = prepared[idx - 1].e
            else:
                prepared[idx].s = prepared[idx - 1].e
                prepared[idx].e = round(prepared[idx].s + 0.05, 3)

    return prepared


def _split_speech_runs(words: list[CaptionWord], gap_threshold: float) -> list[list[CaptionWord]]:
    if not words:
        return []
    runs: list[list[CaptionWord]] = [[words[0]]]
    for w in words[1:]:
        if (w.s - runs[-1][-1].e) > gap_threshold:
            runs.append([w])
        else:
            runs[-1].append(w)
    return runs


def _chunk_speech_run_pages(run: list[CaptionWord], opts: ChunkerOpts) -> list[list[CaptionWord]]:
    """Split a contiguous speech run (no silence gaps > threshold) into optimal phrase pages."""
    pages: list[list[CaptionWord]] = []
    n = len(run)
    idx = 0
    max_cap = max(1, opts.max_words_per_line * opts.max_lines)

    while idx < n:
        # Find maximum feasible end index `max_end` (exclusive)
        max_end = idx + 1
        while max_end < n and (max_end - idx) < max_cap:
            cand = run[idx : max_end + 1]
            if (cand[-1].e - cand[0].s) > opts.max_duration:
                break
            if not _can_wrap_within_limits(cand, opts):
                break
            max_end += 1

        # If a sentence ends before max_end, check whether to cut right there
        sentence_cut: int | None = None
        for k in range(idx + 1, max_end + 1):
            if run[k - 1].w.endswith(SENTENCE_END_PUNCT):
                sentence_cut = k
                break

        if sentence_cut is not None:
            chosen_end = sentence_cut
        elif max_end == n:
            chosen_end = max_end
        else:
            # Score candidate cut points k in [idx+1 .. max_end]
            best_k = max_end
            best_score = -math.inf
            for k in range(idx + 1, max_end + 1):
                chunk = run[idx:k]
                dur = chunk[-1].e - chunk[0].s
                score = float(len(chunk)) * 3.0  # prefer fuller pages up to capacity

                if dur < opts.min_duration and k < max_end:
                    score -= 25.0

                if k < n:
                    left_w = run[k - 1]
                    right_w = run[k]
                    if left_w.w.endswith(CLAUSE_PAUSE_PUNCT):
                        score += 18.0
                    if _strip_punct(right_w.w).lower() in CONJUNCTION_WORDS:
                        score += 12.0
                    if _are_sticky_pair(left_w, right_w):
                        score -= 40.0

                # Avoid leaving a 1-word short orphan at the very end if we can avoid it
                rem = n - k
                if rem == 1 and (run[-1].e - run[-1].s) < opts.min_duration:
                    score -= 15.0

                if score >= best_score:
                    best_score = score
                    best_k = k
            chosen_end = best_k

        pages.append(run[idx:chosen_end])
        idx = chosen_end

    # Post-pass: merge short orphan pages (< min_duration) with neighbor when safe
    merged: list[list[CaptionWord]] = []
    for chunk in pages:
        if not merged:
            merged.append(chunk)
            continue
        prev = merged[-1]
        prev_dur = prev[-1].e - prev[0].s
        cur_dur = chunk[-1].e - chunk[0].s
        if prev_dur < opts.min_duration or cur_dur < opts.min_duration:
            combined = prev + chunk
            if (combined[-1].e - combined[0].s) <= opts.max_duration and _can_wrap_within_limits(
                combined, opts
            ):
                merged[-1] = combined
                continue
        merged.append(chunk)

    return merged


def _chunk_speech_run_word_by_word(
    run: list[CaptionWord],
    opts: ChunkerOpts,
) -> list[list[CaptionWord]]:
    """Split a contiguous speech run into 1-2 word steps, keeping sticky pairs or ultra-short words together."""
    steps: list[list[CaptionWord]] = []
    n = len(run)
    idx = 0
    while idx < n:
        if idx + 1 < n:
            pair = run[idx : idx + 2]
            pair_dur = pair[-1].e - pair[0].s
            cur_dur = run[idx].e - run[idx].s
            if (
                (_are_sticky_pair(pair[0], pair[1]) or cur_dur < opts.min_duration)
                and pair_dur <= opts.max_duration
                and _can_wrap_within_limits(pair, opts)
            ):
                steps.append(pair)
                idx += 2
                continue
        steps.append([run[idx]])
        idx += 1
    return steps


def _build_caption_page(
    words: list[CaptionWord],
    opts: ChunkerOpts,
    *,
    max_end_cap: float | None = None,
) -> CaptionPage:
    lines = balance_lines(words, opts.max_lines, opts.max_chars, opts.max_words_per_line)
    start = words[0].s
    end = words[-1].e

    # Enforce min_duration if safe without crossing into next page or violating silence gap
    if (end - start) < opts.min_duration:
        target_end = start + opts.min_duration
        if max_end_cap is not None:
            target_end = min(target_end, max_end_cap)
        if target_end > end:
            end = round(target_end, 3)

    karaoke_cs: list[int] = []
    if opts.mode == "karaoke":
        for w in words:
            cs = max(1, int(round((w.e - w.s) * 100)))
            w.duration_cs = cs
            karaoke_cs.append(cs)

    return CaptionPage(
        start=round(start, 3),
        end=round(end, 3),
        lines=lines,
        words=list(words),
        mode=opts.mode,
        karaoke_cs=karaoke_cs,
    )


def chunk_words_script_aware(
    words: list[dict[str, Any] | CaptionWord],
    template_or_opts: ChunkerOpts | dict[str, Any] | None = None,
    *,
    clip_start_offset: float = 0.0,
    mode: str | None = None,
) -> list[CaptionPage]:
    """Chunk word-level timestamps into script-aware, balanced CaptionPages."""
    opts = ChunkerOpts.from_template_or_opts(template_or_opts, mode_override=mode)
    prepared = _prepare_words(words, opts, clip_start_offset=clip_start_offset)
    if not prepared:
        return []

    runs = _split_speech_runs(prepared, opts.silence_gap_threshold)
    raw_chunks: list[list[CaptionWord]] = []

    for run in runs:
        if opts.mode == "word_by_word":
            raw_chunks.extend(_chunk_speech_run_word_by_word(run, opts))
        else:
            raw_chunks.extend(_chunk_speech_run_pages(run, opts))

    pages: list[CaptionPage] = []
    for idx, chunk in enumerate(raw_chunks):
        next_start = raw_chunks[idx + 1][0].s if idx + 1 < len(raw_chunks) else None
        max_end_cap: float | None = None
        if next_start is not None:
            gap_to_next = next_start - chunk[-1].e
            if gap_to_next > opts.silence_gap_threshold:
                # Keep silence gap strictly > threshold so captions stay hidden during silence
                max_end_cap = next_start - (opts.silence_gap_threshold + 0.01)
            else:
                max_end_cap = next_start
        pages.append(_build_caption_page(chunk, opts, max_end_cap=max_end_cap))

    # Final invariant: zero overlap between adjacent pages
    for idx in range(1, len(pages)):
        if pages[idx - 1].end > pages[idx].start:
            pages[idx - 1].end = round(pages[idx].start, 3)

    return pages
