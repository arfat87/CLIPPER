---
id: multi-agent-model
owner: orchestrator
status: approved-v2
---
# Multi-agent operating model (Antigravity Agent Manager)

## Reality check (design constraint)
Agents running in parallel in Antigravity's Agent Manager are separate conversations. They do **not** message each other. Coordination therefore happens through **files in the repo**: `tasks.yaml`, tickets, reviews, blockers, contracts. The "interaction" a reviewer sends to a builder is a **fix ticket with a ready-to-paste dispatch prompt**; the owner (or the orchestrator agent) starts/continues the builder's conversation with it. This is explicit so nobody assumes magic messaging.

## Roles
| Role | Mission | Parallel? |
|---|---|---|
| orchestrator | Contract freeze, task graph, dispatch, merge gate, status | 1 |
| uiux | Frontend + admin panel + design system | yes |
| backend | API, DB, queue/orchestrator, admin endpoints, settings | yes |
| pipeline | ingest, ASR, analysis, highlights, reframe, captions, render, publish pack, template loader | yes (can be split in 2 conversations: media / intelligence) |
| integrations | Drive sync, HITL service, Docker/deploy, scripts, CI | yes |
| reviewer | Independent quality gate, scope guard, writes fix tickets | continuous |

## Ownership (enforced by reviewer)
See AGENTS.md section 1. Rule: **edit only your paths**. Cross-boundary needs = ticket. Reviewer flags every violation as `SCOPE_VIOLATION`.

## Phases and parallelism
```
Phase 0 (orchestrator + integrations, sequential)
  scaffold -> rules verified -> CONTRACT FREEZE (openapi + schemas + fixtures + mock API)
Phase 1.. (parallel lanes after freeze)
  uiux:         builds against mock API       -> real API when backend ships
  backend:      DB, queue, API, admin         -> uses pipeline stubs via orchestrator interface
  pipeline:     stages against fixtures       -> integrates via Stage protocol
  integrations: Drive sync, HITL, Docker
  reviewer:     reviews each READY_FOR_REVIEW task as it lands
Integration points (orchestrator-led): Contract Freeze (P0), API<->UI swap-over (after B1.4), Pipeline<->Backend wiring (after P-select), E2E (P11)
```

## Git strategy
`main` is always green. Each task on `agent/<role>/<task-id>`; reviewer APPROVE -> orchestrator squash-merges. Use one git worktree per active agent to avoid file clobbering. Conflicts in shared files (Makefile, pyproject) -> integrations owns, others request via ticket.

## Contract-first details
- `contracts/openapi.yaml` generated in T-0.3 from `docs/02_architecture/API_SPEC.md`, then frozen.
- UI generates TS types from it; backend generates pydantic schemas and a contract test; both fail CI on drift.
- JSON artefact schemas in `contracts/schemas/` are shared by pipeline (producer) and backend/UI (consumers).
- Change process: ticket `CONTRACT_CHANGE` -> orchestrator decides -> ADR in `docs/adr/` -> version bump -> affected roles get tickets automatically listed in the ADR.

## Antigravity-specific usage (verify in your version)
- Rules in `.agents/rules/` (frontmatter `trigger`: `always_on | model_decision | glob | manual`); skills in `.agents/skills/<name>/SKILL.md`. Legacy workflows stopped working 2026-10-19, so none are used.
- Do not gitignore `.agents/`.
- Task T-0.2 verifies that rules/skills actually load (ask an agent to list active rules and skills) because glob frontmatter key names can differ by version.
- Use the review/approval policy that asks you before terminal commands that delete, install globally or touch secrets; allow routine test/lint commands.
- Prefer planning-style runs for orchestrator and reviewer; fast runs for well-specified builder tasks.
- If a browser tool is available, reviewer uses it for UI checks and screenshots.

## Owner's daily routine (3 steps)
1. Open `BLOCKERS.md` -> do what each blocker asks -> say DONE in the agent chat.
2. Open `.clipper-state/tickets/` count or ask orchestrator "status" -> dispatch any OPEN tickets using their `dispatch_prompt`.
3. Review the demo/screenshots the orchestrator posts at each milestone and say approve / change.

## Anti-patterns
Two agents editing the same file; builder reviewing its own work; reviewer fixing code; silent contract edits; agents asking the owner questions in chat instead of blockers; building non-goals; marking DONE without `make check`.
