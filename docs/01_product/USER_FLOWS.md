---
id: user-flows
owner: uiux
status: approved-v2
---
# User flows

## Flow A - Link to clips (default)
1. Home: paste URL -> platform icon auto-detected -> options (language, aspect, preset, clip brief, auto mode, max clips) -> Start.
2. Job page: live timeline Fetch -> Prepare -> Transcribe -> Analyze -> Select. ETA per stage.
3. Clips ready (`AWAITING_REVIEW`): grid ordered by Clip Score with thumbnail, score, title, duration, "why this clip".
4. Open a clip -> Clip Editor: preview with live caption overlay, change preset/template, trim, edit words, layout, toggle filler removal.
5. Render (one or all) -> progress -> Download or open Publish Pack.
6. Publish Pack screen: copy title / description / hashtags / transcript, download SRT/VTT/TXT, thumbnail picker.
Auto mode skips step 3-4 using the default preset.

## Flow B - From Google Drive
Library tab -> Drive section (tree view of workspace `01_Sources`) -> pick video -> same as Flow A from step 1 options. Drive files show sync status.

## Flow C - Import a template (admin)
Admin -> Templates -> Import (zip/folder/Drive) -> validation report -> preview on test card in 4 languages -> Save as Draft -> Publish. Published versions are immutable.

## Flow D - Edit a template (admin)
Open template -> Duplicate to draft -> edit via form or JSON tab -> live preview + server test-frame render -> Publish new version. Existing clips keep their pinned version unless the owner clicks "Update to latest".

## Flow E - Blocker handling
Admin -> Inbox lists open blockers with exact steps. Owner does the action, clicks Done -> validation runs -> status RESOLVED or REJECTED with reason.

## Flow F - Retry/failure
Failed job shows stage, error code, human message, "Retry from failed stage", "Download logs". Never a raw traceback in the UI.

## Flow G - Feedback loop
Thumbs up/down per clip; optional "actual views" field after posting. Stored for weight tuning (admin -> Scoring).
