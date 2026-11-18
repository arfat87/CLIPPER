---
id: render-editing
owner: pipeline
status: approved-v2
---
# Editing model and rendering

## `clip_plan.json` - the single source of truth for a clip
Schema: `contracts/schemas/clip_plan.schema.json`. The editor edits this file; the renderer is a **pure function** of (plan, source media, template versions). Same plan -> same output (`plan_hash`).
```json
{ "schema_version": 1, "clip_id": "01J...", "source": {"media":"media/mezzanine.mp4"},
  "segments": [ {"start": 125.40, "end": 160.10} ],
  "ops": [ {"op":"remove_fillers","enabled":true}, {"op":"trim_silence","max_gap_s":0.5} ],
  "layout": {"template":"fill_default@latest","force":null},
  "reframe": {"path":"reframe_path.json"},
  "captions": {"template":"bold_pop_yellow@3","script":"latn","overrides":{"words":{}}},
  "overlays": [ {"template":"hook_title@1","text":"{{hook_line}}","t":[0,2.5]},
                {"template":"progress_bar@1"} ],
  "brand_kit": "default@1",
  "audio": {"loudness_lufs":-14,"fade_in_ms":30,"fade_out_ms":80},
  "output": {"aspect":"9:16","profile":"youtube_shorts"} }
```
Multiple `segments` = jump cuts. Preset supplies defaults; owner edits override.

## Editing operations
| Op | Effect | Rules |
|---|---|---|
| trim | change segment start/end | snap to word boundaries; warn if cutting mid-word |
| remove_fillers | cut filler words ("um", "uh", "matlab" etc. per-language list) | min 120 ms context kept, 40 ms audio crossfade |
| trim_silence | shorten gaps > N s | keep natural pause >= 0.25 s |
| layout/reframe | per clip or per span | span switching allowed |
| caption edit | word text edit | timings preserved |
| overlay | hook title, progress bar, lower third, logo | from `overlay` and `brand_kit` templates |
Caption timing after cuts: `t_out = t_src - removed_duration_before(t_src)` (single mapping function used by captions, reframe, overlays - unit-tested).

## Render pipeline (`render/ffmpeg_cmd.py`, `renderer.py`)
1. Resolve plan -> concrete timeline (segments, mapping function).
2. Build reframe filter, caption `.ass`, overlay inputs.
3. Single ffmpeg pass:
```bash
ffmpeg -y -i mezzanine.mp4 [-i logo.png] \
  -filter_complex "[0:v]select/trim+concat per segment,{reframe},scale=1080:1920,{overlays},ass=captions.ass:fontsdir=assets/fonts[v];[0:a]atrim/concat,loudnorm=I=-14:TP=-1.5:LRA=11[a]" \
  -map "[v]" -map "[a]" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 160k -movflags +faststart -progress pipe:1 out.mp4
```
4. Hardware encode `h264_nvenc` auto-detected; CPU fallback; quality parity check in tests.
5. Outputs: `out.mp4`, `thumb.jpg` (frame at hook or owner-picked), `meta.json` (versions, plan_hash, timings).
6. Progress parsed from `-progress pipe:1` -> SSE.
7. Command builder returns an argument list (never a shell string). Golden tests on arg lists.

## Render profiles (`config/render.yaml`)
`youtube_shorts` (1080x1920, crf 20, 30 fps), `reels`, `tiktok`, `square_1x1`, `portrait_4x5`, `draft_fast` (540x960, crf 28, ultrafast, used for editor previews).

## Invalidation
Render is reusable iff `plan_hash` and all template `id@version` match. Template "update to latest" changes the plan -> new hash.

## Tests
Mapping-function property tests, golden arg lists, loudness within -14 +-1 LUFS, duration within 1 frame of plan, A/V sync check with beep-flash fixture, filler removal keeps captions in sync.
