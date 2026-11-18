from __future__ import annotations

import json
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest

from backend.pipeline.asr import faster_whisper_engine as fw_mod
from backend.pipeline.asr.engine import (
    ASREngine,
    ASREngineRegistry,
    ASREngineUnavailableError,
    ASROpts,
    RawSegment,
    RawTranscript,
    RawWord,
    compute_wer,
)
from backend.pipeline.asr.faster_whisper_engine import (
    FasterWhisperEngine,
    ModelCache,
    detect_asr_device,
    is_hallucinated_segment,
    max_consecutive_ngram_repeat,
    resolve_model_and_device,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TRANSCRIPT_SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "transcript.schema.json"
SPEC_V2_ASR_YAML = REPO_ROOT / "config" / "spec_v2" / "asr.yaml"
V1_ASR_YAML = REPO_ROOT / "config" / "asr.yaml"
ENGLISH_SAMPLE_TRANSCRIPT = REPO_ROOT / "assets" / "samples" / "sample_english_keynote.transcript.json"


def _make_word(w: str, s: float, e: float, p: float = 0.95) -> SimpleNamespace:
    return SimpleNamespace(word=w, start=s, end=e, probability=p)


def _make_segment(
    start: float,
    end: float,
    text: str,
    words: list[SimpleNamespace] | None = None,
    compression_ratio: float = 1.2,
) -> SimpleNamespace:
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        words=words,
        compression_ratio=compression_ratio,
    )


def _write_silent_wav(path: Path, duration_s: float, sample_rate: int = 16000) -> Path:
    nframes = int(round(duration_s * sample_rate))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * nframes)
    return path


def test_detect_asr_device_cpu_fallback_and_mocked_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fw_mod, "_cuda_available_via_ctranslate2", lambda: False)
    monkeypatch.setattr(fw_mod, "_cuda_available_via_torch", lambda: False)
    monkeypatch.setattr(fw_mod, "_cuda_available_via_nvidia_smi", lambda: False)

    assert detect_asr_device("auto") == ("cpu", "int8")
    assert detect_asr_device("cpu") == ("cpu", "int8")
    assert detect_asr_device("cuda") == ("cuda", "float16")

    # ctranslate2 CUDA probe positive
    monkeypatch.setattr(fw_mod, "_cuda_available_via_ctranslate2", lambda: True)
    assert detect_asr_device("auto") == ("cuda", "float16")
    assert resolve_model_and_device(ASROpts()) == ("large-v3", "cuda", "float16")

    # CPU defaults vs explicit overrides
    monkeypatch.setattr(fw_mod, "_cuda_available_via_ctranslate2", lambda: False)
    assert resolve_model_and_device(ASROpts()) == ("medium", "cpu", "int8")
    assert resolve_model_and_device(
        ASROpts(model_id="small", device="cpu", compute_type="float32")
    ) == ("small", "cpu", "float32")


def test_detect_asr_device_probes_real_probe_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ct2 = SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)
    assert fw_mod._cuda_available_via_ctranslate2() is True
    assert detect_asr_device("auto") == ("cuda", "float16")

    fake_ct2_zero = SimpleNamespace(get_cuda_device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2_zero)
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert fw_mod._cuda_available_via_ctranslate2() is False
    assert fw_mod._cuda_available_via_torch() is True
    assert detect_asr_device("auto") == ("cuda", "float16")

    fake_torch_no = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch_no)
    monkeypatch.setattr(fw_mod.shutil, "which", lambda _cmd: None)
    assert detect_asr_device("auto") == ("cpu", "int8")


