from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.app.config import get_settings
from backend.app.db import get_session, init_db
from backend.app.models.entities import Clip, Job, JobStatus, StageCheckpoint
from backend.app.services.storage import get_storage
from backend.pipeline.captions.template_loader import (
    ensure_starter_templates,
    validate_template_dict,
)
from backend.pipeline.ingest.downloader import detect_platform
from backend.pipeline.orchestrator import run_job_pipeline
from backend.pipeline.render.renderer import render_clip_media
from backend.pipeline.sample_generator import ensure_sample_media, verify_toolchain
from hitl.cli import run_loop_status
from hitl.models import BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker, list_blockers, resolve_blocker

app = FastAPI(
    title="CLIPPER — AI Video Clipping & Multi-language Captioning API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    ensure_starter_templates()
    ensure_sample_media()


class CreateJobRequest(BaseModel):
    url: str
    language: str = "auto"        # auto | en | hi | hinglish | bn
    script_pref: str = "auto"     # auto | latn | deva | beng
    aspect: str = "9:16"          # 9:16 | 1:1 | 4:5
    auto_mode: bool = True
    template_id: str = "bold_pop_yellow"
    max_clips: int = 12
    run_sync: bool = False


class UpdateClipRequest(BaseModel):
    start: float | None = None
    end: float | None = None
    title: str | None = None
    layout: str | None = None
    template_id: str | None = None
    aspect: str | None = None
    words: list[dict[str, Any]] | None = None


class RenderClipRequest(BaseModel):
    template_id: str | None = None
    aspect: str | None = None
    layout: str | None = None


class RaiseBlockerRequest(BaseModel):
    type: str = "ASSET"
    task_id: str = "T-5.7"
    title: str
    key: str
    where: str = "assets/templates/"
    how_to_get: str = "Provide caption template JSON or credentials"
    why: str = "Requested via Web UI"


def _serialize_clip(c: Clip) -> dict[str, Any]:
    return {
        "id": c.id,
        "job_id": c.job_id,
        "rank": c.rank,
        "start": c.start,
        "end": c.end,
        "duration": c.duration,
        "score": c.score,
        "score_breakdown": json.loads(c.score_breakdown_json or "{}"),
        "title": c.title,
        "hook_line": c.hook_line,
        "hashtags": json.loads(c.hashtags_json or "[]"),
        "reason": c.reason,
        "transcript_snippet": c.transcript_snippet,
        "words": json.loads(c.words_json or "[]"),
        "layout": c.layout,
        "template_id": c.template_id,
        "aspect": c.aspect,
        "status": c.status,
        "video_url": f"/api/v1/clips/{c.id}/video.mp4" if c.render_mp4_path else None,
        "ass_url": f"/api/v1/clips/{c.id}/subtitles.ass" if c.render_ass_path else None,
        "srt_url": f"/api/v1/clips/{c.id}/subtitles.srt" if c.render_srt_path else None,
        "thumb_url": f"/api/v1/clips/{c.id}/thumb.jpg" if c.thumb_path else None,
    }


def _serialize_job(session: Session, job: Job) -> dict[str, Any]:
    clips = list(
        session.exec(select(Clip).where(Clip.job_id == job.id).order_by(Clip.rank)).all()
    )
    checkpoints = list(
        session.exec(select(StageCheckpoint).where(StageCheckpoint.job_id == job.id)).all()
    )
    return {
        "id": job.id,
        "source_url": job.source_url,
        "platform": job.platform,
        "title": job.title,
        "duration": job.duration,
        "language": job.language,
        "detected_language": job.detected_language,
        "script_pref": job.script_pref,
        "aspect": job.aspect,
        "auto_mode": job.auto_mode,
        "template_id": job.template_id,
        "max_clips": job.max_clips,
        "status": job.status,
        "stage": job.stage,
        "progress_pct": job.progress_pct,
        "message": job.message,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "clips": [_serialize_clip(c) for c in clips],
        "checkpoints": [
            {"stage": cp.stage, "status": cp.status, "finished_at": cp.finished_at}
            for cp in checkpoints
        ],
    }


@app.get("/api/v1/health")
def health_check() -> dict[str, Any]:
    tc = verify_toolchain()
    settings = get_settings()
    return {
        "status": "ok" if tc.get("ffmpeg_available") and tc.get("libass_enabled") else "degraded",
        "toolchain": tc,
        "llm_provider": settings.llm_provider,
        "gemini_configured": bool(settings.gemini_api_key.strip()),
        "smtp_configured": bool(settings.notify_smtp_user and settings.notify_smtp_app_password),
    }


@app.get("/api/v1/samples")
def list_samples() -> list[dict[str, Any]]:
    return ensure_sample_media()


@app.get("/api/v1/templates")
def list_templates() -> list[dict[str, Any]]:
    return ensure_starter_templates()


@app.post("/api/v1/templates")
def create_custom_template(template_data: dict[str, Any]) -> dict[str, Any]:
    valid, err = validate_template_dict(template_data)
    if not valid:
        raise HTTPException(status_code=400, detail=f"Invalid template schema: {err}")
    settings = get_settings()
    tid = template_data["id"]
    folder = settings.assets_dir / "templates" / tid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "template.json").write_text(json.dumps(template_data, indent=2), encoding="utf-8")
    template_data["valid"] = True
    return template_data


