---
id: design-system
owner: uiux
status: approved-v2
---
# Design system

## Tokens (CSS variables, dark default; light theme mirrors)
| Token | Dark | Use |
|---|---|---|
| `--bg` | #0B0D10 | page |
| `--surface` | #141820 | cards |
| `--surface-2` | #1C2230 | raised/inputs |
| `--border` | #2A3242 | dividers |
| `--text` | #E8ECF2 | body (contrast >= 7:1 on bg) |
| `--text-dim` | #9AA6B8 | secondary (>= 4.5:1) |
| `--accent` | #FFD400 | primary actions, highlights |
| `--accent-fg` | #111111 | text on accent |
| `--ok` `--warn` `--err` | #3DDC84 `#FFB020` `#FF5C5C` | status |
Spacing 4-px scale (4,8,12,16,24,32,48). Radius 8/12/16. Type: Inter (UI), Noto Sans Devanagari / Bengali fallbacks; sizes 12/14/16/20/24/32; line-height 1.4-1.6.

## Components (shadcn/ui based)
Button (primary/secondary/ghost/destructive), Input/Textarea with counters, Select, Switch, Tabs, Dialog/Sheet (mobile bottom sheets), Toast, Progress/Timeline, Badge (status, route), ScoreBadge + BreakdownBars, ClipCard, PresetCarousel, CaptionPreview, TrimBar (waveform), TranscriptEditor, TemplateCard, SchemaForm (from JSON Schema), JsonEditor with validation, TreeView (Drive), CopyField, EmptyState, ErrorState, OfflineBanner.

## Caption preview rules (`captionPreview.ts`)
Map template JSON -> CSS: font stack from `fonts.<script>` with fallbacks, `text.size_px` scaled by container width/1080, text-stroke or layered text-shadow for outline, box via padding/background, animation via CSS keyframes (`pop`, `fade`, `slide_up`), active word via span. Overlay safe-area guide from `platforms.yaml`. Unmappable properties (glow accuracy, libass kerning) are listed in a "preview limitations" tooltip; server test-frame is the truth.

## Motion
150-200 ms ease-out; respect reduced motion; no motion on progress bars beyond width.

## Iconography
lucide-react, 20 px default, labelled for screen readers.