def test_model_cache_hit_reuse_thread_safety_and_cache_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_settings = SimpleNamespace(cache_dir=tmp_path / "custom_cache", data_dir=tmp_path / "data")
    monkeypatch.setattr(fw_mod, "get_settings", lambda: fake_settings)

    calls: list[tuple[str, str, str, Path]] = []

    def factory(model_id: str, device: str, compute_type: str, download_root: Path) -> dict[str, Any]:
        calls.append((model_id, device, compute_type, download_root))
        return {"model_id": model_id, "device": device, "compute_type": compute_type}

    cache = ModelCache(model_factory=factory)
    m1 = cache.get_or_load("medium", device="cpu", compute_type="int8")
    m2 = cache.get_or_load("medium", device="cpu", compute_type="int8")
    assert m1 is m2
    assert len(calls) == 1
    assert calls[0][3] == tmp_path / "custom_cache" / "whisper"
    assert (tmp_path / "custom_cache" / "whisper").is_dir()

    # Different key loads a new entry
    m3 = cache.get_or_load("large-v3", device="cuda", compute_type="float16")
    assert m3 is not m1
    assert len(calls) == 2

    # Concurrent access for same key still invokes factory once
    concurrent_cache = ModelCache(cache_dir=tmp_path / "conc_cache", model_factory=factory)
    calls.clear()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: concurrent_cache.get_or_load("small", device="cpu", compute_type="int8"),
                range(12),
            )
        )
    assert len(calls) == 1
    assert all(r is results[0] for r in results)


def test_model_cache_raises_unavailable_when_faster_whisper_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    cache = ModelCache(cache_dir=tmp_path)
    with pytest.raises(ASREngineUnavailableError, match="faster-whisper"):
        cache.get_or_load("medium", device="cpu", compute_type="int8")


def test_hallucination_guard_filters_compression_and_ngram_loops() -> None:
    assert max_consecutive_ngram_repeat("hello world") == 1
    assert max_consecutive_ngram_repeat("thank you thank you thank you thank you thank you") == 5
    assert (
        is_hallucinated_segment(
            "thank you thank you thank you thank you thank you",
            compression_ratio=1.3,
            max_compression_ratio=2.4,
            max_repeat_ngram=4,
        )
        is True
    )
    assert (
        is_hallucinated_segment(
            "normal clear sentence with varied words",
            compression_ratio=2.9,
            max_compression_ratio=2.4,
            max_repeat_ngram=4,
        )
        is True
    )
    assert (
        is_hallucinated_segment(
            "I had had a genuinely good time today",
            compression_ratio=1.2,
            max_compression_ratio=2.4,
            max_repeat_ngram=4,
        )
        is False
    )


