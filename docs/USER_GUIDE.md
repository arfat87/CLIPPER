# CLIPPER — User Guide (Studio & Admin Console)

Welcome to **CLIPPER**, a local-first AI Video Clipping & Multi-language Captioning web application supporting **YouTube**, **Instagram**, **Facebook**, **Google Drive**, and **local file uploads** across **English (`en`)**, **Hindi (`hi`)**, **Hinglish (`hinglish`)**, and **Bengali / Bangla (`bn`)**.

---

## 1. Quick Start (`make dev`)

CLIPPER runs completely on a single machine with zero paid cloud dependencies required.

```bash
# 1. One-time bootstrap (creates .venv, installs Python + frontend deps, checks FFmpeg)
make setup

# 2. Start the backend API + worker + frontend dev server
make dev
```

- **Web Studio & Admin UI**: `http://localhost:5173` (or `http://localhost:8000` when served via FastAPI / Docker)
- **OpenAPI Docs**: `http://localhost:8000/docs`
- **Health Endpoint**: `http://localhost:8000/api/v1/health`

---

## 2. Studio Workflow

### Step 1: Ingest a Video (`HomeView`)
1. Open the **Studio** tab.
2. Choose your video source:
   - **Paste URL**: Paste a YouTube (`youtube.com/watch?v=...`, `youtu.be/...`), Instagram Reel/Video, or Facebook Video URL. The platform badge is detected automatically.
   - **Local Upload**: Drag-and-drop or browse for `.mp4`, `.mov`, `.mkv`, or `.webm` files (chunked resumable upload with progress bar).
   - **Google Drive Picker**: Select a media file directly from your mirrored `CLIPPER/02_Sources` Google Drive folder.
3. Configure job options:
   - **Language**: `Auto-detect`, `English`, `Hindi (हिन्दी)`, `Hinglish`, or `Bengali (বাংলা)`.
   - **Clip Brief** *(optional)*: Natural-language prompt (e.g., *"Find moments about system design or funny anecdotes"*).
   - **Aspect Ratio & Templates**: Default `9:16` vertical (`1:1`, `4:5`, `16:9` also supported), caption template (`hormozi_yellow`, `bangla_bold`, `hindi_creator`, etc.), and reframe layout (`single_subject`, `split_2up`, `pip_screen`, `blurred_bg`).
   - **Rights Confirmation**: Check the rights confirmation box before submitting.

### Step 2: Live Pipeline Progress (`JobTimelineView`)
Once submitted, CLIPPER streams real-time Server-Sent Events (`/api/v1/jobs/{id}/events`) across the 8 pipeline stages:
1. `INGEST` (`yt-dlp` / upload validation + cookie management)
2. `PREPARE` (16 kHz mono WAV extraction, 360p proxy video, waveform peaks, EBU R128 loudness)
3. `ASR` (`faster-whisper` transcription + `WhisperX` word-level forced alignment + mixed-script tagging `latn`/`deva`/`beng`)
4. `ANALYZE` (PySceneDetect / FFmpeg scene cuts, audio energy/laughter/pause signals, face presence & active speaker tracking, LLM topic segmentation)
5. `HIGHLIGHTS` (Thought-unit candidate windowing, LLM Map-Reduce 6-dimension virality scoring + multi-modal signal fusion, NMS, semantic dedupe, boundary snapping)
6. `REFRAME` (1-Euro smoothed crop path, scene-cut snap resets, multi-speaker split/PiP layout engine)
7. `RENDER` (`pysubs2` `.ass` caption generation with inline script font fallback + FFmpeg `libass` burn-in + `-16 LUFS` loudness normalization)
8. `PUBLISH` (Multi-platform titles, descriptions, hashtags, pinned comment, and `publish_pack.md`)

