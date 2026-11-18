---
id: caption-template-schema
owner: pipeline
status: approved-v2
---
# Caption template schema (v2)

Machine schema: `contracts/schemas/caption_template.schema.json`. This page explains each field.

```json
{
  "schema_version": 2,
  "meta": { "id": "bold_pop_yellow", "name": "Bold Pop - Yellow", "version": 1,
            "author": "starter", "license": "OFL (fonts) / MIT (template)", "tags": ["bold","pop","shorts"] },
  "aspects": ["9:16", "1:1", "4:5"],
  "fonts": {
    "latn": { "family": "Montserrat", "weight": 800, "fallback": ["Noto Sans"] },
    "deva": { "family": "Mukta", "weight": 800, "fallback": ["Noto Sans Devanagari"] },
    "beng": { "family": "Hind Siliguri", "weight": 700, "fallback": ["Noto Sans Bengali"] }
  },
  "text": { "size_px": 78, "case": "upper", "letter_spacing_px": 0, "line_height": 1.15 },
  "colors": { "primary": "#FFFFFF", "active": "#FFE600", "outline": "#000000", "shadow": "#00000099", "box": null },
  "stroke": { "outline_px": 6, "shadow_px": 3, "shadow_offset": [2, 3] },
  "box": { "enabled": false, "padding_px": 16, "radius_px": 12, "opacity": 0.85 },
  "layout": { "anchor": "bottom_center", "y_percent": 68, "align": "center",
              "max_words_per_line": 3, "max_lines": 2,
              "max_chars": { "latn": 22, "deva": 18, "beng": 18 },
              "safe_area_profile": "shorts_default" },
  "animation": { "mode": "pages", "active_word": "highlight", "in": "pop", "out": "none",
                 "duration_ms": 160, "easing": "ease_out" },
  "emphasis": { "keywords_bold": true, "keyword_color": "#00E5FF", "numbers_color": null },
  "emoji": { "enabled": false },
  "speaker_colors": { "enabled": false, "palette": [] }
}
```

## Field reference
| Field | Meaning | Notes |
|---|---|---|
| `meta.id` | snake_case slug equal to folder name | unique |
| `meta.version` | integer, bump on every change | published = immutable |
| `meta.license` | licence text for template + bundled fonts | required; `UNKNOWN` triggers warning |
| `aspects` | supported output aspects | preview greys unsupported |
| `fonts.<script>` | family, weight, fallback list | all three scripts required |
| `text.case` | `none|upper|lower|title` | applies to Latin only |
| `colors.*` | hex `#RRGGBB` or `#RRGGBBAA` | `active` used by highlight/karaoke |
| `stroke.*` | outline and shadow in px at 1080 width | scaled for other sizes |
| `box.*` | opaque/translucent box behind text | rounded corners render in v1.1, square in v1 |
| `layout.anchor` | `bottom_center|center|top_center|lower_third` | y clamped into safe area |
| `layout.max_*` | chunker limits | per-script chars |
| `animation.mode` | `pages|word_by_word|karaoke` | see 06_CAPTIONS_ENGINE |
| `animation.active_word` | `none|highlight|scale|underline` | |
| `animation.in/out` | `none|pop|fade|slide_up|typewriter|bounce` | |
| `emphasis.*` | keyword styling from `emphasis.json` | |
| `emoji.enabled` | v1.1 feature flag | ignored in v1 |
| `speaker_colors` | v1.1 with diarization | ignored in v1 |

## Compatibility rules
- Unknown fields: ignored with warning (forward compatibility); missing required fields: invalid.
- `schema_version` mismatch: loader upgrades v1 -> v2 using documented migration (v1 `size_px` -> `text.size_px`, `outline_px` -> `stroke.outline_px`, `layout.position` -> `layout.anchor`).
- Frontend `captionPreview.ts` maps this JSON to CSS; any property it cannot map must be listed under `preview_limitations` in the UI tooltip.
