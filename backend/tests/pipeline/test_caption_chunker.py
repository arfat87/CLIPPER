from __future__ import annotations

import json
import random
import unicodedata
from pathlib import Path
from typing import Any

from backend.pipeline.captions.chunker import (
    CaptionPage,
    ChunkerOpts,
    balance_lines,
    chunk_words_script_aware,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_V2_DIR = REPO_ROOT / "assets" / "templates_v2"


def _mk_words(tokens: list[str], *, start: float = 0.0, dur: float = 0.35, gap: float = 0.05) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    t = start
    for tok in tokens:
        s = round(t, 3)
        e = round(t + dur, 3)
        out.append({"w": tok, "s": s, "e": e})
        t = e + gap
    return out


def test_no_word_or_indic_conjunct_split_and_latin_only_casing() -> None:
    # Includes challenging Devanagari & Bengali conjuncts (क्ष, স্ত্র, सफलता)
    tokens = ["Dosto", "सफलता", "क्षमता", "অস্ত্রশস্ত্র", "শিক্ষা", "growth"]
    words = _mk_words(tokens, dur=0.4, gap=0.05)

    template = json.loads((TEMPLATES_V2_DIR / "bold_pop_yellow" / "template.json").read_text(encoding="utf-8"))
    pages = chunk_words_script_aware(words, template, mode="pages")

    assert all(isinstance(p, CaptionPage) for p in pages)
    flattened = [w.w for p in pages for w in p.words]
    assert len(flattened) == len(tokens)
    # Latin words uppercased per template text.case='upper'; Indic conjuncts preserved verbatim in NFC
    assert flattened[0] == "DOSTO"
    assert flattened[1] == unicodedata.normalize("NFC", "सफलता")
    assert flattened[2] == unicodedata.normalize("NFC", "क्षमता")
    assert flattened[3] == unicodedata.normalize("NFC", "অস্ত্রশস্ত্র")
    assert flattened[4] == unicodedata.normalize("NFC", "শিক্ষা")
    assert flattened[5] == "GROWTH"

    # Also test Bengali conjuncts directly with tight char budget: single long conjunct word stays intact
    tight_words = _mk_words(["অস্ত্রশস্ত্র", "শিক্ষা", "কৌশল"], dur=0.4)
    tight_pages = chunk_words_script_aware(
        tight_words,
        ChunkerOpts(max_words_per_line=2, max_lines=2, max_chars=8),
    )
    assert [w.w for p in tight_pages for w in p.words] == ["অস্ত্রশস্ত্র", "শিক্ষা", "কৌশল"]


def test_indic_08x_character_budget_wraps_sooner_than_latin() -> None:
    # With max_chars=20 -> Latin budget=20, Indic (deva/beng) budget=16 (0.8x).
    # Two 9-char Latin words = 9 + 1 + 9 = 19 <= 20 -> fits on 1 line.
    latn_words = _mk_words(["abcdefghi", "jklmnopqr"], dur=0.4)
    latn_lines = balance_lines(latn_words, max_lines=2, max_chars_budget=20, max_wpl=3)
    assert len(latn_lines) == 1

    # Two 9-char Bengali words = 9 + 1 + 9 = 19 > 16 -> wraps onto 2 lines!
    beng_words = _mk_words(["অআআইঈউঊঋএ", "কখগঘঙচছজঝ"], dur=0.4)
    beng_lines = balance_lines(beng_words, max_lines=2, max_chars_budget=20, max_wpl=3)
    assert len(beng_lines) == 2
    assert [w["w"] for line in beng_lines for w in line] == ["অআআইঈউঊঋএ", "কখগঘঙচছজঝ"]


def test_silence_gap_over_08s_forces_hard_break_and_hides_caption() -> None:
    words = [
        {"w": "First", "s": 10.0, "e": 10.35},
        {"w": "part", "s": 10.4, "e": 10.8},
        # 1.1s silence gap (> 0.8s)
        {"w": "Second", "s": 11.9, "e": 12.3},
        {"w": "part", "s": 12.35, "e": 12.8},
    ]
    pages = chunk_words_script_aware(
        words,
        ChunkerOpts(max_words_per_line=4, max_lines=2, max_chars=40),
        clip_start_offset=10.0,
    )
    assert len(pages) == 2
    assert [w.w for w in pages[0].words] == ["First", "part"]
    assert [w.w for w in pages[1].words] == ["Second", "part"]
    assert pages[0].start == 0.0
    assert pages[0].end == 0.8
    assert pages[1].start == 1.9
    assert (pages[1].start - pages[0].end) > 0.8


def test_sticky_number_unit_and_capitalized_proper_names_stay_together() -> None:
    # Number + unit ("100 ms") should not split across lines or pages when it fits
    words = _mk_words(["Latency", "is", "100", "ms", "today"], dur=0.35)
    opts = ChunkerOpts(max_words_per_line=3, max_lines=2, max_chars=24, case="upper")
    pages = chunk_words_script_aware(words, opts)
    # Check that "100" and "MS" are on the same line in the same page!
    found_same_line = any(
        ["100", "MS"] == line_texts[i : i + 2]
        for p in pages
        for line in p.lines
        for line_texts in [[w.w for w in line]]
        for i in range(max(0, len(line_texts) - 1))
    )
    assert found_same_line is True

    # Capitalized proper name ("Elon Musk") should stay on the same line
    name_words = _mk_words(["Yesterday", "Elon", "Musk", "launched"], dur=0.35)
    lines = balance_lines(name_words, max_lines=2, max_chars_budget=22, max_wpl=3)
    assert len(lines) == 2
    # Must wrap as ["Yesterday"] / ["Elon", "Musk", "launched"] or ["Yesterday", "Elon", "Musk"] / ["launched"]
    # Never splitting "Elon" and "Musk" across lines!
    assert any(
        any(line[i]["w"] == "Elon" and line[i + 1]["w"] == "Musk" for i in range(len(line) - 1))
        for line in lines
    )


def test_break_preferences_and_balanced_line_wrapping() -> None:
    # 4 similar-length words with max_wpl=3, max_lines=2 -> balanced wrapping prefers 2+2 over 3+1
    even_words = _mk_words(["fast", "ship", "wins", "big"], dur=0.35)
    balanced = balance_lines(even_words, max_lines=2, max_chars_budget=18, max_wpl=3)
    assert [len(line) for line in balanced] == [2, 2]

    # Conjunction preference: break cleanly before 'and' / 'aur' / 'এবং'
    conj_words = _mk_words(["Build", "fast", "and", "iterate"], dur=0.35)
    conj_lines = balance_lines(conj_words, max_lines=2, max_chars_budget=15, max_wpl=3)
    assert [w["w"] for w in conj_lines[0]] == ["Build", "fast"]
    assert [w["w"] for w in conj_lines[1]] == ["and", "iterate"]

    # Sentence-end punctuation forces page break even if capacity remains
    sent_words = _mk_words(["Stop", "now.", "Start", "today!"], dur=0.45)
    pages = chunk_words_script_aware(
        sent_words,
        ChunkerOpts(max_words_per_line=4, max_lines=2, max_chars=40),
    )
    assert len(pages) == 2
    assert [w.w for w in pages[0].words] == ["Stop", "now."]
    assert [w.w for w in pages[1].words] == ["Start", "today!"]


def test_modes_pages_word_by_word_and_karaoke() -> None:
    words = _mk_words(["Latency", "dropped", "100", "ms", "fast"], dur=0.4, gap=0.05)
    opts = ChunkerOpts(max_words_per_line=3, max_lines=2, max_chars=24)

    # word_by_word: 1-2 words per step, sticky "100 ms" kept together
    wbw_pages = chunk_words_script_aware(words, opts, mode="word_by_word")
    assert all(1 <= len(p.words) <= 2 for p in wbw_pages)
    assert any([w.w for w in p.words] == ["100", "ms"] for p in wbw_pages)
    assert [w.w for p in wbw_pages for w in p.words] == ["Latency", "dropped", "100", "ms", "fast"]

    # karaoke: phrase pages with per-word centisecond durations
    k_pages = chunk_words_script_aware(words, opts, mode="karaoke")
    assert len(k_pages) >= 1
    for p in k_pages:
        assert p.mode == "karaoke"
        assert len(p.karaoke_cs) == len(p.words)
        assert all(cs > 0 for cs in p.karaoke_cs)
        assert all(w.duration_cs == cs for w, cs in zip(p.words, p.karaoke_cs, strict=True))


def test_min_035s_and_max_25s_duration_bounds() -> None:
    # 1) Short orphan token (< 0.35s) merges safely with neighbor
    short_orphan_words = [
        {"w": "Hello", "s": 0.0, "e": 0.5},
        {"w": "world.", "s": 0.52, "e": 0.68},  # 0.16s token right after sentence boundary? No, same sentence!
    ]
    merged_pages = chunk_words_script_aware(
        short_orphan_words,
        ChunkerOpts(max_words_per_line=3, max_lines=2, min_duration=0.35, max_duration=2.5),
    )
    assert len(merged_pages) == 1
    assert merged_pages[0].duration >= 0.35

    # Even if forced to split by sentence punctuation, short second page extends cleanly if isolated
    forced_split = [
        {"w": "Go!", "s": 0.0, "e": 0.15},
        {"w": "Wait.", "s": 1.4, "e": 2.0},
    ]
    split_pages = chunk_words_script_aware(
        forced_split,
        ChunkerOpts(max_words_per_line=3, max_lines=2, min_duration=0.35, max_duration=2.5),
    )
    assert len(split_pages) == 2
    assert split_pages[0].duration >= 0.35
    assert split_pages[1].start - split_pages[0].end > 0.8

    # 2) Long continuous stream must split so no page exceeds max_duration=2.5s
    long_words = _mk_words([f"w{i}" for i in range(12)], dur=0.45, gap=0.05)  # 0.5s per word = 6.0s total
    bounded_pages = chunk_words_script_aware(
        long_words,
        ChunkerOpts(max_words_per_line=5, max_lines=2, max_chars=40, max_duration=2.5),
    )
    assert len(bounded_pages) >= 3
    for p in bounded_pages:
        assert p.duration <= 2.5 + 1e-6


def test_property_multilingual_token_preservation_zero_overlaps_and_limits() -> None:
    rng = random.Random(6464)
    token_pool = [
        "ship",
        "fast",
        "and",
        "100",
        "ms",
        "Elon",
        "Musk",
        "viral,",
        "सफलता",
        "दोस्तों",
        "और",
        "ज़िंदगी!",
        "ভাইরাল",
        "অস্ত্রশস্ত্র",
        "এবং",
        "কৌশল।",
    ]

    for trial in range(35):
        n_words = rng.randint(3, 28)
        words: list[dict[str, Any]] = []
        t = 10.0
        for _ in range(n_words):
            dur = round(rng.uniform(0.18, 0.7), 3)
            gap = round(rng.choice([0.03, 0.05, 0.1, 0.95]), 3)
            words.append({"w": rng.choice(token_pool), "s": round(t, 3), "e": round(t + dur, 3)})
            t += dur + gap

        max_wpl = rng.choice([2, 3, 4])
        max_lines = rng.choice([1, 2])
        mode = ("pages", "word_by_word", "karaoke")[trial % 3]
        opts = ChunkerOpts(
            max_words_per_line=max_wpl,
            max_lines=max_lines,
            max_chars=22,
            case=("none", "upper", "lower")[trial % 3],
        )

        pages = chunk_words_script_aware(words, opts, clip_start_offset=10.0, mode=mode)
        out_words = [w for p in pages for w in p.words]

        # 100% token preservation (no splits, no drops)
        assert len(out_words) == len(words)
        for idx, p in enumerate(pages):
            assert p.start >= 0.0
            assert p.end >= p.start
            assert len(p.lines) <= max_lines
            for line in p.lines:
                assert 1 <= len(line) <= max_wpl
            if idx + 1 < len(pages):
                assert p.end <= pages[idx + 1].start + 1e-9
            for w in p.words:
                assert unicodedata.is_normalized("NFC", w.w)
                if w.script != "latn":
                    # Indic tokens must never have ASCII case mutation
                    assert w.w == w.orig_w
