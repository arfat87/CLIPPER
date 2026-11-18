---
id: video-analysis
owner: pipeline
status: approved-v2
---
# Video analysis: how the system understands a video

Goal: turn raw media into a compact, time-indexed set of artefacts that the selector, reframer and editor all read. All files live in `analysis/` and use the single mezzanine timeline.

## A. Video profile (`video_profile.json`)
Purpose: pick genre-specific behaviour.
Inputs: first ~8 min of transcript, 12-20 sampled frames, face stats, shot rate, audio stats.
Output:
```json
{ "schema_version": 1, "genre": "podcast", "genre_conf": 0.82,
  "speakers_estimated": 2, "layout_hint": "two_shot",
  "visual_style": "talking_head", "has_screenshare": false,
  "language": "hinglish", "topics_overview": ["..."], "energy_profile": "medium" }
```
Genres: `podcast, interview, solo_talk, lecture, screenshare_tutorial, gaming, vlog, news, other`. Classification = deterministic features first (face count, shot rate, motion, text-density of frames) then LLM on summary (JSON-only). Owner can override in UI.
Genre selects: weight set in `highlights.yaml`, default clip length, default layout, whether visual analyzer is recommended.

## B. Scenes (`scenes.json`)
PySceneDetect ContentDetector -> `[{start,end,type:cut|fade}]`. Used for boundary avoidance and for resetting reframe smoothing at cuts.

## C. Audio signals (in `signals.json`)
At 1 Hz and per thought unit: RMS loudness z-score, speech rate vs median, pitch variance (optional), silence map (VAD), laughter/applause via YAMNet if enabled else energy-spike heuristic, music-presence flag (to avoid starting clips in intro music).

## D. Faces (`faces.json`)
MediaPipe at 5 fps (adaptive: 2 fps on long videos, refine inside candidate windows). Per frame boxes, landmarks needed for mouth-motion proxy, face size, count. Aggregates: `face_presence_ratio` per window, dominant speaker track ids.

## E. Topics/chapters (`topics.json`)
Embedding or LLM segmentation of transcript into topic spans with titles (also seeds from YouTube chapters if present). Used for diversity (do not pick 5 clips from one topic) and for "context line" in the selector prompt.

## F. Optional visual analysis (`visual.json`, off by default)
Sample 1 frame / 2-3 s inside top candidate windows only (cost control) -> vision LLM returns `{t, description, on_screen_text, visual_interest 0-1, is_screenshare}`. Enabled by genre (`gaming`, `screenshare_tutorial`, `vlog`) or admin toggle. Always cached by frame hash.

## G. Aggregated signals (`signals.json`)
```json
{ "schema_version": 1, "hz": 1,
  "series": { "energy": [..], "speech_rate": [..], "laughter": [..], "scene_density": [..], "face_presence": [..], "heatmap": [..]|null, "music": [..] },
  "silences": [[s,e],...], "thought_units": [{"id":0,"start":..,"end":..,"w0":0,"w1":17}] }
```
All series normalised 0-1 (robust min-max with 5/95 percentiles). Missing signals are `null`, never zero (so weights can be redistributed).

## Quality and cost controls
- Every analyzer is independently skippable and cached by `input_hash`.
- Budget per job in `highlights.yaml`: max vision frames, max LLM tokens.
- Analyzer failures downgrade gracefully (warning, signal `null`), never fail the job unless transcript is missing.

## Tests
Synthetic video fixtures (tone bursts, scripted cuts, face image sequences) with known truths; schema validation; null-handling in weight redistribution.
