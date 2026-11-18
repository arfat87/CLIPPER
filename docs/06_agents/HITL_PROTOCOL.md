---
id: hitl-protocol
owner: integrations
status: approved-v2
---
# Human-in-the-loop (HITL) protocol: blockers

Agents ask the owner for things **only** through blockers. Blockers never stop the whole project: the blocked task is marked `BLOCKED` and other READY tasks continue.

## 1. Channels (in order of availability)
1. **File channel (always on, built first, T-0.6):** `.clipper-state/blockers/BLK-####.yaml` + auto-generated `BLOCKERS.md` + console banner + admin Inbox.
2. **Antigravity chat:** the agent may post a one-line pointer to the blocker file, but the file is the record.
3. **Email (optional, late module T-13.1):** outbound notices and inbound replies as in the v1 blueprint; needs Gmail app password and IMAP. Not required for anything else.

## 2. Taxonomy
| Type | Examples | Owner action | Validation before resume |
|---|---|---|---|
| `SECRET` | `GEMINI_API_KEY`, `HF_TOKEN`, R2 keys | put value in `.env`, say DONE | live test call |
| `CREDENTIAL` | IG/FB `cookies.txt`, Drive service-account JSON | drop file in `secrets/` | parse + test download/list |
| `ASSET` | templates, fonts, logos, reference transcripts, sample clips | place files in Drive `02/03/04/06` or `assets/inbox/` | schema/font/coverage checks |
| `DECISION` | model choice, auth method, scope question | answer option number or text | recorded in `DECISIONS.md` |
| `ENVIRONMENT` | no libass, CUDA mismatch, disk full | fix machine, say DONE | `/health` passes |
| `ACCOUNT` | Google Cloud project, hosting, domain, payment | do it, give details | specific probe |

## 3. Blocker file template
```yaml
id: BLK-0007
type: SECRET
status: OPEN              # OPEN > AWAITING_VALIDATION > RESOLVED | REJECTED
task_id: T-5.3
step: 2                   # exact resume point
title: "Gemini API key needed for highlight scoring"
needed: [ { key: GEMINI_API_KEY, where: ".env", how_to_get: "https://aistudio.google.com/app/apikey" } ]
why: "LLM semantic scoring. Fallback: local Ollama (slower, ~8 GB RAM)."
alternatives: ["Reply 'USE OLLAMA' to proceed offline."]
unblocked_meanwhile: [T-5.1, T-5.2, T-6.1]
created_at: 2026-11-18T10:00:00Z
reminders_sent: 0
```
Never include the secret value anywhere.

## 4. Lifecycle
1. Agent creates the file (skill `raise-blocker`), sets task BLOCKED, continues elsewhere.
2. Owner acts and replies DONE (chat, admin Inbox button, or `python -m hitl resolve BLK-0007`).
3. Resolver validates (live test / parse / health). Success -> RESOLVED, task READY at `step`. Failure -> REJECTED with a precise reason and the blocker re-OPENs.
4. Reminders: console/admin banner at 24 h and 72 h; after 7 days it only shows in the digest (`BLOCKERS.md` header).

## 5. Rules
- **Secrets are never sent through chat, email or tickets.** If a reply contains something matching a key pattern, the watcher refuses it, redacts the stored copy and tells the owner to rotate it.
- Batch asks: one blocker per need, grouped by what it unblocks; do not ask the same thing twice.
- Always offer the fallback if one exists.
- A DECISION blocker lists 2-3 options with a recommendation and consequences.
- Code paths that need human input raise `BlockerRequired(type, key)`; the orchestrator catches, persists and notifies.

## 6. Checkpoint and resume
Each task in `tasks.yaml` has numbered steps; after each step the agent writes `state.json` and may make a WIP commit on `wip/<task-id>`. Resume = checkout the WIP branch and continue at `step`.

## 7. Optional email module (T-13.1) summary
Outbound: Gmail SMTP app password, subject `[CLIPPER][BLK-0007][ACTION NEEDED] title`. Inbound: IMAP poll 2 min, accept only `From == OWNER_EMAIL` with correlation token and SPF/DKIM pass; parse `DONE`, `USE OLLAMA`, `OPTION n`, `SKIP`, free text; attachments to `.clipper-state/inbox/BLK-####/`. Daily digest and final report optional.
