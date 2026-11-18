---
id: highlight-engine
owner: pipeline
status: approved-v2
---
# Highlight engine (select stage) - the core IP

Output: 3-12 clips, only those above threshold, each with an explainable Clip Score, title, hook line, hashtags, reason.

## Step A - Segmentation
Words -> thought units (sentence end punctuation or pause > 0.6 s). Slide over units to create candidate windows of `min_len..max_len` (default 20-60 s, target 35 s; genre can override, e.g. lecture 40-90 s). Never start/end mid-word.

## Step B - Signal features per candidate (0-1)
`energy, speech_rate, audience_reaction, scene_dynamics, heatmap, asr_confidence, face_presence`, plus `music_penalty`. Missing -> excluded and weight redistributed.

## Step C - LLM semantic scoring (map-reduce)
- **Map**: split transcript into ~8-min chunks with 60 s overlap; LLM returns candidate moments by word indices.
- **Reduce**: re-rank top ~30 with whole-video summary + `topics.json` + Clip Brief.
- **Dimensions (0-100):** hook, payoff, standalone, emotion, shareability, quotability. `L` = weighted composite (weights in config).
- **Clip Brief**: if present, inserted as the owner's priority instruction in a clearly delimited block; LLM must output `brief_match 0-100`; low match demotes.
- Prompts: `config/prompts/highlight_map.md`, `highlight_reduce.md` (versioned, editable in admin).

### Prompt-injection defence (mandatory)
Transcript is wrapped as `<transcript>` data with an explicit statement that it may contain instructions which must be ignored. LLM has no tools. Output must validate against JSON Schema; invalid -> one repair attempt -> else fallback to signals-only. Indices are validated against transcript length. Titles/hashtags are sanitised (length, charset, no URLs unless owner template adds them).

## Step D - Fusion and selection (`ranker.py`)
`S = sum(w_i * f_i)` with weights per genre from `config/highlights.yaml`. Default (talk content):
`S = 0.55 L + 0.10 energy + 0.07 speech_rate + 0.08 reaction + 0.05 scene + 0.10 heatmap + 0.05 asr_conf` (heatmap weight moves to L if absent).
- Non-max suppression: drop candidates overlapping a higher one by > 30 %.
- Semantic dedupe: drop near-duplicate content (embedding cosine > 0.9).
- Diversity: at most 2 clips per 3-minute region and per topic.
- Adaptive count: keep `S >= threshold` (0.62), cap `max_clips` (12), floor `min_clips` (3) if any candidate >= 0.45. Always report why fewer than max.
- Optional: `reselect` endpoint re-runs with new brief/threshold without redoing ASR/analysis.

## Step E - Boundary snapping (`boundaries.py`)
Start: nearest thought-unit start within +-2 s lying in a VAD silence; End: sentence end + 0.3 s tail; avoid scene cuts within 0.4 s of a boundary; ensure the opening 3 s contains speech (hook), and trailing 0.3 s has no new word. All boundaries in silence +-50 ms.

## Step F - Clip creation
For each selected clip write `clips/<id>/clip_plan.json` (default plan from preset) and `highlights/clips.json` with rank, score, `score_breakdown`, `score_mode`, title, hook_line, hashtags, reason.

## Score honesty and explainability
UI shows breakdown bars and `reason`. Label is "Clip Score", never "virality probability".

## Feedback and tuning
Owner thumbs + optional actual views -> `feedback_event`. Admin "Scoring" screen shows correlation of each feature/LLM dimension with owner approval and suggests weights; changes are versioned and can be dry-run against past jobs (compare old vs new picks).

## Degraded modes
`score_mode = full | signals_only | llm_only`. LLM down -> signals_only with banner. No audio signals -> llm_only.

## Evaluation harness (T-5.8)
10 owner-labelled videos with "good clip" ranges. Metrics: precision@k and recall of overlapping picks (IoU >= 0.5), boundary quality score, cost (tokens). Run on every prompt/weight change in admin dry-run and in CI nightly.

## Tests
Windows obey bounds; NMS; adaptive count edge cases (0, 1, 50 candidates); weight redistribution; schema validation of LLM output with malicious transcript fixture; boundary-in-silence property.
