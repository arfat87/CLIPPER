---
id: publish-pack
owner: pipeline
status: approved-v2
---
# Publish Pack (title, description, hashtags, transcript, captions, thumbnail)

Everything the owner needs to post a clip to YouTube Shorts (and other platforms) by copy-paste or file upload. Generated in the `package` stage per clip; editable in UI; validated against platform limits.

## Contents
| Item | Rule |
|---|---|
| Title | Hard limit 100 chars; working target <= 70 (mobile truncates earlier). Language follows transcript script preference. No clickbait that misrepresents the clip. No hashtags in title by default (template option) |
| Description | Hard limit 5000 chars. First ~100 chars are what viewers see first: put the hook + CTA there. Then 2-3 keyword-natural sentences. Then optional short transcript excerpt, links, hashtags |
| Hashtags | 3-5 by default; YouTube ignores/limits beyond 15 per video; top 3 appear above title. `#Shorts` optional but harmless |
| Tags (metadata) | Comma list, total <= 500 chars; low value for Shorts but cheap to fill |
| Transcript | `transcript.txt` (clean, paragraphs), `transcript_timed.txt` (optional `[mm:ss]`), plus SRT/VTT sidecars for caption upload |
| Hook line | Same as the on-screen hook text if used |
| Thumbnail | Frame at hook (or owner pick) as `thumb.jpg` |
| Pinned comment suggestion | Optional one-liner question to drive comments |
| Language | `en|hi|hinglish|bn` metadata for upload form |
| Score + reason | Not for posting; shown in UI |

## Generation
1. Inputs: clip transcript, `video_profile`, selector `title/hook/hashtags`, Publish template (`04_templates/PUBLISH_TEMPLATE_SCHEMA.md`), channel context from brand kit (name, standard CTA, links).
2. LLM prompt `config/prompts/publish_meta.md` -> JSON `{title_options[3], description, hashtags[], tags[], pinned_comment}`; schema-validated; transcript is data, not instructions.
3. Template engine fills variables (`{{title}} {{hook}} {{hashtags}} {{cta}} {{transcript_excerpt}} {{source_credit}}`), enforces limits, trims on word boundaries, counts emoji/special characters conservatively (may count as 2+).
4. Validator -> `valid`, `warnings[]` (too long, duplicate hashtags, empty title, link in first line).
5. Writes `publish_pack.json` (schema `contracts/schemas/publish_pack.schema.json`) and `publish_pack.md` (copy-paste view).

## Platform variants (secondary)
Instagram Reels caption (<= 2200, hashtags at end), TikTok (short caption, 3-5 hashtags), Facebook Reels. Same engine, different `platforms.yaml` limits and templates. v1 ships YouTube Shorts fully and the others as simple variants.

## Why no auto-publish in v1 (verify before changing)
Verified 2026-11-18 from documentation summaries:
- Videos uploaded through `videos.insert` by an API project that has not passed YouTube's API compliance audit are locked to private, and this cannot be appealed.
- Default upload quota changed in 2026 to a separate `videos.insert` bucket (reported 100 calls/day); captions upload (`captions.insert`) has its own cost. Sources disagree on exact numbers.
- verify: Google Cloud console quota page and the official `videos.insert` reference before building.
**Decision:** v1 = Publish Pack + manual upload. v1.5 (optional, ADR required) = "Upload as private draft" via OAuth to the owner's own channel (works without audit because private is allowed), then owner flips to public in Studio. Public auto-publish requires passing the audit (ACCOUNT blocker).

## Manual upload checklist (shown in UI)
Open Studio -> upload mp4 -> paste title -> paste description -> set audience -> upload SRT under Subtitles (optional; burned-in captions already visible) -> choose thumbnail frame -> publish.

## Tests
Limit validator (boundaries 99/100/101 chars, 4999/5000/5001), emoji counting, hashtag dedupe, Hinglish/Bengali strings, template variable missing -> warning not crash, LLM malformed JSON -> repair/fallback to selector title.