def test_faster_whisper_engine_mocked_transcribe_schema_flags_progress_and_wer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fw_mod, "_cuda_available_via_ctranslate2", lambda: False)
    monkeypatch.setattr(fw_mod, "_cuda_available_via_torch", lambda: False)
    monkeypatch.setattr(fw_mod, "_cuda_available_via_nvidia_smi", lambda: False)

    fixture_data = json.loads(ENGLISH_SAMPLE_TRANSCRIPT.read_text(encoding="utf-8"))
    ref_text = " ".join(seg["text"] for seg in fixture_data["segments"])

    transcribe_kwargs_seen: list[dict[str, Any]] = []

    class FakeWhisperModel:
        def transcribe(self, audio_path: str, **kwargs: Any) -> tuple[Any, Any]:
            transcribe_kwargs_seen.append({"audio_path": audio_path, **kwargs})
            segs = [
                _make_segment(
                    1.0,
                    18.0,
                    fixture_data["segments"][0]["text"],
                    words=[
                        _make_word(w["w"], w["s"], w["e"], 0.18 if i == 2 else 0.94)
                        for i, w in enumerate(fixture_data["segments"][0]["words"])
                    ],
                    compression_ratio=1.35,
                ),
                # Hallucinated by high compression_ratio -> must be dropped
                _make_segment(
                    18.5,
                    22.0,
                    "a b c d e f g h",
                    words=[_make_word("a", 18.5, 19.0, 0.9)],
                    compression_ratio=3.2,
                ),
                # Hallucinated by repeated n-gram loop -> must be dropped
                _make_segment(
                    22.5,
                    28.0,
                    "subscribe now subscribe now subscribe now subscribe now subscribe now",
                    words=[_make_word("subscribe", 22.5, 23.0, 0.9)],
                    compression_ratio=1.6,
                ),
                _make_segment(
                    20.0,
                    44.0,
                    fixture_data["segments"][1]["text"],
                    words=[
                        _make_word(w["w"], w["s"], w["e"], 0.91)
                        for w in fixture_data["segments"][1]["words"]
                    ],
                    compression_ratio=1.25,
                ),
                _make_segment(
                    46.0,
                    72.0,
                    fixture_data["segments"][2]["text"],
                    words=[
                        _make_word(w["w"], w["s"], w["e"], 0.89)
                        for w in fixture_data["segments"][2]["words"]
                    ],
                    compression_ratio=1.20,
                ),
            ]
            info = SimpleNamespace(language="en", duration=75.0)
            return iter(segs), info

    wav_path = _write_silent_wav(tmp_path / "english.wav", duration_s=10.0)
    progress_events: list[tuple[float, str]] = []

    engine = FasterWhisperEngine(
        model_cache=ModelCache(
            cache_dir=tmp_path / "cache",
            model_factory=lambda *_args: FakeWhisperModel(),
        )
    )
    assert isinstance(engine, ASREngine)

    opts = ASROpts(
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False,
        beam_size=5,
        low_conf_threshold=0.3,
        max_compression_ratio=2.4,
        max_repeat_ngram=4,
    )
    transcript = engine.transcribe(
        wav_path,
        "en",
        opts,
        progress=lambda pct, msg: progress_events.append((pct, msg)),
    )

    assert isinstance(transcript, RawTranscript)
    assert len(transcribe_kwargs_seen) == 1
    call_kw = transcribe_kwargs_seen[0]
    assert call_kw["word_timestamps"] is True
    assert call_kw["vad_filter"] is True
    assert call_kw["condition_on_previous_text"] is False
    assert call_kw["beam_size"] == 5
    assert call_kw["language"] == "en"

    # Hallucinated segments dropped; remaining re-indexed 0, 1, 2
    assert [seg.id for seg in transcript.segments] == [0, 1, 2]

    # Low-confidence word flagged
    assert "low_conf" in transcript.segments[0].words[2].flags
    assert "low_conf" not in transcript.segments[0].words[0].flags

    # Progress reported monotonically up to 100
    assert len(progress_events) >= 3
    pcts = [p for p, _ in progress_events]
    assert pcts == sorted(pcts)
    assert pcts[-1] == 100.0

    # Schema validation against contracts/schemas/transcript.schema.json
    payload = transcript.to_dict()
    schema = json.loads(TRANSCRIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)
    assert "low_conf" in payload["segments"][0]["words"][2]["flags"]

    # WER on English fixture <= 0.12
    hyp_text = " ".join(seg.text for seg in transcript.segments)
    wer = compute_wer(ref_text, hyp_text)
    assert wer <= 0.12


def test_faster_whisper_engine_long_audio_windowing(tmp_path: Path) -> None:
    wav_path = _write_silent_wav(tmp_path / "long_audio.wav", duration_s=65.0)
    win_calls: list[str] = []

    class WindowedFakeModel:
        def transcribe(self, audio_path: str, **_kwargs: Any) -> tuple[list[Any], Any]:
            idx = len(win_calls)
            win_calls.append(audio_path)
            if idx == 0:
                # Window 0: [0, 30]
                segs = [
                    _make_segment(2.0, 10.0, "First window opening", [_make_word("First", 2.0, 3.0)]),
                    _make_segment(26.0, 29.0, "Overlap zone line", [_make_word("Overlap", 26.0, 27.0)]),
                ]
            elif idx == 1:
                # Window 1: [25, 55] -> local t=2.0 is global t=27.0 (in left cut <=27.5, dropped)
                segs = [
                    _make_segment(1.0, 4.0, "Overlap zone line", [_make_word("Overlap", 1.0, 2.0)]),
                    _make_segment(10.0, 18.0, "Middle window content", [_make_word("Middle", 10.0, 11.0)]),
                ]
            else:
                # Window 2: [50, 65] -> local t=6.0 is global t=56.0
                segs = [
                    _make_segment(6.0, 12.0, "Final window closing", [_make_word("Final", 6.0, 7.0)]),
                ]
            return segs, SimpleNamespace(language="en", duration=30.0)

    engine = FasterWhisperEngine(
        model_cache=ModelCache(
            cache_dir=tmp_path / "cache",
            model_factory=lambda *_args: WindowedFakeModel(),
        )
    )
    res = engine.transcribe(wav_path, "en", ASROpts(window_s=30, overlap_s=5))

    assert len(win_calls) == 3  # [0,30], [25,55], [50,65]
    texts = [s.text for s in res.segments]
    assert texts == [
        "First window opening",
        "Overlap zone line",
        "Middle window content",
        "Final window closing",
    ]
    assert res.segments[2].start == pytest.approx(35.0)
    assert res.segments[3].start == pytest.approx(56.0)


