---
id: layout-overlay-schema
owner: pipeline
status: approved-v2
---
# Layout and overlay templates

## Layout template
```json
{ "schema_version":1, "meta":{"id":"fill_default","name":"Fill (tracked)","version":1},
  "type":"fill",
  "params":{ "headroom_pct":40, "ema_alpha":0.15, "dead_zone_pct":5, "max_pan_px_per_s":420,
             "zoom":{"enabled":true,"max":1.12}, "fallback":"fit_blur", "min_confidence":0.5 } }
```
Types and params:
| type | params |
|---|---|
| `fill` | as above |
| `fit_blur` | `blur_sigma` (30), `bg_zoom` (1.25), `bg_brightness` (-0.1), `fit` (`width|height`), `corner_radius_px` |
| `split_2` (v1.1) | `ratio` (0.5), `gap_px`, `divider_color`, `order` (`speaker_order|left_right`) |
| `screen_focus` (v1.1) | `zoom_to_activity`, `pip_face` {enabled, corner, size_pct} |

## Overlay template
```json
{ "schema_version":1, "meta":{"id":"hook_title","name":"Hook title","version":1},
  "type":"text_block",
  "text":{"source":"hook_line","max_chars":60,"font_role":"title","size_px":64,"color":"#FFFFFF","bg":"#000000CC","padding_px":18,"radius_px":14},
  "position":{"anchor":"top_center","y_percent":14},
  "timing":{"from_s":0,"to_s":2.5,"fade_ms":150} }
```
Overlay types: `text_block` (hook/title/lower third), `progress_bar` (`height_px`, `color`, `position`), `image` (logo/sticker: `asset`, `size_pct`, `opacity`, `anchor`, `timing`), `end_card` (CTA text + duration, appended or overlaid last N seconds).
Rules: overlays respect the safe area; text sources are variables (`hook_line`, `title`, `custom`); max 4 overlays per clip; rendered through ffmpeg overlay/drawbox or ASS events (text) - never browser screenshots.
