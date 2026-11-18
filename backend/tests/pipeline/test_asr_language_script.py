from __future__ import annotations

import json
import random
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from backend.pipeline.asr.engine import detect_word_script
from backend.pipeline.asr.language import (
    LanguageDetectionResult,
    detect_language_multiwindow,
    write_language_evidence,
)
from backend.pipeline.asr.script import (
    mask_profanity_for_captions,
    normalize_script_tokens,
    restore_punctuation_and_casing,
    transliterate_devanagari_to_roman,
    transliterate_devanagari_token,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "assets" / "samples"


@pytest.mark.parametrize(
    "sample_id,expected_lang,expected_script",
    [
        ("sample_english_keynote", "en", "latn"),
        ("sample_hindi_motivation", "hi", "deva"),
        ("sample_hinglish_podcast", "hinglish", "latn"),
        ("sample_bengali_tech", "bn", "beng"),
    ],
)
def test_detect_language_multiwindow_on_all_four_samples(
    sample_id: str,
    expected_lang: str,
    expected_script: str,
    tmp_path: Path,
) -> None:
    sample_path = SAMPLES_DIR / f"{sample_id}.transcript.json"
    data = json.loads(sample_path.read_text(encoding="utf-8"))

    result = detect_language_multiwindow(
        data["segments"],
        declared_lang="auto",
        audio_duration=float(data["duration"]),
    )
    assert isinstance(result, LanguageDetectionResult)
    assert result.language == expected_lang
    assert result.script == expected_script
    assert result.detected_language == expected_lang
    assert result.override_applied is False
    assert len(result.window_votes) == 3
    assert [w["window"] for w in result.window_votes] == ["start", "middle", "end"]
    assert 0.0 < result.confidence <= 1.0

    lang_json_path = write_language_evidence(result, tmp_path)
    assert lang_json_path == tmp_path / "asr" / "lang.json"
    written = json.loads(lang_json_path.read_text(encoding="utf-8"))
    assert written["language"] == expected_lang
    assert written["script"] == expected_script
    assert len(written["window_votes"]) == 3


def test_hinglish_heuristic_deva_plus_latin_and_roman_pass_with_hi_hint() -> None:
    # 1) Mixed Devanagari + >= 25% Latin tokens across 3 windows -> hinglish
    mixed_windows = [
        {
            "window": "start",
            "segments": [
                {
                    "start": 0.0,
                    "end": 10.0,
                    "text": "दोस्तों आज startup growth और retention पर बात करेंगे",
                    "words": [
                        {"w": "दोस्तों", "s": 0.0, "e": 0.5},
                        {"w": "आज", "s": 0.5, "e": 0.9},
                        {"w": "startup", "s": 0.9, "e": 1.4},
                        {"w": "growth", "s": 1.4, "e": 1.9},
                        {"w": "और", "s": 1.9, "e": 2.2},
                        {"w": "retention", "s": 2.2, "e": 2.8},
                        {"w": "पर", "s": 2.8, "e": 3.1},
                        {"w": "बात", "s": 3.1, "e": 3.5},
                        {"w": "करेंगे", "s": 3.5, "e": 4.0},
                    ],
                }
            ],
        },
        {
            "window": "middle",
            "segments": [
                {
                    "start": 30.0,
                    "end": 40.0,
                    "text": "हर creator को video hook और captions पर focus करना चाहिए",
                    "words": [
                        {"w": w, "s": 30.0 + i, "e": 30.5 + i}
                        for i, w in enumerate(
                            ["हर", "creator", "को", "video", "hook", "और", "captions", "पर", "focus", "करना", "चाहिए"]
                        )
                    ],
                }
            ],
        },
        {
            "window": "end",
            "segments": [
                {
                    "start": 60.0,
                    "end": 70.0,
                    "text": "आपका content तभी viral होगा",
                    "words": [
                        {"w": w, "s": 60.0 + i, "e": 60.5 + i}
                        for i, w in enumerate(["आपका", "content", "तभी", "viral", "होगा"])
                    ],
                }
            ],
        },
    ]
    res_mixed = detect_language_multiwindow(mixed_windows)
    assert res_mixed.language == "hinglish"
    assert res_mixed.script == "latn"
    assert res_mixed.latin_token_ratio >= 0.25
    assert res_mixed.devanagari_ratio > 0.0

    # 2) ASR detected 'hi' in Roman-script pass with >= 25% Latin tokens -> hinglish
    roman_pass_windows = [
        {
            "window": "start",
            "language": "hi",
            "segments": [{"start": 0.0, "end": 10.0, "text": "Dosto welcome to our tech podcast"}],
        },
        {
            "window": "middle",
            "language": "hi",
            "segments": [{"start": 30.0, "end": 40.0, "text": "Aaj hum pipeline design discuss karenge"}],
        },
        {
            "window": "end",
            "language": "hi",
            "segments": [{"start": 60.0, "end": 70.0, "text": "Subscribe for more updates"}],
        },
    ]
    res_roman = detect_language_multiwindow(roman_pass_windows)
    assert res_roman.language == "hinglish"
    assert res_roman.script == "latn"
    assert res_roman.latin_token_ratio >= 0.25


def test_majority_vote_and_owner_override_priority() -> None:
    # 2 windows en, 1 window hi -> majority vote picks en
    windows = [
        [{"start": 0.0, "end": 10.0, "text": "This is purely English engineering talk"}],
        [{"start": 30.0, "end": 40.0, "text": "We measure latency and throughput every day"}],
        [{"start": 60.0, "end": 70.0, "text": "सफलता मेहनत से मिलती है"}],
    ]
    auto_res = detect_language_multiwindow(windows)
    assert auto_res.language == "en"
    assert auto_res.override_applied is False

    # Owner override ALWAYS wins while still recording evidence
    override_res = detect_language_multiwindow(windows, declared_lang="hinglish")
    assert override_res.language == "hinglish"
    assert override_res.script == "latn"
    assert override_res.override_applied is True
    assert override_res.detected_language == "en"
    assert override_res.confidence == 1.0
    assert len(override_res.window_votes) == 3


def test_rule_based_devanagari_transliterator_covers_nukta_matras_halant_digits() -> None:
    # Individual challenging Devanagari tokens
    assert transliterate_devanagari_token("दोस्त") == "dost"
    assert transliterate_devanagari_token("ज़िंदगी") == "zindagee"
    # Decomposed nukta (ज + \u093c) must behave identically to precomposed ज़
    decomposed_zindagi = "ज\u093cिंदगी"
    assert transliterate_devanagari_token(decomposed_zindagi) == "zindagee"
    assert transliterate_devanagari_token("फ़िल्म") == "film"
    assert transliterate_devanagari_token("संभव") == "sambhav"
    assert transliterate_devanagari_token("में") == "men"
    assert transliterate_devanagari_token("हैं") == "hain"
    assert transliterate_devanagari_token("२०२६") == "2026"
    assert transliterate_devanagari_token("दोस्तों,") == "doston,"
    assert transliterate_devanagari_token("है।") == "hai."

    # English / Latin tokens remain strictly untouched
    assert transliterate_devanagari_token("YouTube") == "YouTube"
    assert transliterate_devanagari_token("API_v2!") == "API_v2!"

    mixed = transliterate_devanagari_to_roman("YouTube पर सफलता के 3 rules हैं।")
    assert "YouTube" in mixed
    assert "3 rules" in mixed
    assert detect_word_script(mixed) == "latn"


def test_bengali_nfc_normalization_preserves_nukta_hasanta_and_matras() -> None:
    sample_bn = json.loads((SAMPLES_DIR / "sample_bengali_tech.transcript.json").read_text(encoding="utf-8"))
    # Convert all words and texts to NFD to simulate decomposed ASR/Unicode input
    nfd_segments: list[dict[str, Any]] = []
    for seg in sample_bn["segments"]:
        nfd_words = [
            {**w, "w": unicodedata.normalize("NFD", w["w"])}
            for w in seg["words"]
        ]
        nfd_segments.append(
            {
                **seg,
                "text": unicodedata.normalize("NFD", seg["text"]),
                "words": nfd_words,
            }
        )

    norm_segs, target_script = normalize_script_tokens(
        nfd_segments,
        language="bn",
        script_pref="auto",
    )
    assert target_script == "beng"
    for orig_seg, out_seg in zip(sample_bn["segments"], norm_segs, strict=True):
        assert len(out_seg["words"]) == len(orig_seg["words"])
        assert unicodedata.is_normalized("NFC", out_seg["text"])
        for orig_w, out_w in zip(orig_seg["words"], out_seg["words"], strict=True):
            assert out_w["w"] == unicodedata.normalize("NFC", orig_w["w"])
            assert unicodedata.is_normalized("NFC", out_w["w"])
            assert out_w["s"] == orig_w["s"]
            assert out_w["e"] == orig_w["e"]
            assert out_w["p"] == orig_w["p"]
            assert out_w["script"] == "beng"


def test_normalize_script_tokens_language_defaults_and_explicit_script_pref() -> None:
    hindi_sample = json.loads((SAMPLES_DIR / "sample_hindi_motivation.transcript.json").read_text(encoding="utf-8"))

    # Default for hi -> deva (keeps Devanagari intact)
    deva_segs, deva_sc = normalize_script_tokens(hindi_sample, language="hi", script_pref="auto")
    assert deva_sc == "deva"
    assert detect_word_script(deva_segs[0]["words"][0]["w"]) == "deva"

    # Explicit script_pref='latn' for hi -> transliterates to Roman preserving exact token count
    roman_segs, roman_sc = normalize_script_tokens(hindi_sample, language="hi", script_pref="latn")
    assert roman_sc == "latn"
    for orig_seg, out_seg in zip(hindi_sample["segments"], roman_segs, strict=True):
        assert len(out_seg["words"]) == len(orig_seg["words"])
        for orig_w, out_w in zip(orig_seg["words"], out_seg["words"], strict=True):
            assert out_w["w"]
            assert out_w["s"] == orig_w["s"]
            assert out_w["e"] == orig_w["e"]
            assert detect_word_script(out_w["w"]) == "latn"


def test_llm_fallback_validation_accepts_exact_count_and_rejects_mismatch() -> None:
    segs = [
        {
            "id": 0,
            "start": 0.0,
            "end": 3.0,
            "text": "Viral सफलता है",
            "words": [
                {"w": "Viral", "s": 0.0, "e": 0.9, "p": 0.98, "flags": []},
                {"w": "सफलता", "s": 1.0, "e": 2.1, "p": 0.25, "flags": ["low_conf"]},
                {"w": "है", "s": 2.2, "e": 2.9, "p": 0.91, "flags": []},
            ],
        }
    ]

    # 1) Valid 1-to-1 LLM fallback output is accepted
    valid_segs, sc = normalize_script_tokens(
        segs,
        language="hinglish",
        llm_fallback=lambda tokens, _lang, _sc: ["Viral", "safalta", "hai"],
    )
    assert sc == "latn"
    assert [w["w"] for w in valid_segs[0]["words"]] == ["Viral", "safalta", "hai"]
    assert valid_segs[0]["words"][1]["flags"] == ["low_conf"]

    # 2) LLM returning mismatched token count (4 instead of 3) is rejected -> rule-based fallback used
    mismatch_segs, _ = normalize_script_tokens(
        segs,
        language="hinglish",
        llm_fallback=lambda _tokens, _lang, _sc: ["Viral", "sa", "falta", "hai"],
    )
    assert len(mismatch_segs[0]["words"]) == 3
    assert mismatch_segs[0]["words"][0]["w"] == "Viral"
    assert detect_word_script(mismatch_segs[0]["words"][1]["w"]) == "latn"

    # 3) LLM corrupting a pure-Latin token is rejected -> rule-based fallback used
    corrupt_latin_segs, _ = normalize_script_tokens(
        segs,
        language="hinglish",
        llm_fallback=lambda _tokens, _lang, _sc: ["Changed", "safalta", "hai"],
    )
    assert corrupt_latin_segs[0]["words"][0]["w"] == "Viral"

    # 4) LLM raising exception falls back cleanly
    err_segs, _ = normalize_script_tokens(
        segs,
        language="hinglish",
        llm_fallback=lambda *_args: (_ for _ in ()).throw(RuntimeError("timeout")),
    )
    assert len(err_segs[0]["words"]) == 3
    assert err_segs[0]["words"][0]["w"] == "Viral"


def test_property_token_count_and_timestamps_preserved_across_random_streams() -> None:
    rng = random.Random(20261118)
    vocab_pool = [
        "Creator",
        "Growth",
        "API",
        "99%",
        "सफलता",
        "ज़िंदगी",
        "फ़िल्म",
        "दोस्तों,",
        "क्योंकि",
        "संभव",
        "प्रभाव!",
        "न",
        "२०२६",
        "ভাইরাল",
        unicodedata.normalize("NFD", "হয়ে"),
        unicodedata.normalize("NFD", "কৌশল"),
    ]

    for trial in range(40):
        num_segs = rng.randint(1, 4)
        input_segs: list[dict[str, Any]] = []
        t = 0.0
        for seg_id in range(num_segs):
            n_words = rng.randint(1, 14)
            words: list[dict[str, Any]] = []
            for _ in range(n_words):
                tok = rng.choice(vocab_pool)
                s = round(t, 3)
                e = round(t + rng.uniform(0.15, 0.8), 3)
                p = round(rng.uniform(0.1, 0.99), 3)
                flags = ["low_conf"] if p < 0.3 else []
                words.append({"w": tok, "s": s, "e": e, "p": p, "flags": flags})
                t = e + 0.05
            input_segs.append(
                {
                    "id": seg_id,
                    "start": words[0]["s"],
                    "end": words[-1]["e"],
                    "text": " ".join(w["w"] for w in words),
                    "words": words,
                }
            )

        lang = ("hinglish", "hi", "bn", "en")[trial % 4]
        pref = ("latn", "auto", "auto", "latn")[trial % 4]
        out_segs, out_script = normalize_script_tokens(input_segs, language=lang, script_pref=pref)

        assert len(out_segs) == len(input_segs)
        for in_seg, out_seg in zip(input_segs, out_segs, strict=True):
            assert len(out_seg["words"]) == len(in_seg["words"])
            for in_w, out_w in zip(in_seg["words"], out_seg["words"], strict=True):
                assert isinstance(out_w["w"], str) and len(out_w["w"]) >= 1
                assert not any(ch.isspace() for ch in out_w["w"])
                assert out_w["s"] == in_w["s"]
                assert out_w["e"] == in_w["e"]
                assert out_w["p"] == in_w["p"]
                assert out_w["flags"] == in_w["flags"]
                if out_script == "latn" and detect_word_script(in_w["w"]) == "latn":
                    assert out_w["w"] == unicodedata.normalize("NFC", in_w["w"])


def test_casing_restoration_and_caption_profanity_masking_preserve_timings() -> None:
    segs = [
        {
            "id": 0,
            "start": 0.0,
            "end": 3.0,
            "text": "hello i said shit!",
            "words": [
                {"w": "hello", "s": 0.0, "e": 0.6, "p": 0.95},
                {"w": "i", "s": 0.7, "e": 1.0, "p": 0.92},
                {"w": "said", "s": 1.1, "e": 1.8, "p": 0.94},
                {"w": "shit!", "s": 1.9, "e": 2.9, "p": 0.89},
            ],
        }
    ]

    restored = restore_punctuation_and_casing(segs, language="en")
    assert [w["w"] for w in restored[0]["words"]] == ["Hello", "I", "said", "shit!"]
    assert [w["s"] for w in restored[0]["words"]] == [0.0, 0.7, 1.1, 1.9]
    assert [w["e"] for w in restored[0]["words"]] == [0.6, 1.0, 1.8, 2.9]

    masked = mask_profanity_for_captions(restored)
    assert [w["w"] for w in masked[0]["words"]] == ["Hello", "I", "said", "s**t!"]
    assert [w["s"] for w in masked[0]["words"]] == [0.0, 0.7, 1.1, 1.9]
    assert [w["e"] for w in masked[0]["words"]] == [0.6, 1.0, 1.8, 2.9]
    # Original segments were not mutated
    assert segs[0]["words"][0]["w"] == "hello"
    assert restored[0]["words"][3]["w"] == "shit!"
