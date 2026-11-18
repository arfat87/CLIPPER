---
id: gap-analysis
owner: orchestrator
status: approved-v2
---
# Gap analysis: what changed from the v1 blueprint and why

The original blueprint is preserved at `docs/_original/clipper_blueprint_v1.md`. v2 keeps its architecture and splits it into focused files. Changes:

| # | v1 position | Problem found | v2 change |
|---|---|---|---|
| 1 | Loop state in `.agent/` | Antigravity itself reads `.agent/` (legacy) and `.agents/`; name clash | State moved to `.clipper-state/`; Antigravity config in `.agents/` |
| 2 | Custom external loop runner (`hitl.loop`) as the dispatcher | Antigravity's Agent Manager already dispatches parallel agents | Agent Manager = dispatcher; coordination is file-based (tasks, tickets, blockers). Custom loop becomes optional |
| 3 | Email HITL is Task T-0.2 (first) | Needs credentials before anything works; heavy bootstrap | File/console HITL first (T-0.6); email is optional late module (T-13.1) |
| 4 | "Workflows" for repeatable procedures | Antigravity legacy workflows stopped working on 2026-10-19 | Procedures are Skills (`.agents/skills/*/SKILL.md`) |
| 5 | Whisper large-v3 as default ASR | Public benchmarks suggest it is mediocre on Hindi and weak on Hinglish; stronger/lighter Indic models exist (vendor-reported, unverified) | Pluggable ASR + bake-off task on the owner's audio before locking a default |
| 6 | Auto-posting listed as v2, no detail | YouTube Data API uploads from unaudited projects are forced private; quotas changed in 2026 | Publish Pack in v1; optional private-draft uploader v1.5; audit path documented |
| 7 | Single caption template type | Brand, layout, overlays, publish text all need templating | Six template kinds + presets, admin import/edit, versioning |
| 8 | Clip = start/end + template | No single editable truth; trims/filler cuts/overlays cannot be expressed | `clip_plan.json` (EDL-style) |
| 9 | Signals: audio, scenes, heatmap, faces | No genre awareness; no visual understanding; no user brief | `video_profile`, genre weight sets, optional visual analyzer, `clip_brief` |
| 10 | Virality-style score | Overclaims | "Clip Score" with explainable breakdown + feedback loop |
| 11 | No Google Drive | Owner wants Drive as source of info and structure | Drive integration spec (service-account read-only sync, manifest, tree, routing) |
| 12 | One builder + HITL | Owner wants specialist agents + reviewer + fix loop | 6 roles, ownership map, ticket protocol, round limit |
| 13 | Admin panel absent | Owner needs to import/edit templates and tune settings | Admin panel spec |
| 14 | No prompt-injection thought | Transcripts and Drive docs can contain instructions | Data-not-instructions rule, schema-enforced outputs, no tools for scorer LLM |
| 15 | Safe zone: bottom 18 %, right 12 % | Public specs disagree (bottom UI cover ~14-30 %) | Platform profiles in `config/platforms.yaml`, conservative default 25 % bottom |
| 16 | No docs structure | Owner wants a docs section | `docs/` tree with ownership, frontmatter, ADRs, runbooks, Drive-mirrored docs |
| 17 | MoviePy "optional glue" | Risk of agents using TextClip anyway | Banned in AGENTS.md |
| 18 | Drive/ASR/YouTube facts stated as fixed | Facts change fast | Each volatile fact carries a "verify" note and a task to verify |
