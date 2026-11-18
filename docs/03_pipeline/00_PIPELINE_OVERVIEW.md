---
id: pipeline-overview
owner: pipeline
status: approved-v2
---
# Pipeline overview

| # | Stage | Input | Output artefacts | Spec |
|---|---|---|---|---|
| 1 | fetch | URL / upload / Drive id | `source/original.*`, `source/metadata.json` | 01_INGEST |
| 2 | prepare | original | `media/mezzanine.mp4`, `media/audio.wav`, `media/thumbs/` | 01_INGEST |
| 3 | transcribe | audio.wav | `asr/transcript.json` | 02_TRANSCRIPTION |
| 4 | analyze | mezzanine, transcript | `analysis/*.json` | 03_VIDEO_ANALYSIS |
| 5 | select | transcript, analysis | `highlights/clips.json`, `clips/<id>/clip_plan.json` | 04_HIGHLIGHT_ENGINE |
| 6 | render (per clip) | clip_plan, templates | `captions.ass`, `reframe_path.json`, `out.mp4` | 05, 06, 07 |
| 7 | package (per clip) | clip, transcript, templates | `publish_pack.json/.md`, srt/vtt/txt, thumb | 08_PUBLISH_PACK |

## Stage contract (every stage implements)
```python
class Stage(Protocol):
    name: str
    def input_hash(self, ctx: JobContext) -> str: ...
    def run(self, ctx: JobContext, progress: ProgressFn) -> StageResult: ...   # idempotent
```
`StageResult = { artefacts: list[Path], metrics: dict, warnings: list[str] }`. The orchestrator owns checkpoints, retries, cancellation and temp-dir atomicity; stages never touch the DB directly.

## Cross-cutting
- Time unit: seconds (float, 3 decimals). All media timestamps refer to `mezzanine.mp4` time (the single timeline).
- Language/script tags: `en|hi|hinglish|bn` and `latn|deva|beng`.
- Warnings are accumulated into `job_report.md` and surfaced in UI.
- Every JSON artefact has `schema_version` and `produced_by: {stage, version, config_hash}`.
- Fixture mode: `PIPELINE_FIXTURE=path` makes a stage read a canned upstream artefact so each stage is developable alone.
