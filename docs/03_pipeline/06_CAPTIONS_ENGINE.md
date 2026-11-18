---
id: captions-engine
owner: pipeline
status: approved-v2
---
# Captions engine (burned-in captions)

Pipeline: words (from clip_plan time range, after edits) -> chunker -> pages -> ASS builder (template) -> `captions.ass` (+ `.srt`, `.vtt`) -> burned by ffmpeg `ass=` filter (libass + HarfBuzz).

## Chunker (`chunker.py`)
- Operates on **words**, never characters (never split a conjunct/grapheme cluster).
- Limits from template: `max_words_per_line`, `max_lines`, `max_chars` per script (Devanagari/Bengali ~0.8x Latin budget), min page duration 0.35 s, max 2.5 s.
- Break preferences: sentence end > comma/pause > conjunction > balanced line lengths (minimise raggedness).
- Modes: `pages` (phrase at a time), `word_by_word` (one/two words), `karaoke` (full line with progressive highlight).
- Keep numbers with units together; keep names (capitalised runs) together when possible.
- Gaps: if a pause > 0.8 s, hide caption (no stale text on screen).

## ASS builder (`ass_builder.py`) - pure function
Header: `PlayResX/Y` = output size (1080x1920), `ScaledBorderAndShadow: yes`, `WrapStyle: 2` (we wrap ourselves), `YCbCr Matrix: TV.709`.
One style per (template, script). Events positioned with `\an` and `\pos` computed from safe area.
Animations (override tags):
| Name | Implementation |
|---|---|
| `karaoke_fill` | `\kf<cs>` per word |
| `highlight_word` | per-word split events: active word `\c&H<accent>&\fscx115\fscy115`, others primary |
| `pop` | `\t(0,80,\fscx120\fscy120)\t(80,160,\fscx100\fscy100)` |
| `typewriter` | cumulative reveal events |
| `fade_slide` | `\fad(80,80)\move(x1,y1,x2,y2)` |
| `bounce` | `\t` chain on `\fscy` with accel |
| `emoji_auto` (v1.1) | LLM-chosen emoji per page, rendered with an emoji font |
Boxes: `BorderStyle=3` for simple opaque box; rounded box via vector drawing (`\p1`) is v1.1.

## Fonts and scripts
Template declares `fonts.latn/deva/beng` (+ fallback lists). Builder detects script **per word** by Unicode block and inserts inline `\fn` overrides on mixed lines (Hinglish, Bengali-English). `fontsdir=assets/fonts` (+ template fonts). Case transforms (`upper`) apply only to Latin words.

## Emphasis
Keywords from `emphasis.json` (LLM-tagged per clip: names, numbers, strong words) get bold/colour from template. Profanity mask option replaces middle letters with `*` in captions only.

## Safe areas
From `config/platforms.yaml` profile (default conservative: top 12 %, bottom 25 %, right 12 %, left 6 % on 9:16). Template `y_percent` is clamped into the safe box; preview overlay shows it.

## Sidecars
`.srt` (UTF-8, sentence-level pages), `.vtt`, and `.ass` are written for every clip and are what the Publish Pack offers for upload.

## Tests
Golden `.ass` for fixed transcripts; snapshot frames for Devanagari matras, Bengali conjuncts (e.g. ক্ষ, স্ত্র), Hinglish mixed lines; no tofu detection (compare rendered glyph count / use fonttools coverage); chunker property tests (no word split, limits respected).
