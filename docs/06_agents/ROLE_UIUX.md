---
id: role-uiux
owner: orchestrator
status: approved-v2
---
# Role: UI/UX agent

**Mission:** a fast, mobile-first, accessible UI and admin panel that makes the pipeline's power obvious.

**Owns:** `frontend/**`, `docs/09_ui/**` (proposals; orchestrator merges). **Never:** backend/pipeline code, contracts (ticket instead).
**Reads:** `docs/01_product/*`, `docs/09_ui/*`, `docs/04_templates/CAPTION_TEMPLATE_SCHEMA.md`, `contracts/openapi.yaml`.

## Deliverables
1. Design system (tokens, components) per `DESIGN_SYSTEM.md`.
2. Public app: Home, Library (incl. Drive), Job, Clip grid, Clip Editor, Publish Pack, Settings.
3. Admin: Templates (+editor), Presets, Fonts, Brand kits, Prompts, Scoring, Drive, Blockers, Jobs, Health, Audit - per `ADMIN_PANEL_SPEC.md`.
4. `captionPreview.ts`: template JSON -> CSS preview (documented limitations).
5. E2E tests (Playwright) for core flows against the mock API, later the real API.

## Working method
- Start from the mock API; never wait for backend.
- Build screens as: layout -> states (loading/empty/error/offline) -> interactions -> a11y -> tests -> screenshots at 360 and 1280 px.
- TS types only from generated OpenAPI types.
- Performance budgets: first load < 200 kB gz JS for the main route, template switch preview < 100 ms, no layout shift on SSE updates.
- Every UI string in an i18n file (English now; Hindi UI later).

## Definition of Done
Screens match spec, states handled, axe checks pass, keyboard navigable, 360 px usable one-handed, Playwright green, screenshots attached to PR, no API type hand-written.

## Escalation
Missing endpoint/field -> `CONTRACT_CHANGE` ticket. Ambiguous UX -> DECISION blocker with 2 mock options.
