---
id: ui-ux-spec
owner: uiux
status: approved-v2
---
# UI/UX spec

Principles: mobile-first (360 px baseline, one-handed), dark default, fast, honest progress, never a dead end (every error has a next action), explain scores.

## Navigation
Bottom tab bar on mobile (Home, Library, Jobs, Templates, Settings), sidebar on desktop; `/admin` separate shell with its own sidebar.

## Screens
| Screen | Content | Key states |
|---|---|---|
| Home / New job | URL field with platform icon, source switch (link / upload / Drive), language, script, aspect, preset, clip brief (optional textarea with examples), auto mode, max clips, rights checkbox, Start | invalid URL, unsupported platform, login hint |
| Library | Recent jobs, Drive sources tree with sync badges, uploads | empty, Drive stale/auth error |
| Job | Vertical timeline Fetch/Prepare/Transcribe/Analyze/Select/Render/Package with %, ETA, warnings; cancel; retry from failed stage; logs download | running, failed (code + human message), cancelled, done |
| Clip grid | Cards sorted by Clip Score: thumbnail, score + breakdown bars, title, duration, "why this clip", feedback thumbs, select-for-render checkboxes, "Render selected" | fewer clips than expected -> explanation + "lower threshold / reselect" |
| Clip Editor | 9:16 player, live caption overlay, preset carousel, timeline with waveform and trim handles, transcript editor (tap word -> edit), toggles (remove fillers, trim silence), layout switch, overlay toggles (hook title, progress bar), safe-area overlay toggle, Render draft / Render final | unsaved changes, render running, render failed, outdated template |
| Publish Pack | Title options (select/edit) with char counter vs 100/70, description editor with 100-char fold marker and counter, hashtags chips (3-5 hint, max 15), tags, transcript (copy/download), SRT/VTT/TXT downloads, thumbnail picker, manual upload checklist, copy buttons everywhere, regenerate | validator warnings inline |
| Settings | Default preset, language, aspect, model size/device, LLM provider, thresholds (basic), storage path, cookies status | |
| Admin | See ADMIN_PANEL_SPEC.md | |

## Interaction details
- Template/preset switch: CSS preview < 100 ms; "Render test frame" for the true libass look.
- SSE reconnect with `Last-Event-ID`; offline banner; optimistic edits with rollback.
- Destructive actions (delete job, archive template) need confirm with consequence text.
- Copy buttons confirm with toast; long text areas support paste-friendly formatting.
- Desktop shortcuts: space play/pause, J/K/L seek, [ ] set trim, Ctrl+Enter render.

## Accessibility and i18n
WCAG AA contrast, visible focus, 44 px targets, reduced-motion respected, captions preview not the only way to read text, all strings in i18n files (English first, Hindi UI planned).

## Performance budgets
Main route < 200 kB gz JS; LCP < 2.5 s on mid phone; no layout shift on progress updates; virtualised lists for > 100 items.

## Error copy rule
Plain language + what to do: "Instagram needs you to log in. Add cookies in Settings, then Retry." Never raw tracebacks.
