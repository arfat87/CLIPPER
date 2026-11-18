from __future__ import annotations

import copy
import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest

from backend.pipeline.asr.aligner import (
    AlignmentUnavailableError,
    EngineWordTimeFallbackAligner,
    WhisperXAligner,
    WordAligner,
    align_transcript,
    extract_words,
    median_word_boundary_error_ms,
    median_word_end_error_ms,
    median_word_start_error_ms,
    word_boundary_error_ms,
    word_end_error_ms,
    word_start_error_ms,
)
from backend.pipeline.asr.engine import RawSegment, RawTranscript, RawWord

REPO_ROOT = Path(__file__).resolve().parents[3]
TRANSCRIPT_SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "transcript.schema.json"
ENGLISH_SAMPLE = REPO_ROOT / "assets" / "samples" / "sample_english_keynote.transcript.json"
HINDI_SAMPLE = REPO_ROOT / "assets" / "samples" / "sample_hindi_motivation.transcript.json"
HINGLISH_SAMPLE = REPO_ROOT / "assets" / "samples" / "sample_hinglish_podcast.transcript.json"
BENGALI_SAMPLE = REPO_ROOT / "assets" / "samples" / "sample_bengali_tech.transcript.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(TRANSCRIPT_SCHEMA_PATH.read_text(encoding="utf-8"))


def _write_silent_wav(path: Path, duration_s: float = 2.0, sample_rate: int = 16000) -> Path:
    nframes = int(round(duration_s * sample_rate))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * nframes)
    return path


def _assert_schema_and_invariants(transcript: dict[str, Any]) -> None:
    jsonschema.validate(instance=transcript, schema=_load_schema())
    prev_word_end = 0.0
    for seg in transcript["segments"]:
        assert 0.0 <= seg["start"] <= seg["end"]
        for w in seg["words"]:
            assert len(w["w"]) >= 1
            assert 0.0 <= w["s"] <= w["e"]
            assert w["s"] >= seg["start"] - 1e-9
            assert w["e"] <= seg["end"] + 1e-9
            assert w["s"] >= prev_word_end - 1e-9
            prev_word_end = w["e"]


@pytest.mark.parametrize(
    ("sample_path", "lang"),
    [
        (ENGLISH_SAMPLE, "en"),
        (HINDI_SAMPLE, "hi"),
    ],
)
def test_whisperx_aligner_en_hi_accuracy_bounds_and_low_conf(
    tmp_path: Path,
    sample_path: Path,
    lang: str,
) -> None:
    reference = json.loads(sample_path.read_text(encoding="utf-8"))
    ref_words = extract_words(reference)

    # Create a drifted ASR transcript (+0.26s drift) with stale low_conf on word 0
    noisy = copy.deepcopy(reference)
    for seg in noisy["segments"]:
        for idx, w in enumerate(seg["words"]):
            w["s"] = min(seg["end"], w["s"] + 0.26)
            w["e"] = min(seg["end"], max(w["s"], w["e"] + 0.26))
            if idx == 0:
                w["p"] = 0.2
                w["flags"] = ["low_conf"]

    assert median_word_boundary_error_ms(noisy, reference) > 200.0

    load_calls: list[tuple[str, str]] = []

    def fake_model_loader(language: str, device: str) -> tuple[Any, Any]:
        load_calls.append((language, device))
        return {"align_model": language}, {"language": language, "dictionary": {}}

    def fake_align_fn(
        wx_segments: list[dict[str, Any]],
        _model: Any,
        _metadata: Any,
        _audio: Any,
        _device: str,
    ) -> dict[str, Any]:
        aligned_segs: list[dict[str, Any]] = []
        for seg_idx, _wx_seg in enumerate(wx_segments):
            ref_seg = reference["segments"][seg_idx]
            wx_words: list[dict[str, Any]] = []
            for w_idx, rw in enumerate(ref_seg["words"]):
                # Small +-35ms alignment jitter; deliberately push first word slightly before seg.start
                jitter_s = -0.5 if (seg_idx == 0 and w_idx == 0) else (0.03 if w_idx % 2 == 0 else -0.025)
                jitter_e = 0.5 if (seg_idx == 0 and w_idx == len(ref_seg["words"]) - 1) else (0.02 if w_idx % 2 == 0 else -0.03)
                score = 0.19 if w_idx == 1 else (0.88 if w_idx == 0 else 0.94)
                wx_words.append(
                    {
                        "word": rw["w"],
                        "start": rw["s"] + jitter_s,
                        "end": rw["e"] + jitter_e,
                        "score": score,
                    }
                )
            aligned_segs.append(
                {
                    "start": ref_seg["start"],
                    "end": ref_seg["end"],
                    "text": ref_seg["text"],
                    "words": wx_words,
                }
            )
        return {"segments": aligned_segs}

    wav_path = _write_silent_wav(tmp_path / f"{lang}.wav")
    aligner = WhisperXAligner(
        device="cpu",
        model_loader=fake_model_loader,
        audio_loader=lambda p: f"loaded:{p.name}",
        align_fn=fake_align_fn,
    )
    assert isinstance(aligner, WordAligner)

    aligned = align_transcript(
        noisy,
        wav_path,
        enabled=True,
        languages=("en", "hi"),
        low_conf_threshold=0.3,
        aligner=aligner,
    )

    _assert_schema_and_invariants(aligned)
    assert aligned["language"] == lang
    assert aligned["produced_by"]["alignment"]["aligner"] == "whisperx"
    assert aligned["produced_by"]["alignment"]["fallback_used"] is None
    assert aligned["produced_by"]["alignment"]["warnings"] == []

    # Token count strictly preserved
    aligned_words = extract_words(aligned)
    assert len(aligned_words) == len(ref_words)

    # T-3.3 acceptance target: median word boundary error <= 120 ms
    med_err = median_word_boundary_error_ms(aligned_words, ref_words)
    assert med_err <= 120.0

    # Out-of-bounds words clamped to segment bounds
    first_seg = aligned["segments"][0]
    assert first_seg["words"][0]["s"] >= first_seg["start"]
    assert first_seg["words"][-1]["e"] <= first_seg["end"]

    # low_conf updated according to aligned score (< 0.3 flagged, >= 0.3 cleared)
    assert "low_conf" not in first_seg["words"][0].get("flags", [])
    assert "low_conf" in first_seg["words"][1]["flags"]

    # Repeated alignment on same language reuses cached align model
    align_transcript(noisy, wav_path, aligner=aligner)
    assert load_calls == [(lang, "cpu")]


