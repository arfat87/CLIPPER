---
id: transcription
owner: pipeline
status: approved-v2
---
# Transcription (ASR), languages, Hinglish

## Engine abstraction
```python
class ASREngine(Protocol):
    id: str
    def supports(self, lang: str) -> bool: ...
    def transcribe(self, wav: Path, lang: str|None, opts: ASROpts, progress) -> RawTranscript: ...
```
Engines are registered in `config/asr.yaml` with per-language preference order. v1 ships `faster_whisper` plus an adapter slot for one Indic-specialised engine chosen by the bake-off.

## ASR bake-off (task T-3.1b) - do not lock a default before this
Evidence from public pages suggests Whisper large-v3 is only moderate on Hindi and weak on Hinglish, and that smaller Hinglish/Indic models can beat it, but those numbers come from model authors. Therefore:
1. Owner supplies 5-10 real clips (60-120 s each, mix of en/hi/hinglish/bn) with correct reference transcripts (`assets/samples/reference/`).
2. Pipeline agent runs each candidate engine, computes WER/CER after normalisation (lowercase, strip punctuation, script-normalise), timing sanity, speed, VRAM.
3. Result table goes to `docs/adr/0002-asr-default.md`; `config/asr.yaml` defaults follow the data.
Candidates (verify availability/licence at run time): faster-whisper large-v3 and medium int8; a Hinglish fine-tune of Whisper; AI4Bharat IndicConformer for Hindi (note 30 s max input -> chunk with VAD); optionally a cloud STT behind the same interface.

## Language detection
Run on 3 windows (start/middle/end, 30 s each) + majority vote. Rule for Hinglish: detected `hi` AND >= 25 % of tokens are Latin-script/English words in a Roman-script pass -> `hinglish`. Owner override always wins. Store detection evidence in `asr/lang.json`.

## Decode settings (defaults)
`word_timestamps=True`, `vad_filter=True`, `beam_size=5`, `condition_on_previous_text=False` (avoid hallucination loops), 30 s windows with 5 s overlap for long videos, temperature fallback list. Hallucination guard: drop segments with compression_ratio > 2.4 or repeated n-grams.

## Alignment
Forced alignment (WhisperX/wav2vec2) for en/hi when a model exists; else keep engine word times. Target: median word boundary error <= 120 ms on the sample set. Words with p < 0.3 flagged `low_conf`.

## Script normalisation (`script.py`)
| Language | Default output | Option |
|---|---|---|
| en | latn | - |
| hi | deva | roman via transliteration |
| hinglish | latn (Roman) | deva |
| bn | beng (NFC) | - |
Transliteration: rule/ML transliterator for Devanagari -> Roman, English tokens untouched; LLM transliteration is a fallback, always schema-validated and word-count-preserving (1 token in -> 1 token out, so timings stay attached).

## Post-processing
Punctuation/casing restoration (light, deterministic or LLM, count-preserving), number/units normalisation off by default, profanity masking is a caption option not a transcript change, optional speaker labels (v1.1 `Diarizer`).

## Output `asr/transcript.json`
Schema: `contracts/schemas/transcript.schema.json`. Words carry `w, s, e, p, flags`. Segments carry `id, start, end, text, words`. Also `engine`, `model_id`, `language`, `script`, `duration`, `mean_conf`.

## Editing
Owner edits words in the editor -> `PATCH /jobs/{id}/transcript` -> timings preserved for unchanged tokens; inserted tokens inherit neighbour timing (interpolated); `edited=true`.

## Tests
Per-language WER/CER on samples, timing error test, hallucination-guard test, Hinglish detection on 4 samples, NFC test for Bengali, token-count-preserving transliteration property test.
