---
id: starter-templates
owner: pipeline
status: approved-v2
---
# Starter templates (shipped in `assets/templates/`)

These exist so the app works before the owner's own assets arrive. Owner templates replace or extend them via the ASSET blocker or admin import. Fonts named here are open-licence families the pipeline agent must fetch/verify (do not assume bundled).

| id | Look | Animation | Best for | Fonts (latn / deva / beng) |
|---|---|---|---|---|
| `clean_white` | White text, thin black outline, no box | pages, fade | Interviews, calm talks | Poppins / Noto Sans Devanagari / Noto Sans Bengali |
| `bold_pop_yellow` | Uppercase white, active word yellow, thick outline | pages + highlight_word + pop | Energetic Shorts, Hinglish | Montserrat ExtraBold / Mukta ExtraBold / Hind Siliguri Bold |
| `karaoke_green` | Line shown, words fill green progressively | karaoke | Storytelling, motivational | Poppins Bold / Mukta Bold / Hind Siliguri Bold |
| `boxed_black` | White text on semi-opaque black box | pages, fade_slide | Noisy backgrounds, readability first | Noto Sans / Noto Sans Devanagari / Noto Sans Bengali |
| `minimal_lowercase` | Lowercase, no outline, soft shadow, centre-low | word_by_word, fade | Calm/aesthetic content | Poppins / Noto Sans Devanagari / Noto Sans Bengali |
| `neon_glow` | Neon cyan/pink with glow shadow | pages + pop | Gaming, tech | Bebas Neue / Mukta ExtraBold / Hind Siliguri Bold |

Also ship (defaults): layouts `fill_default`, `fit_blur_default`; overlays `hook_title`, `progress_bar`; publish `yt_shorts_en`, `yt_shorts_hinglish`, `yt_shorts_hindi`, `yt_shorts_bengali`; presets `quick_default`, `hinglish_podcast_bold`.

JSON for the six caption templates is included in `assets/templates/*/template.json` and must pass `contracts/schemas/caption_template.schema.json`.

## Acceptance for starters (T-6.3)
Each: schema valid, test card without tofu in four languages, contrast warning list empty, preview generated.
