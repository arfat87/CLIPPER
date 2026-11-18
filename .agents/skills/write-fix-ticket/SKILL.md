---
name: write-fix-ticket
description: Use to hand a defect, a missing piece, or a contract change to the owning agent. Creates a ticket file with evidence, acceptance criteria and a ready-to-paste dispatch prompt.
---
# Write a fix ticket

Create `.clipper-state/tickets/FIX-<4 digits>.yaml`:

```yaml
id: FIX-0042
kind: DEFECT            # DEFECT | MISSING | CONTRACT_CHANGE | SCOPE_VIOLATION | NIT
severity: S2            # S1 blocks merge, S2 must fix, S3 should fix, S4 optional
task_id: T-5.5
target_role: pipeline   # who must fix it
round: 1                # increments on each failed recheck, max 3
status: OPEN            # OPEN > IN_PROGRESS > READY_FOR_RECHECK > VERIFIED | WONTFIX | ESCALATED
files: [backend/pipeline/captions/ass_builder.py]
finding: "Bengali conjunct split across two caption pages."
evidence: ".clipper-state/reviews/evidence/REV-0007/bn_split.png"
expected: "Chunker never splits inside a word; page break only between words."
acceptance: "Golden test bn_conjunct_01 passes; snapshot SSIM >= 0.97"
dispatch_prompt: |
  You are the pipeline agent. Read AGENTS.md and docs/06_agents/ROLE_PIPELINE.md.
  Fix ticket FIX-0042 only. Add the regression test named in `acceptance`.
  Do not refactor unrelated code. Set status READY_FOR_RECHECK when done.
```
Rules: one finding per ticket; evidence is mandatory; acceptance must be machine-checkable where possible; never include secrets. After round 3 set `ESCALATED` and raise a DECISION blocker.
