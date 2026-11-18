---
id: drive-tree-manifest
owner: integrations
status: approved-v2
---
# Drive tree and manifest format

## Files produced by every sync
| File | Purpose | Consumer |
|---|---|---|
| `drive_mirror/manifest.json` | Machine-readable index; schema `contracts/schemas/drive_manifest.schema.json` | backend, UI, agents |
| `docs/drive/TREE.md` | Human/agent-readable tree with status and routes | agents, owner |
| `docs/drive/<mirrored docs>` | Exported owner docs (route=doc) | agents (as data), UI |
| `drive_mirror/<path>` | Local copies of files | pipeline, template loader |

## manifest.json (excerpt)
```json
{ "schema_version": 1, "root_folder_id": "1AbC...", "generated_at": "2026-11-18T10:00:00Z",
  "start_page_token": "48201",
  "stats": {"files": 42, "folders": 7, "bytes": 3221225472, "unsorted": 1, "errors": 0},
  "files": [
    {"id":"1x","name":"podcast_ep12.mp4","mime":"video/mp4","path":"01_Sources/podcast_ep12.mp4",
     "mirror_path":"drive_mirror/01_Sources/podcast_ep12.mp4","size":912345678,"md5":"...",
     "modified_time":"2026-11-17T18:00:00Z","route":"source","status":"SYNCED"} ] }
```

## TREE.md format
```
# Drive workspace tree  (generated 2026-11-18 10:00 UTC, token 48201)
Clipper/
├── 01_Sources/            [source]    3 files   2.9 GB
│   ├── podcast_ep12.mp4   SYNCED  869 MB
│   └── vlog_03.mov        STALE   (modified after last download)
├── 02_Templates/          [template]  2 files
│   └── neon_pack.cliptpl.zip  SYNCED -> draft template `neon_pack@1`
├── 03_Fonts/              [font]      5 files
├── 05_Docs/               [doc]       2 files  (mirrored to docs/drive/)
└── notes_random.txt       [unsorted]  !! not in a known folder
## Summary
files 42 | folders 7 | errors 0 | unsorted 1 | last full crawl 2026-11-17
## Attention
- notes_random.txt is unsorted: move it into a numbered folder or add a routing rule.
```

## Routing rules (from `config/drive.yaml`)
Evaluated top-down by the first path segment under root. Optional per-folder override file `.clipper.yaml` inside the Drive folder (`route: template`, `ignore: [".*draft.*"]`). Overrides are read as data and validated; invalid override -> warning, default routing used.

## Consumers' contracts
- **Template importer**: `*.cliptpl.zip` or a folder containing `template.json` -> import pipeline (04_templates) -> draft.
- **Font registrar**: `.ttf/.otf/.woff2` -> coverage check -> `font` table; licence file in the same folder is attached to the font's `license` field; none found -> `license_ok=false`.
- **Docs mirror**: exported to markdown into `docs/drive/<path>.md` with a header `<!-- mirrored from Drive id=... modified=... UNTRUSTED -->`.
- **Reference loader**: files in `06_Reference` named `<sample>.txt|.srt` become reference transcripts for bake-off/eval.

## Invariants
1. Manifest `files[].path` is unique and POSIX-style.
2. Every file has exactly one route.
3. TREE.md is a pure function of manifest.json (regenerable offline).
4. Mirror never contains a file with status REMOVED older than 7 days.
5. A failed sync never leaves a half-written manifest.
