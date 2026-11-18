---
id: day0-checklist
owner: orchestrator
status: approved-v2
---
# Day-0 owner checklist (do before starting agents)

## A. Machine facts (write into `DECISIONS.md`)
GPU model + VRAM (or "CPU only"), RAM, free disk (>= 50 GB recommended), OS.

## B. Install
Git, Python 3.11, `uv`, Node + `pnpm`, FFmpeg **full build with libass/harfbuzz/fribidi** (Windows: gyan.dev full; Linux: BtbN/johnvansickle static), optionally Ollama and CUDA drivers.

## C. Antigravity
Open the repo folder; confirm `.agents/` is present and **not gitignored**; set the approval policy so destructive/global/secret-touching commands ask you; follow `docs/06_agents/LAUNCH_PROMPTS.md`.

## D. Google Drive workspace
1. Create folder `Clipper` with subfolders `01_Sources 02_Templates 03_Fonts 04_BrandKits 05_Docs 06_Reference 07_Exports`.
2. Google Cloud console -> new project -> enable **Google Drive API** -> IAM -> Service Accounts -> create -> Keys -> JSON. Save as `secrets/drive_sa.json`.
3. Share the `Clipper` folder with the service-account email (looks like `name@project.iam.gserviceaccount.com`) as **Viewer**.
4. Put the folder id (the long string in the folder URL) in `.env` as `DRIVE_ROOT_FOLDER_ID`, set `DRIVE_ENABLED=true`.
5. Say DONE on the CREDENTIAL blocker; the agent validates by listing the folder.

## E. Content for quality
- 5-10 real clips (60-120 s; mix en/hi/hinglish/bn) + correct transcripts -> `06_Reference` (for ASR bake-off).
- 10 videos with time ranges of clips you consider "good" -> `06_Reference/good_clips.csv` (for highlight evaluation).
- Your caption templates/fonts (any format; agents convert) -> `02_Templates`, `03_Fonts` with licence info.
- Logo/intro/outro -> `04_BrandKits`.

## F. Keys
LLM: Gemini API key (`.env`) **or** accept local Ollama. Instagram/Facebook cookies only when prompted.

## G. Optional
Gmail app password for email HITL (T-13.1). YouTube Cloud project for private-draft upload (T-13.2).
