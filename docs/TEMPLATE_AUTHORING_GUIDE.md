# CLIPPER — Template Authoring Guide

CLIPPER uses a declarative **Template Pack** format (`template.json` + preview assets + optional fonts/overlays) stored under `assets/templates/` (v1 compat) and `assets/templates_v2/<template_id>/` (Spec Pack v2) or imported from Google Drive (`CLIPPER/01_Templates/`).

All template packs are validated against [`contracts/schemas/template_meta.schema.json`](../contracts/schemas/template_meta.schema.json).

---

## 1. Template Kinds

Spec Pack v2 supports 6 template kinds (`kind` field in `template.json`):

| Kind | Purpose | Key Section in `template.json` |
|---|---|---|
| `caption` | Word-level animated ASS subtitles burned in via FFmpeg + `libass` | `caption: { font, colors, stroke, shadow, background_box, layout, animation }` |
| `layout` | Smart 9:16 / 1:1 / 4:5 reframing layout (`single_subject`, `split_2up`, `pip_screen`, `blurred_bg`) | `layout: { mode, aspect, slots, divider, pip }` |
| `publish` | Prompt & formatting presets for multi-platform titles, descriptions, and hashtags | `publish: { tone, platforms, hashtag_count, cta_style }` |
| `brand_kit` | Brand colors, watermark logo, lower-third, and intro/outro stingers | `brand_kit: { primary_color, secondary_color, watermark, fonts }` |
| `overlay` | Reusable progress bars, hook banners, and stickers | `overlay: { type, position, style }` |
| `preset` | Bundled combination of a `caption`, `layout`, `publish`, and `brand_kit` template | `preset: { caption_template, layout_template, publish_template }` |

---

## 2. Directory Layout

```text
assets/templates_v2/
  hormozi_yellow/
    template.json       # Validated against contracts/schemas/template_meta.schema.json
    preview.svg         # Visual preview card displayed in Studio & Admin
  bangla_bold/
    template.json
    preview.svg
  hindi_creator/
    template.json
    preview.svg
```

---

## 3. Authoring a Multi-Script Caption Template (`template.json`)

Every caption template MUST define `font_fallbacks` for Latin (`latn`), Devanagari (`deva`), and Bengali (`beng`) so mixed-script Hinglish and Bangla+English lines render without tofu (`□`) glyphs:

```json
{
  "id": "hindi_creator",
  "version": 1,
  "kind": "caption",
  "name": "Hindi Creator Bold",
  "description": "High-contrast Devanagari + Latin karaoke captions with safe-area clamping.",
  "author": "CLIPPER Studio",
  "status": "active",
  "aspect_ratios": ["9:16", "1:1", "4:5", "16:9"],
  "languages": ["hi", "hinglish", "en"],
  "scripts": ["deva", "latn"],
  "tags": ["hindi", "hinglish", "karaoke", "bold"],
  "caption": {
    "font_family": "Noto Sans Devanagari",
    "font_weight": 800,
    "font_size_px": 64,
    "font_fallbacks": {
      "latn": "Montserrat",
      "deva": "Noto Sans Devanagari",
      "beng": "Noto Sans Bengali"
    },
    "primary_color": "#FFFFFF",
    "highlight_color": "#FFD600",
    "secondary_color": "#00E5FF",
    "outline_color": "#000000",
    "outline_px": 5,
    "shadow_color": "#000000CC",
    "shadow_offset_px": 3,
    "background_box": {
      "enabled": true,
      "color": "#00000099",
      "padding_px": 14,
      "radius_px": 10
    },
    "alignment": "bottom_center",
    "margin_v_px": 280,
    "margin_h_px": 80,
    "max_words_per_line": 4,
    "max_lines": 2,
    "max_chars_per_line": 26,
    "uppercase": false,
    "animation": {
      "style": "karaoke_fill",
      "duration_ms": 140
    }
  }
}
```

> **Important for Indic Scripts (`deva`, `beng`)**:
> - Always set `"uppercase": false` when targeting Hindi or Bengali, and allow at least `1.25x` line spacing so top matras (शिरोरेखा / मात्रा) and bottom vowel signs are never clipped.
> - Keep `max_chars_per_line` between `22` and `28` for 9:16 vertical videos.

---

## 4. Supported Animation Styles (`caption.animation.style`)

1. `karaoke_fill` — Progressive syllable/word fill using ASS `{\kf<cs>}` karaoke tags.
2. `highlight_word` — Active word switches to `highlight_color` (`{\c&H...&}`) while surrounding chunk words stay in `primary_color`.
3. `pop` — Active word scales up `115%` and settles back via `{\t(0,80,\fscx115\fscy115)\t(80,160,\fscx100\fscy100)}`.
4. `typewriter` — Words appear progressively as they are spoken within the chunk window.
5. `fade_slide` — Chunk fades in (`{\fad(120,120)}`) with a subtle upward slide (`{\move(...)}`).
6. `bounce` — Vertical elastic bounce (`{\t(0,90,\fscy120)\t(90,180,\fscy100)}`).

---

## 5. Testing & Validating Your Template

1. **Schema Validation & Tofu Glyph Detection**:
   ```bash
   .venv/bin/pytest backend/tests/pipeline/test_templates_and_ass_v2.py -v
   ```
2. **Render a Test Card**:
   Use `render_template_test_card(template_id, sample_text, script)` in `backend/pipeline/captions/template_loader.py` or preview it live in the **Admin -> Templates** view.
3. **Importing from Google Drive**:
   Drop your template folder or `.json` into `CLIPPER/01_Templates/` on Google Drive and click **Sync Now** in **Admin -> Drive Workspace**. Imported templates start in `status: "draft"` until validated and activated.
