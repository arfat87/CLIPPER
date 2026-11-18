---
id: glossary
owner: orchestrator
status: approved-v2
---
# Glossary
| Term | Meaning |
|---|---|
| Job | One source video processed end to end |
| Stage | One pipeline step (fetch, prepare, transcribe, analyze, select, render, package) with a checkpoint |
| Artifact | A file a stage produces; stage output is valid only if its input hash matches |
| Thought unit | A sentence/utterance unit used as the atom for clip boundaries |
| Candidate | A possible clip window before scoring |
| Clip Score | 0-1 fused score (LLM + signals); not a virality prediction |
| Clip Brief | Optional natural-language instruction from the owner ("find funny moments about cricket") |
| Genre profile | Detected video type (podcast, interview, solo, lecture, screenshare, gaming, other) selecting weights and layout defaults |
| `clip_plan.json` | Declarative edit description for one clip (EDL-style); renderer is a pure function of it |
| Template kind | caption, layout, brand_kit, overlay, publish, preset |
| Preset | A bundle of one template per kind, selectable per job |
| Publish Pack | Title, description, hashtags, transcript, SRT/VTT, thumbnail for one clip |
| Blocker (BLK) | A request for something only the owner can provide |
| Ticket (FIX) | A review finding or contract change assigned to a role |
| HITL | Human in the loop (the owner) |
| Mirror | Local copy of the owner's Drive workspace folder (`drive_mirror/`) |
| Tree | Generated index of the Drive workspace (`docs/drive/TREE.md`, `manifest.json`) |
| EDL | Edit Decision List: ordered source ranges plus operations |
| Safe area | Region of the frame not covered by platform UI |
| Script (writing) | latn, deva, beng - used to pick fonts and chunk rules |
