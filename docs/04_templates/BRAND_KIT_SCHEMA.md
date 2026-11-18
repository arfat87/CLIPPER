---
id: brandkit-schema
owner: pipeline
status: approved-v2
---
# Brand kit

```json
{ "schema_version":1, "meta":{"id":"my_channel","name":"My Channel","version":1,"license":"owner"},
  "palette":{"primary":"#FFE600","secondary":"#111111","accent":"#00E5FF"},
  "fonts":{"title":"Montserrat","body":"Noto Sans"},
  "logo":{"asset":"logo.png","anchor":"top_right","size_pct":12,"opacity":0.9,"margin_pct":4,"show":"always"},
  "watermark":{"asset":"wm.png","anchor":"bottom_left","size_pct":8,"opacity":0.5},
  "intro":{"asset":"intro.mp4","max_s":2.0,"enabled":false},
  "outro":{"asset":"outro.mp4","max_s":3.0,"enabled":false},
  "end_card":{"text":"Follow for more","duration_s":2.0,"enabled":false},
  "defaults":{"preset":"hinglish_podcast_bold@2","publish_template":"yt_shorts_hinglish@1"},
  "channel":{"name":"My Channel","handle":"@mychannel","links":[],"standard_cta":"Subscribe for more"} }
```
Rules
- Assets live beside the JSON (relative paths only; no absolute paths, no URLs).
- Intro/outro are concatenated after normalising to the output profile (resolution, fps, audio rate) and loudness-matched. Total clip length cap respects platform max duration.
- Logo safe-area aware (never under the platform UI).
- `channel.*` feeds `{{cta}}`, `{{channel_name}}` in Publish templates.
- Multiple brand kits allowed (unlimited locally); one marked default.