@app.post("/api/v1/uploads")
async def upload_local_video(file: UploadFile = File(...)) -> dict[str, Any]:
    settings = get_settings()
    uploads_dir = settings.data_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or "uploaded.mp4").name
    dest = uploads_dir / safe_name
    content = await file.read()
    dest.write_bytes(content)
    return {"upload_path": str(dest), "filename": safe_name}


@app.post("/api/v1/jobs")
def create_job(
    req: CreateJobRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    job = Job(
        source_url=req.url.strip(),
        platform=detect_platform(req.url),
        language=req.language,
        script_pref=req.script_pref,
        aspect=req.aspect,
        auto_mode=req.auto_mode,
        template_id=req.template_id,
        max_clips=req.max_clips,
        status=JobStatus.QUEUED.value,
        stage="QUEUED",
        progress_pct=5,
        message="Job queued",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    if req.run_sync or job.platform == "sample":
        job = run_job_pipeline(job.id)
    else:
        background_tasks.add_task(run_job_pipeline, job.id)

    return _serialize_job(session, job)


@app.get("/api/v1/jobs")
def list_jobs(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    jobs = list(session.exec(select(Job).order_by(Job.created_at.desc())).all())
    return [_serialize_job(session, j) for j in jobs]


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(session, job)


@app.post("/api/v1/jobs/{job_id}/retry")
def retry_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job = run_job_pipeline(job.id)
    return _serialize_job(session, job)


@app.delete("/api/v1/jobs/{job_id}")
def delete_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clips = list(session.exec(select(Clip).where(Clip.job_id == job_id)).all())
    for c in clips:
        session.delete(c)
    cps = list(session.exec(select(StageCheckpoint).where(StageCheckpoint.job_id == job_id)).all())
    for cp in cps:
        session.delete(cp)
    session.delete(job)
    session.commit()
    get_storage().delete_job(job_id)
    return {"deleted": job_id}


@app.get("/api/v1/jobs/{job_id}/transcript")
def get_job_transcript(job_id: str) -> dict[str, Any]:
    job_dir = get_storage().job_dir(job_id)
    tp = job_dir / "asr" / "transcript.json"
    if not tp.exists():
        raise HTTPException(status_code=404, detail="Transcript not ready yet")
    return json.loads(tp.read_text(encoding="utf-8"))


@app.get("/api/v1/jobs/{job_id}/events")
async def job_events_sse(job_id: str) -> StreamingResponse:
    async def event_generator():
        for _ in range(60):
            with next(get_session()) as session:
                job = session.get(Job, job_id)
                if not job:
                    yield f"data: {json.dumps({'error': 'not_found'})}\n\n"
                    break
                payload = {
                    "id": job.id,
                    "status": job.status,
                    "stage": job.stage,
                    "pct": job.progress_pct,
                    "message": job.message,
                }
                yield f"data: {json.dumps(payload)}\n\n"
                if job.status in (
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                    JobStatus.AWAITING_REVIEW.value,
                ):
                    break
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.patch("/api/v1/clips/{clip_id}")
def update_clip(
    clip_id: str,
    req: UpdateClipRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if req.start is not None:
        clip.start = round(float(req.start), 2)
    if req.end is not None:
        clip.end = round(float(req.end), 2)
    clip.duration = round(max(1.0, clip.end - clip.start), 2)
    if req.title is not None:
        clip.title = req.title
    if req.layout is not None:
        clip.layout = req.layout
    if req.template_id is not None:
        clip.template_id = req.template_id
    if req.aspect is not None:
        clip.aspect = req.aspect
    if req.words is not None:
        clip.words_json = json.dumps(req.words, ensure_ascii=False)
        clip.transcript_snippet = " ".join(w.get("w", "") for w in req.words)

    session.add(clip)
    session.commit()
    session.refresh(clip)
    return _serialize_clip(clip)


@app.post("/api/v1/clips/{clip_id}/render")
def render_single_clip(
    clip_id: str,
    req: RenderClipRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    if req.template_id:
        clip.template_id = req.template_id
    if req.aspect:
        clip.aspect = req.aspect
    if req.layout:
        clip.layout = req.layout

    job_dir = get_storage().job_dir(clip.job_id)
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
    session.refresh(clip)
    return _serialize_clip(clip)


@app.get("/api/v1/clips/{clip_id}/video.mp4")
def serve_clip_video(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.render_mp4_path or not Path(clip.render_mp4_path).exists():
        raise HTTPException(status_code=404, detail="Rendered video not found")
    return FileResponse(clip.render_mp4_path, media_type="video/mp4")


@app.get("/api/v1/clips/{clip_id}/subtitles.ass")
def serve_clip_ass(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.render_ass_path or not Path(clip.render_ass_path).exists():
        raise HTTPException(status_code=404, detail="ASS file not found")
    return FileResponse(clip.render_ass_path, media_type="text/plain; charset=utf-8")


@app.get("/api/v1/clips/{clip_id}/subtitles.srt")
def serve_clip_srt(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.render_srt_path or not Path(clip.render_srt_path).exists():
        raise HTTPException(status_code=404, detail="SRT file not found")
    return FileResponse(clip.render_srt_path, media_type="text/plain; charset=utf-8")


@app.get("/api/v1/clips/{clip_id}/thumb.jpg")
def serve_clip_thumb(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.thumb_path or not Path(clip.thumb_path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(clip.thumb_path, media_type="image/jpeg")


@app.get("/api/v1/jobs/{job_id}/download.zip")
def download_job_zip(job_id: str, session: Session = Depends(get_session)) -> StreamingResponse:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clips = list(session.exec(select(Clip).where(Clip.job_id == job_id).order_by(Clip.rank)).all())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in clips:
            prefix = f"clip_{c.rank:02d}_{c.id}"
            if c.render_mp4_path and Path(c.render_mp4_path).exists():
                zf.write(c.render_mp4_path, f"{prefix}.mp4")
            if c.render_srt_path and Path(c.render_srt_path).exists():
                zf.write(c.render_srt_path, f"{prefix}.srt")
            if c.render_ass_path and Path(c.render_ass_path).exists():
                zf.write(c.render_ass_path, f"{prefix}.ass")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="clipper_{job_id}.zip"'},
    )


# --- Human-in-the-Loop (HITL) Protocol Endpoints ---
@app.get("/api/v1/hitl/status")
def get_hitl_status() -> dict[str, Any]:
    st = run_loop_status()
    all_b = [b.model_dump(mode="json") for b in list_blockers()]
    st["blockers"] = all_b
    return st


@app.post("/api/v1/hitl/blockers")
def api_raise_blocker(req: RaiseBlockerRequest) -> dict[str, Any]:
    b = create_or_get_blocker(
        btype=BlockerType(req.type),
        task_id=req.task_id,
        title=req.title,
        needed=[NeededItem(key=req.key, where=req.where, how_to_get=req.how_to_get)],
        why=req.why,
    )
    return b.model_dump(mode="json")


@app.post("/api/v1/hitl/blockers/{blocker_id}/resolve")
def api_resolve_blocker(blocker_id: str, note: str = "Resolved via Web UI") -> dict[str, Any]:
    b = resolve_blocker(blocker_id, note=note)
    return b.model_dump(mode="json")


@app.get("/", response_class=HTMLResponse)
def serve_web_ui() -> HTMLResponse:
    ui_file = Path(__file__).resolve().parents[2] / "frontend" / "index.html"
    if ui_file.exists():
        return HTMLResponse(ui_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>CLIPPER API is running.</h1>")