def test_whisperx_lazy_import_and_cache_via_sys_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_model_calls: list[tuple[str, str]] = []
    align_calls: list[str] = []

    def fake_load_align_model(*, language_code: str, device: str) -> tuple[Any, Any]:
        load_model_calls.append((language_code, device))
        return {"m": language_code}, {"d": device}

    def fake_load_audio(path_str: str) -> str:
        return f"audio:{Path(path_str).name}"

    def fake_align(
        segments: list[dict[str, Any]],
        _model: Any,
        _meta: Any,
        audio: str,
        _device: str,
        *,
        return_char_alignments: bool = False,
    ) -> dict[str, Any]:
        assert return_char_alignments is False
        align_calls.append(audio)
        return {
            "segments": [
                {
                    "start": segments[0]["start"],
                    "end": segments[0]["end"],
                    "text": segments[0]["text"],
                    "words": [
                        {"word": "Hello", "start": 0.25, "end": 0.70, "score": 0.91},
                        {"word": "world", "start": 0.75, "end": 1.30, "score": 0.22},
                    ],
                }
            ]
        }

    fake_wx = SimpleNamespace(
        load_align_model=fake_load_align_model,
        load_audio=fake_load_audio,
        align=fake_align,
    )
    monkeypatch.setitem(sys.modules, "whisperx", fake_wx)

    wav_path = _write_silent_wav(tmp_path / "sample.wav")
    raw = RawTranscript(
        language="en",
        script="latn",
        duration=2.0,
        engine="faster_whisper",
        model_id="medium",
        segments=[
            RawSegment(
                id=0,
                start=0.2,
                end=1.5,
                text="Hello world",
                words=[
                    RawWord(w="Hello", s=0.3, e=0.8, p=0.9),
                    RawWord(w="world", s=0.85, e=1.4, p=0.85),
                ],
            )
        ],
    )

    aligner = WhisperXAligner(device="cpu")
    res1 = align_transcript(raw, wav_path, aligner=aligner)
    res2 = align_transcript(raw, wav_path, aligner=aligner)

    _assert_schema_and_invariants(res1)
    assert len(load_model_calls) == 1
    assert len(align_calls) == 2
    assert res1["segments"][0]["words"][0]["s"] == pytest.approx(0.25)
    assert "low_conf" in res1["segments"][0]["words"][1]["flags"]
    assert res2["produced_by"]["alignment"]["fallback_used"] is None