def test_asr_engine_registry_resolution_and_unavailable_handling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ASREngineRegistry.from_config(config_path=SPEC_V2_ASR_YAML)

    for lang in ("en", "hi", "hinglish", "bn"):
        eng = registry.resolve(lang)
        assert eng.id == "faster_whisper"
        assert eng.supports(lang) is True

    opts_from_cfg = registry.build_opts()
    assert opts_from_cfg.beam_size == 5
    assert opts_from_cfg.vad_filter is True
    assert opts_from_cfg.condition_on_previous_text is False
    assert opts_from_cfg.word_timestamps is True
    assert opts_from_cfg.max_compression_ratio == pytest.approx(2.4)
    assert opts_from_cfg.max_repeat_ngram == 4
    assert opts_from_cfg.low_conf_threshold == pytest.approx(0.3)

    # Unsupported language raises ASREngineUnavailableError
    with pytest.raises(ASREngineUnavailableError):
        registry.resolve("klingon")

    # Custom indic_specialist takes precedence for hi/hinglish/bn when enabled
    class FakeIndicEngine:
        id = "indic_specialist"

        def supports(self, lang: str) -> bool:
            return lang in {"hi", "hinglish", "bn"}

        def transcribe(
            self,
            wav: Path,
            lang: str | None,
            opts: ASROpts,
            progress: Any = None,
        ) -> RawTranscript:
            return RawTranscript(language="hi", script="deva", duration=1.0, engine=self.id)

    registry.register(FakeIndicEngine(), enabled=True)
    assert registry.resolve("en").id == "faster_whisper"
    assert registry.resolve("hi").id == "indic_specialist"
    assert registry.resolve("hinglish").id == "indic_specialist"
    assert registry.resolve("bn").id == "indic_specialist"

    # v1 config fallback still resolves faster_whisper
    v1_registry = ASREngineRegistry.from_config(config_path=V1_ASR_YAML)
    assert v1_registry.resolve("en").id == "faster_whisper"

    # Graceful error when faster_whisper wheel is missing
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    wav_path = _write_silent_wav(tmp_path / "test.wav", duration_s=1.0)
    with pytest.raises(ASREngineUnavailableError, match="faster-whisper"):
        registry.resolve("en").transcribe(wav_path, "en", opts_from_cfg)


def test_compute_wer_normalization_and_edge_cases() -> None:
    assert compute_wer("Hello, World!", "hello world") == pytest.approx(0.0)
    assert compute_wer("one two three four", "one two three five") == pytest.approx(0.25)
    assert compute_wer("", "") == pytest.approx(0.0)
    assert compute_wer("", "extra") == pytest.approx(1.0)


def test_raw_transcript_to_dict_schema_compliance() -> None:
    transcript = RawTranscript(
        language="bn",
        script="beng",
        duration=4.25,
        engine="faster_whisper",
        model_id="medium",
        mean_conf=0.87,
        segments=[
            RawSegment(
                id=0,
                start=0.2,
                end=2.1,
                text="ভাইরাল ভিডিও",
                words=[
                    RawWord(w="ভাইরাল", s=0.2, e=1.0, p=0.92),
                    RawWord(w="ভিডিও", s=1.1, e=2.1, p=0.22, flags=["low_conf"]),
                ],
            )
        ],
    )
    payload = transcript.to_dict()
    schema = json.loads(TRANSCRIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload, schema=schema)
    assert payload["segments"][0]["words"][1]["flags"] == ["low_conf"]
