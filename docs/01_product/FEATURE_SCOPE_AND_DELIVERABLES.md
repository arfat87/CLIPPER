---
id: feature-scope
owner: orchestrator
status: approved-v2
---
# Feature Architecture: what works, what does not, what is delivered

Reviewer uses this file as the **Scope Guard**: anything built that is not in "IN" is a SCOPE_VIOLATION ticket.

## 1. IN scope (v1)
| ID | Feature | Works like this | Acceptance (short) |
|---|---|---|---|
| F1 | Sources | YouTube (video, Shorts, VOD), Instagram Reels/posts, Facebook videos/Reels, direct URL, local upload, Google Drive file | Each type ingests a sample; login walls raise CREDENTIAL blocker |
| F2 | Transcription | Word-level timestamps; en, hi, hinglish, bn; auto-detect + override; script choice (Roman/native) | WER/CER and timing targets in TESTING doc |
| F3 | Video analysis | Genre profile, scenes, audio signals, faces, topics/chapters, optional visual analysis | `analysis/*.json` valid against schemas |
| F4 | Highlights | 3-12 clips, adaptive count by threshold, explainable score, optional Clip Brief | No overlap > 30 %, boundaries on silence |
| F5 | Reframe | 9:16 / 1:1 / 4:5; face/speaker tracking; fit-blur fallback | No jitter above threshold, resets at cuts |
| F6 | Captions | Template-driven, burned-in via libass, word highlight, Hindi/Bengali shaping, safe areas | Snapshot SSIM >= 0.97 |
| F7 | Editor | Trim, transcript word edit, layout switch, template swap, filler/silence removal toggle, re-render single clip | Edits persist in `clip_plan.json` |
| F8 | Templates | 6 kinds, drop-in folders, admin import/export/edit, versioning, presets | Add template with 0 code changes |
| F9 | Publish Pack | Title, description, hashtags, transcript (txt), SRT/VTT, thumbnail, validator, copy buttons | Passes YouTube limit validator |
| F10 | Export | MP4 H.264/AAC, loudness -14 LUFS, SRT/ASS, ZIP of everything | ffprobe checks |
| F11 | Dashboard | Job history, SSE progress, retry from failed stage, cancel, delete | Chaos test passes |
| F12 | Admin panel | Templates, fonts, brand kits, presets, prompts, weights, ASR/LLM settings, Drive, blockers inbox, health | See ADMIN_PANEL_SPEC |
| F13 | Google Drive | Read-only sync of workspace folder, tree index, ingest from Drive, auto-import templates/fonts/docs | See 05_drive docs |
| F14 | Agent workflow | Roles, tickets, blockers, reviewer gate | See 06_agents |

## 2. OUT of scope (v1) - do not build
Direct auto-publishing to social platforms, scheduling, multi-user billing, B-roll generation, voice dubbing, music library, team collaboration, analytics dashboard, mobile native app, live-stream real-time clipping.
Designed-for-later hooks allowed (interfaces only): `PublisherBackend`, `Diarizer`, `EmojiPicker`.

## 3. How it fails gracefully (what "does not work" looks like)
| Situation | Behaviour |
|---|---|
| IG/FB requires login | CREDENTIAL blocker, job `FAILED(stage=fetch, code=LOGIN_REQUIRED)`, retry after cookies |
| No GPU | Medium int8 model, slower, warning in UI; never silent quality drop without notice |
| LLM unavailable | Heuristic-only ranking, clips marked `score_mode=signals_only` |
| No faces found | `fit_blur` layout |
| Template invalid | Skipped with reason in UI; if zero valid -> ASSET blocker |
| Font missing glyph | Per-script fallback font; if still missing, render warning + tofu check test |
| Drive unreachable | App works from last mirror; Drive page shows stale banner |
| Video > max duration | Rejected with message (default 3 h, configurable) |
| < min candidates | Fewer than 3 clips allowed; UI explains threshold, offers "lower threshold" |

## 4. Deliverables per job (what the owner receives)
```
job_<id>/
  clip_01_<slug>/
    clip_01.mp4            # final vertical video with burned-in captions
    clip_01.srt  clip_01.vtt  clip_01.ass
    thumb.jpg
    transcript.txt         # clean text of the clip
    publish_pack.json      # title, description, hashtags, tags, language, score, reason
    publish_pack.md        # human-readable copy-paste version
    clip_plan.json         # reproducible edit
  full_transcript.json  full_transcript.txt  full_transcript.srt
  job_report.md            # settings, versions, timings, warnings
  all.zip
```

## 5. Quality bars
Captions never overlap platform UI; audio -14 LUFS +-1; first caption visible within 0.3 s of first spoken word; no clip starts mid-word; no clip ends before the payoff sentence.
