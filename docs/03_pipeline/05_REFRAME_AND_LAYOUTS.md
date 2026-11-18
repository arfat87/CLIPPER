---
id: reframe-layouts
owner: pipeline
status: approved-v2
---
# Reframe and layouts

Output `clips/<id>/reframe_path.json` and a layout decision stored in `clip_plan.json`.

## Layouts
| Layout | When | Behaviour |
|---|---|---|
| `fill` | one clear face/subject | Crop a 9:16 window following the subject, scale to 1080x1920 |
| `fit_blur` | no faces, wide shots, screenshare, multiple faces far apart | Original scaled to fit width over a blurred, zoomed copy of itself |
| `split_2` (v1.1) | two speakers | Two crops stacked 50/50 with divider |
| `screen_focus` (v1.1) | screenshare | Detect active region (cursor/changes), zoom to it, with optional face PiP |
Layout can be forced by the owner; default from genre profile and face stats.

## Tracking algorithm (`fill`)
1. Sample at 5 fps inside the clip (2x denser around speaker changes).
2. Face boxes via MediaPipe; fallback OpenCV DNN if confidence low.
3. **Active speaker**: score = face area x mouth-motion proxy (landmark delta) x continuity; hysteresis: switch subject only if the new one dominates >= 1.0 s.
4. **Headroom**: place face centre at ~38-42 % of crop height; keep eyes above the caption zone.
5. **Smoothing**: EMA alpha ~0.15, max pan speed clamp, dead-zone 5 % of width; smoothing **resets at scene cuts** (jump, never pan across a cut).
6. **Zoom**: optional gentle zoom 100-112 % on emphasis moments (template/preset flag), clamp so crop never leaves source frame.
7. **Safety**: if tracking confidence < threshold for > 1.5 s -> blend to centre crop or fall back to `fit_blur` for that span (span-level layout switch allowed).

## `reframe_path.json`
```json
{ "schema_version":1, "fps":30, "source_w":1920, "source_h":1080, "out_aspect":"9:16",
  "keyframes":[{"t":0.0,"cx":0.52,"cy":0.45,"zoom":1.0,"layout":"fill"}],
  "cuts":[12.4, 31.9] }
```
## FFmpeg application
Preferred: `crop` with per-frame x/y from `sendcmd` file (keyframes at 10 Hz, linear interpolation pre-expanded to the output fps). Fallback for very long clips: split into segments at keyframe groups and concat. Pure function in `ffmpeg_cmd.py`; golden-string tests.

## Output sizes
9:16 1080x1920, 1:1 1080x1080, 4:5 1080x1350. Upscaling warning if source height < 720.

## Tests
Synthetic moving-face video -> path within tolerance; jitter metric below threshold; no pan across cuts; fallback triggers when no faces; crop stays inside frame (property test).
