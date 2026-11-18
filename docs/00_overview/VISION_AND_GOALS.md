---
id: vision-goals
owner: orchestrator
status: approved-v2
---
# Vision and Goals

## One-line
Paste a link (YouTube / Instagram / Facebook), upload a file, or pick a video from Google Drive -> get 3-12 ready-to-post vertical clips with styled burned-in captions in English, Hindi, Hinglish or Bengali, plus a complete Publish Pack (title, description, hashtags, transcript, SRT, thumbnail) - running on the owner's own PC, no paid service required.

## Who it is for
Primary: the owner (single user, personal content or content they have rights to). Secondary (v2): a small team via a hosted deployment.

## Success criteria (measurable)
| # | Goal | Target |
|---|---|---|
| G1 | Time to first usable clip, 30-min video, GPU | < 15 min total |
| G2 | Clips the owner keeps without editing | >= 60% of proposed clips |
| G3 | Caption shaping correct for Devanagari + Bengali | 100% on snapshot suite |
| G4 | Word timing error (en/hi) | median <= 120 ms |
| G5 | Adding a new caption template | 0 code changes (drop-in or admin import) |
| G6 | Re-render one clip after edit | < 1.5x clip duration on CPU, faster on GPU |
| G7 | Pipeline resume after crash | 100% of stages resume from last checkpoint |
| G8 | Publish Pack valid for YouTube limits | 100% pass validator (title <= 100, desc <= 5000) |

## Differentiators vs commercial clippers (see RESEARCH_COMPETITOR_ANALYSIS.md)
1. Indic-first: Hindi / Hinglish / Bengali transcription, script choice (Roman vs native) and correct complex-script caption shaping.
2. Local-first, no credits: cost is hardware/electricity, not minutes of video.
3. Template system with admin import/edit and versioning (caption, layout, brand kit, overlay, publish, preset).
4. Single source of truth per clip (`clip_plan.json`): editor, preview and renderer cannot drift apart.
5. Publish Pack as a first-class output, not an afterthought.
6. Google Drive as a native source of videos, templates, fonts, brand kits and docs.
7. Built by a team of coordinated agents with a reviewer gate (see `docs/06_agents/`).

## What we deliberately do NOT promise
- A true "virality prediction". We compute a transparent Clip Score from signals + LLM judgement and let the owner's feedback tune it.
- Direct auto-publishing to YouTube in v1 (see 08_PUBLISH_PACK.md for why).