def test_fallback_when_whisperx_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "whisperx", None)
    transcript = json.loads(ENGLISH_SAMPLE.read_text(encoding="utf-8"))
    orig_count = len(extract_words(transcript))
    wav_path = tmp_path / "missing_audio.wav"

    res = align_transcript(transcript, wav_path)
    _assert_schema_and_invariants(res)

    meta = res["produced_by"]["alignment"]
    assert meta["aligner"] == "engine_word_times"
    assert meta["fallback_used"] == "engine_word_times"
    assert len(meta["warnings"]) == 1
    assert "whisperx" in meta["warnings"][0].lower()
    assert len(extract_words(res)) == orig_count


def test_fallback_when_aligner_raises_runtime_error(tmp_path: Path) -> None:
    class FailingAligner:
        id = "whisperx"

        def supports(self, lang: str) -> bool:
            return lang in {"en", "hi"}

        def align(
            self,
            transcript: dict[str, Any],
            wav_path: Path,
            *,
            low_conf_threshold: float = 0.3,
        ) -> dict[str, Any]:
            raise RuntimeError("CUDA out of memory during wav2vec2 forward pass")

    transcript = json.loads(HINDI_SAMPLE.read_text(encoding="utf-8"))
    orig_count = len(extract_words(transcript))
    wav_path = _write_silent_wav(tmp_path / "hi.wav")

    res = align_transcript(transcript, wav_path, aligner=FailingAligner())
    _assert_schema_and_invariants(res)

    meta = res["produced_by"]["alignment"]
    assert meta["aligner"] == "engine_word_times"
    assert meta["fallback_used"] == "engine_word_times"
    assert len(meta["warnings"]) == 1
    assert "CUDA out of memory" in meta["warnings"][0]
    assert len(extract_words(res)) == orig_count


@pytest.mark.parametrize("sample_path", [BENGALI_SAMPLE, HINGLISH_SAMPLE])
def test_fallback_for_unsupported_languages_bn_and_hinglish(
    tmp_path: Path,
    sample_path: Path,
) -> None:
    transcript = json.loads(sample_path.read_text(encoding="utf-8"))
    orig_count = len(extract_words(transcript))

    aligner = WhisperXAligner(
        model_loader=lambda *_args: pytest.fail("model_loader must not be called for unsupported lang"),
        align_fn=lambda *_args: pytest.fail("align_fn must not be called for unsupported lang"),
    )
    res = align_transcript(transcript, tmp_path / "audio.wav", aligner=aligner)
    _assert_schema_and_invariants(res)

    meta = res["produced_by"]["alignment"]
    assert meta["aligner"] == "engine_word_times"
    assert meta["fallback_used"] == "engine_word_times"
    assert len(meta["warnings"]) == 1
    assert len(extract_words(res)) == orig_count


def test_fallback_when_alignment_disabled(tmp_path: Path) -> None:
    transcript = json.loads(ENGLISH_SAMPLE.read_text(encoding="utf-8"))
    res = align_transcript(transcript, tmp_path / "audio.wav", enabled=False)
    _assert_schema_and_invariants(res)
    assert res["produced_by"]["alignment"]["fallback_used"] == "engine_word_times"
    assert res["produced_by"]["alignment"]["enabled"] is False
    assert len(res["produced_by"]["alignment"]["warnings"]) == 1


