---
id: ingest
owner: pipeline
status: approved-v2
---
# Ingest (fetch + prepare)

## Sources
| Source | Method | Notes |
|---|---|---|
| YouTube video/Shorts/VOD | yt-dlp | Also capture chapters and "most replayed" heatmap when present (free audience signal) |
| Instagram Reels/posts | yt-dlp + cookies | Often login-gated |
| Facebook videos/Reels | yt-dlp + cookies | Same |
| Direct URL | HTTP download with size/duration guard | mp4/mov/webm/mkv |
| Local upload | chunked upload then verify | |
| Google Drive | `drive.pick` copies from mirror or streams `alt=media` | Verify md5 after download |

## Steps
1. **Detect platform** by URL regex -> `youtube|instagram|facebook|direct|drive|upload`. Canonicalise (strip tracking params, extract video id).
2. **Dedupe/cache key**: `platform:video_id` or sha256 of first+last 10 MB + size. If a finished transcript exists for the same key, offer reuse (saves ASR).
3. **Probe**: `yt-dlp -J` (or ffprobe) -> title, duration, uploader, thumbnails, chapters, heatmap, available subtitles (manual subs are a reference only; auto subs are low quality and never trusted).
4. **Guards**: duration <= `MAX_VIDEO_HOURS`; free disk >= 3x estimated size; yt-dlp version check; rights checkbox recorded on job.
5. **Download**: format selector `bv*[height<=1080]+ba/b`, merge to mp4, retry with backoff, `--cookies secrets/cookies/<platform>.txt` if present. Login/403/429 -> classify (`LOGIN_REQUIRED`, `RATE_LIMITED`, `PRIVATE_VIDEO`, `GEO_BLOCKED`). LOGIN_REQUIRED raises `BlockerRequired(CREDENTIAL, COOKIES_<PLATFORM>)`.
6. **Normalise (prepare)**: `mezzanine.mp4` = CFR 30 fps, H.264, yuv420p, rotation applied, max 1080p long side, audio AAC; `audio.wav` = 16 kHz mono PCM. Extract 1 fps thumbnails into `media/thumbs/` for later face/visual steps (cap count for long videos).
7. **Audio probe**: integrated loudness, silence ratio, clipping detection -> warnings (e.g. "very low volume").

## Errors and security
- Arguments passed as list, never through a shell; URLs validated (scheme http/https only; block private IP ranges to avoid SSRF).
- Cookies are secrets: never logged, never attached to tickets.
- Downloaded content is private user data; do not upload anywhere.

## Tests
Platform regex table, canonicalisation, guard logic, ffprobe assertions on normalised output, simulated login-wall -> blocker.