### Step 3: Review Ranked Highlights (`ClipGridView`)
- Filter clips by **Minimum Virality Score**, **Duration Range**, **Topic**, or **Score Mode** (`full`, `signals_only`, `llm_only`).
- Inspect the **Virality Score Breakdown** (`Hook`, `Payoff`, `Standalone`, `Emotion`, `Shareability`, `Quotability`, plus audio/visual signal contributions).
- If a video has fewer high-scoring moments than requested, CLIPPER displays a transparent **`fewer_clips_reason`** banner rather than padding with low-quality clips.

### Step 4: Non-Linear Clip Editor (`ClipEditorView`)
Click **Edit Clip** on any highlight card to open the NLE Clip Editor:
- **9:16 Canvas & Safe-Zone Overlay**: Toggle TikTok / Reels / Shorts UI safe-zone guides (`top 12%`, `bottom 22%`, `right 14%`).
- **Word-Level Transcript Editor**: Click any word to seek, double-click to correct spelling across Latin, Devanagari, or Bengali scripts, or toggle **Remove Filler Words** / **Trim Silence**.
- **Caption Styling**: Switch templates (`hormozi_yellow`, `clean_minimal`, `bangla_bold`, `hindi_creator`, `neon_pop`) and animation modes (`karaoke_fill`, `highlight_word`, `pop`, `typewriter`, `fade_slide`, `bounce`).
- **Layout Mode**: Switch between `single_subject`, `split_2up`, `pip_screen`, and `blurred_bg`.
- **Keyboard Shortcuts**:
  - `Space`: Play / Pause
  - `J` / `K` / `L`: Shuttle reverse / pause / forward
  - `I` / `O`: Set clip In / Out trim points
  - `[` / `]`: Nudge boundary by 100ms

### Step 5: Publish Pack & Export (`PublishPackView`)
- View and edit platform-tailored copy for **YouTube Shorts**, **Instagram Reels**, **Facebook Reels**, and **TikTok**:
  - 3 title variants (`Curiosity`, `Contrarian`, `List / How-To`)
  - Description + Chapter timestamps
  - Platform-safe hashtags (Latin + native Devanagari/Bengali tags)
  - Pinned comment CTA & thumbnail hook text
- Click **Download All (.zip)** (`/api/v1/jobs/{id}/download.zip`) to download all rendered `.mp4` clips, `.ass`/`.srt`/`.vtt` subtitles, `publish_pack.json`, and `publish_pack.md`.

---

## 3. Admin Console

Switch to the **Admin** mode in the top navigation bar to manage:
1. **Templates (`AdminTemplatesView`)**: Live ASS/Canvas preview of all caption & layout templates across English, Hindi, Hinglish, and Bengali sample strings; clone, validate against JSON Schema, and activate/archive templates.
2. **Assets & Fonts (`AdminAssetsViews`)**: Inspect registered fonts, script coverage (`latn`, `deva`, `beng`), tofu glyph detection test cards, overlays, and watermarks.
3. **Scoring Weights (`AdminScoringView`)**: Tune signal fusion weights and genre presets (`podcast`, `interview`, `lecture`, `gaming`, `vlog`) with auto-redistribution preview.
4. **Prompts (`AdminPromptsView`)**: Versioned prompt editor for Map-Reduce highlight scoring, topic segmentation, and publish-pack generation (enforcing the mandatory `<<<UNTRUSTED_TRANSCRIPT>>>` security sentinel).
5. **Google Drive Workspace (`AdminDriveView`)**: Monitor incremental sync status for `CLIPPER/`, inspect the mirrored file tree, trigger manual sync, and view routed templates/fonts/docs/sources.
6. **HITL Blockers (`AdminBlockersView`)**: View open and resolved Human-in-the-Loop blockers (`SECRET`, `CREDENTIAL`, `ASSET`, `DECISION`, `ENV`) and resolve them from the web UI or CLI (`python -m hitl.cli resolve BLK-XXXX`).
7. **Operations & Maintenance (`AdminOpsViews`)**: Check subsystem health (`ffmpeg`, `gpu`, `models`, `drive`, `queue`), disk usage quotas, structured logs, and trigger cache cleanup.