def test_edge_cases_zero_duration_overlap_missing_and_empty_segments(tmp_path: Path) -> None:
    noisy = {
        "schema_version": 1,
        "language": "en",
        "script": "latn",
        "duration": 10.0,
        "engine": "faster_whisper",
        "segments": [
            {
                "id": 0,
                "start": 1.0,
                "end": 5.0,
                "text": "zero overlap backwards missing",
                "speaker": "S1",
                "words": [
                    {"w": "zero", "s": 1.2, "e": 1.2, "p": 0.25},      # zero duration + low_conf
                    {"w": "overlap", "s": 1.2, "e": 2.8, "p": 0.9},   # touches/overlaps after zero-dur fix
                    {"w": "backwards", "s": 2.3, "e": 2.1, "p": 0.8}, # overlaps prev & inverted s > e
                    {"w": "missing", "s": None, "e": None, "p": 0.95},
                    {"w": "outside", "s": 4.9, "e": 6.4, "p": 0.1},   # exceeds seg.end + low_conf
                ],
            },
            {
                "id": 1,
                "start": 6.0,
                "end": 7.5,
                "text": "",
                "words": [],
            },
        ],
    }

    fallback = EngineWordTimeFallbackAligner()
    assert isinstance(fallback, WordAligner)
    repaired = align_transcript(noisy, tmp_path / "dummy.wav", enabled=False)
    _assert_schema_and_invariants(repaired)

    words0 = repaired["segments"][0]["words"]
    assert [w["w"] for w in words0] == ["zero", "overlap", "backwards", "missing", "outside"]
    # Zero duration repaired to positive duration
    assert words0[0]["e"] > words0[0]["s"]
    # Monotonic across all repaired words
    for i in range(len(words0) - 1):
        assert words0[i]["e"] <= words0[i + 1]["s"]
    # Clamped within segment bounds
    assert words0[0]["s"] >= 1.0
    assert words0[-1]["e"] <= 5.0
    # Missing timestamps received interpolated flag
    assert "interpolated" in words0[3]["flags"]
    # Low-confidence flags preserved/applied accurately
    assert "low_conf" in words0[0]["flags"]
    assert "low_conf" not in words0[1].get("flags", [])
    assert "low_conf" in words0[4]["flags"]

    # Empty segments list handled without crash
    empty_transcript = {
        "schema_version": 1,
        "language": "en",
        "script": "latn",
        "duration": 0.0,
        "engine": "faster_whisper",
        "segments": [],
    }
    empty_res = align_transcript(empty_transcript, tmp_path / "empty.wav")
    _assert_schema_and_invariants(empty_res)
    assert empty_res["segments"] == []


def test_whisperx_aligner_token_count_mismatch_and_direct_unsupported_error(tmp_path: Path) -> None:
    transcript = {
        "schema_version": 1,
        "language": "en",
        "script": "latn",
        "duration": 3.0,
        "engine": "faster_whisper",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 3.0,
                "text": "One 2025 three",
                "words": [
                    {"w": "One", "s": 0.1, "e": 0.6, "p": 0.9},
                    {"w": "2025", "s": 0.7, "e": 1.3, "p": 0.9},
                    {"w": "three", "s": 1.4, "e": 2.1, "p": 0.9},
                ],
            }
        ],
    }

    # Simulated WhisperX dropping numeric token "2025"
    aligner = WhisperXAligner(
        device="cpu",
        model_loader=lambda _l, _d: ("m", {}),
        audio_loader=lambda _p: "audio",
        align_fn=lambda *_args: {
            "segments": [
                {
                    "start": 0.0,
                    "end": 3.0,
                    "text": "One 2025 three",
                    "words": [
                        {"word": "One", "start": 0.12, "end": 0.58, "score": 0.95},
                        {"word": "three", "start": 1.42, "end": 2.05, "score": 0.92},
                    ],
                }
            ]
        },
    )
    res = align_transcript(transcript, tmp_path / "a.wav", aligner=aligner)
    _assert_schema_and_invariants(res)
    assert [w["w"] for w in res["segments"][0]["words"]] == ["One", "2025", "three"]

    with pytest.raises(AlignmentUnavailableError):
        aligner.align({**transcript, "language": "bn"}, tmp_path / "a.wav")


def test_boundary_error_metrics_helpers() -> None:
    pred = [
        {"w": "a", "s": 1.05, "e": 1.55},  # start err 50ms, end err 50ms, avg 50ms
        {"w": "b", "s": 2.10, "e": 2.40},  # start err 100ms, end err 0ms, avg 50ms
        {"w": "c", "s": 3.00, "e": 3.80},  # start err 0ms, end err 200ms, avg 100ms
    ]
    ref = [
        {"w": "a", "s": 1.00, "e": 1.50},
        {"w": "b", "s": 2.00, "e": 2.40},
        {"w": "c", "s": 3.00, "e": 3.60},
    ]

    assert word_start_error_ms(pred[1], ref[1]) == pytest.approx(100.0)
    assert word_end_error_ms(pred[2], ref[2]) == pytest.approx(200.0)
    assert word_boundary_error_ms(pred[0], ref[0]) == pytest.approx(50.0)
    assert median_word_start_error_ms(pred, ref) == pytest.approx(50.0)
    assert median_word_end_error_ms(pred, ref) == pytest.approx(50.0)
    assert median_word_boundary_error_ms(pred, ref) == pytest.approx(50.0)
    assert median_word_boundary_error_ms([], []) == pytest.approx(0.0)

    with pytest.raises(ValueError, match="Word count mismatch"):
        median_word_boundary_error_ms(pred[:2], ref)
