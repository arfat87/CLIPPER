from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from backend.app.db import get_engine
from backend.app.models.entities import Clip, Job, JobStatus, StageCheckpoint
from backend.app.services.storage import get_storage
from backend.pipeline.asr.engine import transcribe_job
from backend.pipeline.highlights.ranker import analyze_and_rank_highlights
from backend.pipeline.ingest.downloader import detect_platform, download_and_normalize
from backend.pipeline.render.renderer import render_clip_media


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _record_checkpoint(session: Session, job_id: str, stage: str, artefact_path: str) -> None:
    cp = StageCheckpoint(
        job_id=job_id,
        stage=stage,
        status="DONE",
        artefact_path=artefact_path,
        finished_at=_now_iso(),
    )
    session.add(cp)
    session.commit()


def _update_job(
    session: Session,
    job: Job,
    *,
    status: str,
    stage: str,
    pct: int,
    message: str,
    error: str | None = None,
) -> None:
    job.status = status
    job.stage = stage
    job.progress_pct = pct
    job.message = message
    job.error = error
    job.updated_at = _now_iso()
    session.add(job)
    session.commit()


def run_job_pipeline(job_id: str) -> Job:
    """Execute the end-to-end clipping pipeline with stage checkpoints and automatic resume."""
    storage = get_storage()
    job_dir = storage.job_dir(job_id)

    with Session(get_engine()) as session:
        job = session.get(Job, job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        try:
            # Stage 1: Ingest (Download & Normalize)
            source_mp4 = job_dir / "source" / "source.mp4"
            meta_json = job_dir / "source" / "metadata.json"
            if not (source_mp4.exists() and meta_json.exists()):
                _update_job(
                    session,
                    job,
                    status=JobStatus.DOWNLOADING.value,
                    stage="DOWNLOADING",
                    pct=15,
                    message="Downloading & normalizing source video...",
                )
                meta = download_and_normalize(job.source_url, job_dir)
                job.platform = meta.get("platform", detect_platform(job.source_url))
                job.title = meta.get("title", job.title)
                job.duration = float(meta.get("duration", 0.0))
                session.add(job)
                session.commit()
                _record_checkpoint(session, job.id, "DOWNLOADING", str(source_mp4))
            else:
                meta = json.loads(meta_json.read_text(encoding="utf-8"))

            # Stage 2: Multi-language ASR & Script Normalisation
            transcript_path = job_dir / "asr" / "transcript.json"
            if not transcript_path.exists():
                _update_job(
                    session,
                    job,
                    status=JobStatus.TRANSCRIBING.value,
                    stage="TRANSCRIBING",
                    pct=40,
                    message="Transcribing audio with word-level timestamps...",
                )
                transcript = transcribe_job(
                    job_dir,
                    requested_lang=job.language,
                    script_pref=job.script_pref,
                )
                job.detected_language = transcript.get("language", "en")
                session.add(job)
                session.commit()
                _record_checkpoint(session, job.id, "TRANSCRIBING", str(transcript_path))
            else:
                transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
                job.detected_language = transcript.get("language", "en")

            # Stage 3: Highlight Analysis & Ranking
            existing_clips: list[Clip] = list(
                session.exec(select(Clip).where(Clip.job_id == job.id).order_by(Clip.rank)).all()
            )
            if not existing_clips:
                _update_job(
                    session,
                    job,
                    status=JobStatus.ANALYZING.value,
                    stage="ANALYZING",
                    pct=68,
                    message="Scoring viral moments & snapping clip boundaries...",
                )
                ranked = analyze_and_rank_highlights(
                    job_dir=job_dir,
                    transcript=transcript,
                    max_clips=job.max_clips,
                )
                for item in ranked:
                    c = Clip(
                        job_id=job.id,
                        rank=item["rank"],
                        start=item["start"],
                        end=item["end"],
                        duration=item["duration"],
                        score=item["score"],
                        score_breakdown_json=json.dumps(item["score_breakdown"]),
                        title=item["title"],
                        hook_line=item["hook_line"],
                        hashtags_json=json.dumps(item["hashtags"]),
                        reason=item["reason"],
                        transcript_snippet=item["transcript_snippet"],
                        words_json=json.dumps(item["words"], ensure_ascii=False),
                        layout="fill",
                        template_id=job.template_id,
                        aspect=job.aspect,
                        status="PROPOSED",
                    )
                    session.add(c)
                    existing_clips.append(c)
                session.commit()
                _record_checkpoint(
                    session, job.id, "ANALYZING", str(job_dir / "highlights" / "clips.json")
                )

            # Stage 4: Auto-render top clip (or all clips in auto_mode)
            if job.auto_mode and existing_clips:
                _update_job(
                    session,
                    job,
                    status=JobStatus.RENDERING.value,
                    stage="RENDERING",
                    pct=85,
                    message="Burning styled captions into vertical clips...",
                )
                # Render all proposed clips so they are immediately playable
                for idx, clip in enumerate(existing_clips):
                    if clip.status != "RENDERED" or not (
                        clip.render_mp4_path and Path(clip.render_mp4_path).exists()
                    ):
                        words = json.loads(clip.words_json or "[]")
                        res = render_clip_media(
                            job_dir=job_dir,
                            clip_id=clip.id,
                            start=clip.start,
                            end=clip.end,
                            words=words,
                            template_id=clip.template_id,
                            aspect=clip.aspect,
                            layout=clip.layout,
                        )
                        clip.render_mp4_path = res["mp4_path"]
                        clip.render_ass_path = res["ass_path"]
                        clip.render_srt_path = res["srt_path"]
                        clip.thumb_path = res["thumb_path"]
                        clip.status = "RENDERED"
                        session.add(clip)
                        session.commit()

                _record_checkpoint(session, job.id, "RENDERING", str(job_dir / "renders"))
                _update_job(
                    session,
                    job,
                    status=JobStatus.COMPLETED.value,
                    stage="COMPLETED",
                    pct=100,
                    message=f"Ready — {len(existing_clips)} viral clips extracted & captioned.",
                )
            else:
                _update_job(
                    session,
                    job,
                    status=JobStatus.AWAITING_REVIEW.value,
                    stage="AWAITING_REVIEW",
                    pct=80,
                    message=f"{len(existing_clips)} clips proposed — select caption template to render.",
                )

            session.refresh(job)
            return job

        except Exception as exc:
            _update_job(
                session,
                job,
                status=JobStatus.FAILED.value,
                stage=job.stage,
                pct=job.progress_pct,
                message=f"Failed at {job.stage}",
                error=str(exc),
            )
            session.refresh(job)
            return job
