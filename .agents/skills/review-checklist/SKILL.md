---
name: review-checklist
description: Use when acting as the reviewer agent to review a finished task. Runs the scope guard, contract, correctness, security, performance, UX and docs checks, then writes a verdict file and fix tickets.
---
# Review a task

Inputs: task id, branch `agent/<role>/<task-id>`, the task's acceptance criteria.

Run in this order and record evidence for each (command output, screenshot path, file:line):
1. **Scope guard** - does the diff do exactly what the task says? Flag any non-goal, extra feature, or edit outside the role's owned paths. (`docs/01_product/FEATURE_SCOPE_AND_DELIVERABLES.md`)
2. **Contract** - API/schema/DB match `contracts/`; no silent contract change.
3. **Correctness** - run `make check`; run the task's acceptance commands; add one adversarial case (empty input, huge input, Hindi/Bengali text, killed worker).
4. **Security** - secrets, path traversal, shell injection in ffmpeg/yt-dlp args, prompt-injection handling, Drive read-only.
5. **Performance** - compare to budgets in `docs/03_pipeline/09_PERFORMANCE_AND_CACHING.md`.
6. **UX** (UI tasks) - run the app, capture screenshots at 360px and 1280px, check states (loading/empty/error), contrast, touch targets.
7. **Docs** - spec/contract/PROGRESS updated.

Output: `.clipper-state/reviews/REV-<id>.md` with verdict `APPROVE | CHANGES_REQUESTED | BLOCK`, and one fix ticket per finding using skill `write-fix-ticket`. You never edit product code.
