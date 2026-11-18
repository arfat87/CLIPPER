---
trigger: glob
globs: frontend/**
description: Frontend rules
---
- React 18 + Vite + TS strict + Tailwind + shadcn/ui + TanStack Query + Zustand.
- API types are generated from `contracts/openapi.yaml`; never hand-write API types.
- Mobile-first (360px baseline), dark theme default, WCAG AA contrast, 44px touch targets.
- Every screen implements loading, empty, error and offline states.
- Caption preview is driven by the same template JSON as the server; server render is the source of truth.
