---
id: research-competitors
owner: orchestrator
status: approved-v2
researched: 2026-11-18
---
# Research: how AI clipping sites work and how CLIPPER differs

> Caveat: most "comparison" pages found are written by vendors (OpusClip, Reap, Klap, others) and are marketing. Treat numbers (speed, accuracy, WER) as claims, not facts. Re-verify before basing a decision on them.

## 1. The generic pipeline every clipper runs
1. Ingest (upload or link) -> 2. Speech-to-text with word timestamps -> 3. Segmentation into candidate moments -> 4. Scoring (LLM, sometimes multimodal: audio energy, sentiment, visuals) -> 5. Reframe to vertical with face/object tracking -> 6. Animated captions from templates -> 7. Brand template (logo, fonts, colours) -> 8. Browser editor (trim, caption text) -> 9. Export or auto-post/schedule.
CLIPPER follows the same skeleton, which is why the v1 blueprint was sound. The differences are in scoring inputs, editing model, metering and what is exported.

## 2. Tool-by-tool findings
| Tool | What it is known for | Notable features | Metering / limits seen | Weak spots reported |
|---|---|---|---|---|
| OpusClip | Market leader in pure clipping | "ClipAnything" multimodal moment finding (visual+audio+sentiment, natural-language prompt for what to find); per-clip 0-99/100 "virality score" built from hook/flow/value/trend; auto reframe with moving-subject tracking; animated captions with emoji and keyword highlight; brand templates; filler-word removal; AI B-roll; direct posting/scheduling | Credits = 1 minute of input video; free tier watermarked, clips expire in ~3 days; only 1 brand template on lower plans, more on Pro | Sometimes few usable clips; weaker on gaming/sports; limited caption styles on low tiers; user data may be used to improve models on non-enterprise plans |
| Vizard | Transcript-first workflow, teams | Readable transcript to pick/cut moments, brand kits, scheduling to ~6 platforms, 100+ caption languages, 4K on paid | Credits per input minute; free tier 720p, short storage | Positioned as lighter on moment intelligence |
| Klap | Fast, simple speaking content | TikTok-first reframe, up to 3-hour sources, self-serve API and MCP | Counts clips OUT (100/300/1000 per month), not minutes in | No native scheduling reported by some comparisons |
| Submagic | Caption aesthetics | Premium animated caption effects, clip detection described as weak/absent by several reviewers | Credits per video | Not a true clipper; best on already-trimmed clips |
| Reap | Multilingual + dubbing, developer API | Captions/transcription in many languages, AI dubbing (claims 80+), REST/CLI/MCP, free tier with watermark | Hours of clipping per month | Self-published benchmark; verify independently |
| Munch | Marketing analytics | Trend/analytics around clips; reported to be folded into a broader studio product | Higher price band | Overkill for a solo creator |
| Descript / CapCut | General editors | Script-based editing, AI voice; manual clipping with auto-captions | n/a | Not automated clipping |

## 3. What the leaders do that v1 of our blueprint lacked (now added)
| Gap | Competitor behaviour | CLIPPER v2 answer | Spec |
|---|---|---|---|
| Natural-language clip brief | OpusClip lets you describe the moment you want | `clip_brief` field on job, injected into scoring prompt | 04_HIGHLIGHT_ENGINE |
| Multimodal signals | Visual + audio + sentiment | Optional visual analyzer (frame sampling + vision LLM), always-on audio/scene/face signals | 03_VIDEO_ANALYSIS |
| Genre awareness | Different handling for podcast / gaming / screenshare | Genre profile -> weight set + default layout | 03_VIDEO_ANALYSIS |
| Filler/silence removal | Offered on paid tiers | Edit operation in `clip_plan.json`, captions retimed | 07_RENDER_AND_EDITING |
| Hook text overlay / branding | Brand templates, hook titles | `overlay` template kind + brand kit | 04_templates |
| Multiple brand templates | Gated by plan | Unlimited local templates + presets | 04_templates |
| Posting | Direct posting | Publish Pack in v1; YouTube draft upload optional v1.5 | 08_PUBLISH_PACK |
| Explainable score | Opaque score | Score breakdown + "why this clip" stored and shown | 04_HIGHLIGHT_ENGINE |
| Feedback loop | Little public info | Thumbs + optional manual performance entry feeds weight tuning | 04_HIGHLIGHT_ENGINE |

## 4. Where CLIPPER is intentionally different
- **Pricing model:** no credits, no watermark, no expiry; the limit is hardware.
- **Indic depth:** Hindi/Hinglish/Bengali are first-class (ASR bake-off on the owner's own audio, script normalisation, per-script fonts, HarfBuzz shaping). Mainstream tools list "20+ languages" but Hinglish code-switching and Bengali conjunct rendering are common weak points.
- **Editing model:** one declarative `clip_plan.json` per clip (like an Edit Decision List). Re-rendering, template swaps and trims are deterministic.
- **Ownership of data:** videos stay on the machine; only transcript text (and optionally sampled frames) go to the chosen LLM, and Ollama keeps even that local.
- **Agent-built:** the spec is written so parallel agents can build it with a reviewer gate.

## 5. Things competitors do that we explicitly defer
AI B-roll, AI dubbing/voice, analytics dashboards, multi-user billing, direct posting to every platform (v2). Rationale in FEATURE_SCOPE_AND_DELIVERABLES.md.

## 6. Open questions to validate with real data (do not assume)
1. Which ASR is best for the owner's real audio (bake-off, T-3.1b).
2. Whether the optional visual analyzer improves picks enough to justify cost (A/B on 10 owner videos).
3. Ideal default clip length per genre (owner feedback).

## Sources consulted (paraphrased above)
OpusClip product/review pages and OpusClip blog; Klap alternatives page; Reap "top AI clipping tools" and state-of-clipping report; Vugola comparison posts; Choppity alternatives post; castmagic/thecmo/aixploria reviews; virlo clipping-tools overview. All retrieved 2026-11-18; several are vendor-authored.
