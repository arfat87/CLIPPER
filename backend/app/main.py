from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from backend.app.config import get_settings
from backend.app.db import get_engine, get_session, init_db
from backend.app.models.entities import (
    Clip,
    FeedbackEvent,
    Job,
    JobStatus,
    PublishPack,
    Render,
    StageCheckpoint,
    Transcript,
)
from backend.app.routes_admin import router as admin_router
from backend.app.routes_drive_health import router as drive_health_router
from backend.app.routes_publish import router as publish_router
from backend.app.services.admin import AdminError
from backend.app.services.orchestrator import (
    STAGES,
    cancel_job,
    clear_job_events,
    get_job_events,
    mark_stale_from,
    record_job_event,
    retry_job,
    wait_for_job_events,
)
from backend.app.services.queue import enqueue_job
from backend.app.services.storage import get_storage
from backend.app.services.uploads import (
    UploadError,
    complete_upload_session,
    init_upload_session,
    resolve_upload_path,
    store_upload_chunk,
)
from backend.pipeline.asr.engine import detect_word_script
from backend.pipeline.captions.template_loader import (
    ensure_starter_templates,
    validate_template_dict,
)
from backend.pipeline.ingest.downloader import detect_platform
from backend.pipeline.orchestrator import run_job_pipeline
from backend.pipeline.render.renderer import render_clip_media
from backend.pipeline.sample_generator import ensure_sample_media
from hitl.cli import run_loop_status
from hitl.models import BlockerType, NeededItem
from hitl.raise_blocker import create_or_get_blocker, list_blockers, resolve_blocker

ACTIVE_JOB_STATUSES = {
    JobStatus.FETCHING.value,
    JobStatus.PREPARING.value,
    JobStatus.DOWNLOADING.value,
    JobStatus.TRANSCRIBING.value,
    JobStatus.ANALYZING.value,
    JobStatus.SELECTING.value,
    JobStatus.RENDERING.value,
    JobStatus.PACKAGING.value,
}

TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.AWAITING_REVIEW.value,
}

VALID_JOB_STATUSES = {s.value for s in JobStatus}

CLIP_PLAN_KEYS = {
    "schema_version",
    "clip_id",
    "source",
    "segments",
    "ops",
    "layout",
    "reframe",
    "captions",
    "overlays",
    "brand_kit",
    "audio",
    "output",
}

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


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable
        super().__init__(message)


@app.exception_handler(ApiError)
async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "retryable": exc.retryable,
            }
        },
    )


@app.exception_handler(UploadError)
async def _handle_upload_error(_request: Request, exc: UploadError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": {},
                "retryable": False,
            }
        },
    )


@app.exception_handler(AdminError)
async def _handle_admin_error(_request: Request, exc: AdminError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "retryable": exc.retryable,
            }
        },
    )


app.include_router(admin_router)
app.include_router(drive_health_router)
app.include_router(publish_router)


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": exc.errors()},
                "retryable": False,
            },
            "detail": exc.errors(),
        },
    )


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    ensure_starter_templates()
    ensure_sample_media()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _json_merge_patch(target: Any, patch: Any) -> Any:
    """Apply RFC-7396 JSON Merge Patch (`null` deletes keys)."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    if not isinstance(target, dict):
        target = {}
    result = copy.deepcopy(target)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = _json_merge_patch(result.get(key), value)
    return result


class JobSourceInput(BaseModel):
    kind: str = "url"
    url: str | None = None
    upload_id: str | None = None
    drive_file_id: str | None = None


class CreateJobRequest(BaseModel):
    source: JobSourceInput | None = None
    source_kind: str | None = None
    source_url: str | None = None
    url: str | None = None
    source_ref: str | None = None
    language: str = "auto"        # auto | en | hi | hinglish | bn
    script_pref: str = "auto"     # auto | latn | deva | beng | native
    aspect: str = "9:16"          # 9:16 | 1:1 | 4:5
    auto_mode: bool = True
    template_id: str = "bold_pop_yellow"
    preset_id: str | None = None
    max_clips: int = Field(default=12, ge=1, le=50)
    clip_brief: str | None = None
    rights_confirmed: bool | None = None
    run_sync: bool | None = None


class RetryJobRequest(BaseModel):
    from_stage: str | None = None


class TranscriptWordEdit(BaseModel):
    idx: int = Field(..., ge=0)
    text: str


class PatchTranscriptRequest(BaseModel):
    edits: list[TranscriptWordEdit]


class ReselectClipsRequest(BaseModel):
    clip_brief: str | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_clips: int | None = Field(default=None, ge=1, le=20)


class RenderAllRequest(BaseModel):
    preset_id: str | None = None


class RenderClipRequest(BaseModel):
    template_id: str | None = None
    aspect: str | None = None
    layout: str | None = None
    preset_id: str | None = None
    profile: str | None = None


class ClipFeedbackRequest(BaseModel):
    value: str
    actual_views: int | None = Field(default=None, ge=0)


class RaiseBlockerRequest(BaseModel):
    type: str = "ASSET"
    task_id: str = "T-5.7"
    title: str
    key: str
    where: str = "assets/templates/"
    how_to_get: str = "Provide caption template JSON or credentials"
    why: str = "Requested via Web UI"


def _default_clip_plan(c: Clip, job: Job | None = None) -> dict[str, Any]:
    job_dir = get_storage().job_dir(c.job_id)
    source_media = str(job_dir / "source" / "source.mp4")
    start_val = round(float(c.start_s if c.start_s is not None else c.start), 2)
    end_val = round(float(c.end_s if c.end_s is not None else c.end), 2)
    script_val = (job.script if job and job.script in ("latn", "deva", "beng") else None) or "latn"
    tpl_base = (c.template_id or "bold_pop_yellow").split("@")[0]
    layout_base = (c.layout or "fill").split("@")[0]
    return {
        "schema_version": 1,
        "clip_id": c.id,
        "source": {"media": source_media},
        "segments": [{"start": start_val, "end": end_val}],
        "ops": [],
        "layout": {
            "template": f"{layout_base}@latest",
            "force": (
                layout_base
                if layout_base in ("fill", "fit_blur", "split_2", "screen_focus")
                else None
            ),
        },
        "captions": {
            "template": f"{tpl_base}@latest",
            "script": script_val,
            "overrides": {},
        },
        "overlays": [],
        "brand_kit": None,
        "audio": {"loudness_lufs": -14, "fade_in_ms": 0, "fade_out_ms": 0},
        "output": {
            "aspect": c.aspect or "9:16",
            "profile": "1080p_fast",
        },
    }


def _load_or_build_clip_plan(c: Clip, job: Job | None = None) -> dict[str, Any]:
    if c.plan_path:
        p = Path(c.plan_path)
        if p.exists():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except (OSError, ValueError):
                pass
    default_path = get_storage().job_dir(c.job_id) / "clips" / c.id / "clip_plan.json"
    if default_path.exists():
        try:
            loaded = json.loads(default_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
        except (OSError, ValueError):
            pass
    return _default_clip_plan(c, job)


def _compute_plan_hash(c: Clip, plan: dict[str, Any], profile: str) -> str:
    payload = {
        "clip_id": c.id,
        "plan": plan,
        "words": json.loads(c.words_json or "[]"),
        "profile": profile,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _serialize_clip(c: Clip) -> dict[str, Any]:
    start_val = c.start_s if c.start_s is not None else c.start
    end_val = c.end_s if c.end_s is not None else c.end
    return {
        "id": c.id,
        "job_id": c.job_id,
        "rank": c.rank,
        "start": c.start,
        "end": c.end,
        "start_s": start_val,
        "end_s": end_val,
        "duration": c.duration,
        "score": c.score,
        "score_mode": c.score_mode,
        "score_breakdown": json.loads(c.score_breakdown_json or "{}"),
        "title": c.title,
        "hook_line": c.hook_line,
        "hashtags": json.loads(c.hashtags_json or "[]"),
        "reason": c.reason,
        "transcript_snippet": c.transcript_snippet,
        "words": json.loads(c.words_json or "[]"),
        "layout": c.layout,
        "template_id": c.template_id,
        "preset_id": c.preset_id,
        "aspect": c.aspect,
        "status": c.status,
        "feedback": c.feedback,
        "actual_views": c.actual_views,
        "video_url": f"/api/v1/clips/{c.id}/video.mp4" if c.render_mp4_path else None,
        "ass_url": f"/api/v1/clips/{c.id}/subtitles.ass" if c.render_ass_path else None,
        "srt_url": f"/api/v1/clips/{c.id}/subtitles.srt" if c.render_srt_path else None,
        "thumb_url": f"/api/v1/clips/{c.id}/thumb.jpg" if c.thumb_path else None,
        "created_at": c.created_at,
    }


def _serialize_clip_detail(c: Clip, job: Job | None = None) -> dict[str, Any]:
    data = _serialize_clip(c)
    data["clip_plan"] = _load_or_build_clip_plan(c, job)
    return data


def _serialize_render(r: Render) -> dict[str, Any]:
    return {
        "id": r.id,
        "clip_id": r.clip_id,
        "plan_hash": r.plan_hash,
        "aspect": r.aspect,
        "profile": r.profile,
        "status": r.status,
        "progress_pct": r.progress_pct,
        "duration_s": r.duration_s,
        "file_url": f"/api/v1/renders/{r.id}/file" if r.path_mp4 else None,
        "thumb_url": f"/api/v1/clips/{r.clip_id}/thumb.jpg" if r.path_thumb else None,
        "video_url": f"/api/v1/clips/{r.clip_id}/video.mp4" if r.path_mp4 else None,
        "ass_url": f"/api/v1/clips/{r.clip_id}/subtitles.ass" if r.path_ass else None,
        "srt_url": f"/api/v1/clips/{r.clip_id}/subtitles.srt" if r.path_srt else None,
        "created_at": r.created_at,
    }


def _serialize_job(session: Session, job: Job) -> dict[str, Any]:
    clips = list(
        session.exec(select(Clip).where(Clip.job_id == job.id).order_by(col(Clip.rank))).all()
    )
    checkpoints = list(
        session.exec(
            select(StageCheckpoint)
            .where(StageCheckpoint.job_id == job.id)
            .order_by(col(StageCheckpoint.id))
        ).all()
    )
    job_dir = get_storage().job_dir(job.id)
    events = get_job_events(job.id, after_id=0, job_dir=job_dir)
    warnings = [
        str(ev.data.get("message", ""))
        for ev in events
        if ev.event == "warning" and ev.data.get("message")
    ]
    duration_s = job.duration_s if job.duration_s is not None else job.duration
    stages = [
        {
            "stage": cp.stage,
            "status": cp.status,
            "attempt": cp.attempt,
            "started_at": cp.started_at,
            "finished_at": cp.finished_at,
            "metrics": cp.metrics_json or {},
        }
        for cp in checkpoints
    ]
    return {
        "id": job.id,
        "source_kind": job.source_kind,
        "source_url": job.source_url,
        "source_ref": job.source_ref,
        "platform": job.platform,
        "title": job.title,
        "duration": job.duration,
        "duration_s": duration_s,
        "language": job.language,
        "detected_language": job.detected_language,
        "script_pref": job.script_pref,
        "script": job.script,
        "genre": job.genre,
        "aspect": job.aspect,
        "auto_mode": job.auto_mode,
        "template_id": job.template_id,
        "preset_id": job.preset_id,
        "max_clips": job.max_clips,
        "clip_brief": job.clip_brief,
        "status": job.status,
        "stage": job.stage,
        "progress_pct": job.progress_pct,
        "message": job.message,
        "error": job.error,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "warnings": warnings,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "clips": [_serialize_clip(c) for c in clips],
        "stages": stages,
        "checkpoints": [
            {"stage": cp.stage, "status": cp.status, "finished_at": cp.finished_at}
            for cp in checkpoints
        ],
    }


def _get_active_job_or_404(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if not job or job.deleted_at is not None:
        raise ApiError(404, "VALIDATION_ERROR", f"Job {job_id} not found")
    return job


def _encode_cursor(job: Job) -> str:
    raw = json.dumps({"created_at": job.created_at, "id": job.id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        obj = json.loads(raw)
        created_at = str(obj["created_at"])
        job_id = str(obj["id"])
        if not created_at or not job_id:
            raise ValueError("empty cursor field")
        return created_at, job_id
    except Exception as exc:
        raise ApiError(400, "VALIDATION_ERROR", "Invalid pagination cursor") from exc


def _parse_event_id(raw_value: str | None) -> int:
    if raw_value is None:
        return 0
    raw = raw_value.strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _find_idempotent_job(session: Session, key: str) -> Job | None:
    jobs = list(
        session.exec(
            select(Job)
            .where(col(Job.deleted_at).is_(None))
            .order_by(col(Job.created_at).desc(), col(Job.id).desc())
        ).all()
    )
    for j in jobs:
        if (j.settings_json or {}).get("idempotency_key") == key:
            return j
    return None


@app.get("/api/v1/samples")
def list_samples() -> list[dict[str, Any]]:
    return ensure_sample_media()


@app.post("/api/v1/templates")
def create_custom_template(template_data: dict[str, Any]) -> dict[str, Any]:
    valid, err = validate_template_dict(template_data)
    if not valid:
        raise ApiError(400, "TEMPLATE_INVALID", f"Invalid template schema: {err}")
    settings = get_settings()
    tid = template_data["id"]
    folder = settings.assets_dir / "templates" / tid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "template.json").write_text(json.dumps(template_data, indent=2), encoding="utf-8")
    template_data["valid"] = True
    return template_data


@app.post("/api/v1/uploads")
async def upload_endpoint(request: Request) -> JSONResponse:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as exc:
            raise ApiError(400, "VALIDATION_ERROR", "Invalid JSON payload") from exc
        if not isinstance(body, dict):
            raise ApiError(422, "VALIDATION_ERROR", "Expected JSON object")
        filename = body.get("filename")
        size_bytes = body.get("size_bytes")
        if not isinstance(filename, str) or not filename.strip():
            raise ApiError(422, "VALIDATION_ERROR", "filename is required")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 1:
            raise ApiError(422, "VALIDATION_ERROR", "size_bytes must be a positive integer")
        session_info = init_upload_session(
            filename=filename,
            size_bytes=size_bytes,
            mime_type=body.get("mime_type"),
            expected_sha256=body.get("sha256"),
        )
        return JSONResponse(status_code=201, content=session_info)

    # Legacy multipart/form-data upload compatibility
    form = await request.form()
    file_obj = form.get("file")
    if file_obj is None or not hasattr(file_obj, "read"):
        raise ApiError(422, "VALIDATION_ERROR", "Missing multipart 'file' field")
    settings = get_settings()
    uploads_dir = settings.data_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    raw_name = getattr(file_obj, "filename", None) or "uploaded.mp4"
    safe_name = Path(raw_name).name
    dest = uploads_dir / safe_name
    content = await file_obj.read()
    dest.write_bytes(content)
    return JSONResponse(status_code=200, content={"upload_path": str(dest), "filename": safe_name})


@app.put("/api/v1/uploads/{upload_id}/chunk")
async def upload_chunk(
    upload_id: str,
    request: Request,
    n: int = Query(..., ge=0),
) -> dict[str, Any]:
    data = await request.body()
    return store_upload_chunk(upload_id, n, data)


@app.post("/api/v1/uploads/{upload_id}/complete")
async def complete_upload(upload_id: str, request: Request) -> dict[str, Any]:
    expected_sha256: str | None = None
    raw_body = await request.body()
    if raw_body.strip():
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise ApiError(400, "VALIDATION_ERROR", "Invalid JSON payload") from exc
        if isinstance(body, dict) and body.get("sha256") is not None:
            expected_sha256 = str(body["sha256"])
    return complete_upload_session(upload_id, expected_sha256=expected_sha256)


@app.post("/api/v1/jobs")
def create_job(
    req: CreateJobRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> JSONResponse:
    if req.rights_confirmed is False:
        raise ApiError(422, "VALIDATION_ERROR", "rights_confirmed must be true")

    idem_key = (idempotency_key or "").strip() or None
    if idem_key:
        existing = _find_idempotent_job(session, idem_key)
        if existing is not None:
            stored_status = (existing.settings_json or {}).get("idempotency_status")
            replay_status = (
                int(stored_status)
                if stored_status in (200, 201)
                else (200 if existing.status == JobStatus.COMPLETED.value else 201)
            )
            return JSONResponse(
                status_code=replay_status,
                content=_serialize_job(session, existing),
            )

    if req.source is not None:
        source_kind = req.source.kind
        raw_url = (req.source.url or req.source_url or req.url or "").strip()
        source_ref = req.source.upload_id or req.source.drive_file_id or req.source_ref
    else:
        source_kind = req.source_kind or "url"
        raw_url = (req.source_url or req.url or "").strip()
        source_ref = req.source_ref

    if source_kind not in ("url", "upload", "drive"):
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            f"Unsupported source_kind {source_kind!r}; expected url, upload, or drive",
        )

    if source_kind == "upload":
        ref_candidate = (source_ref or raw_url).strip()
        if not ref_candidate:
            raise ApiError(422, "VALIDATION_ERROR", "Upload job requires source_ref or upload_id")
        resolved = resolve_upload_path(ref_candidate)
        if resolved is not None:
            raw_url = str(resolved)
            source_ref = source_ref or ref_candidate
        elif not raw_url:
            raw_url = ref_candidate
        platform = "upload"
    elif source_kind == "drive":
        ref_candidate = (source_ref or raw_url).strip()
        if not ref_candidate:
            raise ApiError(422, "VALIDATION_ERROR", "Drive job requires source_ref or drive_file_id")
        raw_url = ref_candidate if "://" in ref_candidate else f"drive://{ref_candidate}"
        source_ref = source_ref or ref_candidate
        platform = "drive"
    else:
        if not raw_url:
            raise ApiError(422, "VALIDATION_ERROR", "Job requires url or source_url")
        platform = detect_platform(raw_url)

    is_sync = bool(req.run_sync is True or (platform == "sample" and req.run_sync is not False))
    status_code = 200 if is_sync else 201

    settings_snapshot: dict[str, Any] = {
        "source_kind": source_kind,
        "language": req.language,
        "script_pref": req.script_pref,
        "aspect": req.aspect,
        "auto_mode": req.auto_mode,
        "template_id": req.template_id,
        "preset_id": req.preset_id,
        "max_clips": req.max_clips,
        "clip_brief": req.clip_brief,
        "rights_confirmed": True if req.rights_confirmed is None else req.rights_confirmed,
    }
    if idem_key:
        settings_snapshot["idempotency_key"] = idem_key
        settings_snapshot["idempotency_status"] = status_code

    job = Job(
        source_kind=source_kind,
        source_url=raw_url,
        source_ref=source_ref,
        platform=platform,
        language=req.language,
        script_pref=req.script_pref,
        aspect=req.aspect,
        auto_mode=req.auto_mode,
        template_id=req.template_id,
        preset_id=req.preset_id,
        max_clips=req.max_clips,
        clip_brief=req.clip_brief,
        settings_json=settings_snapshot,
        status=JobStatus.QUEUED.value,
        stage="QUEUED",
        progress_pct=5,
        message="Job queued",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    if is_sync:
        run_job_pipeline(job.id)
    else:
        job_dir = get_storage().job_dir(job.id)
        record_job_event(
            job.id,
            "state",
            {
                "stage": job.stage,
                "status": job.status,
                "pct": job.progress_pct,
                "message": job.message,
            },
            job_dir=job_dir,
        )
        enqueue_job(job.id)

    session.expire_all()
    fresh_job = session.get(Job, job.id) or job
    return JSONResponse(status_code=status_code, content=_serialize_job(session, fresh_job))


@app.get("/api/v1/jobs")
def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if status is not None and status not in VALID_JOB_STATUSES:
        raise ApiError(422, "VALIDATION_ERROR", f"Invalid job status filter: {status!r}")

    filters: list[Any] = [col(Job.deleted_at).is_(None)]
    if status is not None:
        filters.append(col(Job.status) == status)

    total_stmt = select(func.count()).select_from(Job).where(*filters)
    total = int(session.exec(total_stmt).one())

    query_filters: list[Any] = list(filters)
    if cursor:
        cur_ts, cur_id = _decode_cursor(cursor)
        query_filters.append(
            (col(Job.created_at) < cur_ts)
            | ((col(Job.created_at) == cur_ts) & (col(Job.id) < cur_id))
        )

    rows = list(
        session.exec(
            select(Job)
            .where(*query_filters)
            .order_by(col(Job.created_at).desc(), col(Job.id).desc())
            .limit(limit + 1)
        ).all()
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = _encode_cursor(page_rows[-1]) if has_more and page_rows else None

    return {
        "items": [_serialize_job(session, j) for j in page_rows],
        "next_cursor": next_cursor,
        "total": total,
    }


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = _get_active_job_or_404(session, job_id)
    return _serialize_job(session, job)


@app.post("/api/v1/jobs/{job_id}/retry")
def retry_job_endpoint(
    job_id: str,
    req: RetryJobRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> JSONResponse:
    job = _get_active_job_or_404(session, job_id)
    if job.status in ACTIVE_JOB_STATUSES:
        raise ApiError(
            409,
            "VALIDATION_ERROR",
            f"Job {job_id} is actively running ({job.status}) and cannot be retried",
        )
    from_stage = req.from_stage if req else None
    if from_stage is not None and from_stage not in STAGES:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            f"Invalid from_stage {from_stage!r}; expected one of {STAGES}",
        )

    retry_job(job_id, from_stage=from_stage, enqueue=True)
    session.expire_all()
    fresh_job = _get_active_job_or_404(session, job_id)
    return JSONResponse(status_code=202, content=_serialize_job(session, fresh_job))


@app.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    _get_active_job_or_404(session, job_id)
    cancel_job(job_id)
    session.expire_all()
    fresh_job = _get_active_job_or_404(session, job_id)
    return _serialize_job(session, fresh_job)


@app.delete("/api/v1/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, session: Session = Depends(get_session)) -> Response:
    job = _get_active_job_or_404(session, job_id)

    clips = list(session.exec(select(Clip).where(Clip.job_id == job_id)).all())
    for c in clips:
        for fb in session.exec(select(FeedbackEvent).where(FeedbackEvent.clip_id == c.id)).all():
            session.delete(fb)
        for ren in session.exec(select(Render).where(Render.clip_id == c.id)).all():
            session.delete(ren)
        for pp in session.exec(select(PublishPack).where(PublishPack.clip_id == c.id)).all():
            session.delete(pp)
        session.delete(c)

    for cp in session.exec(select(StageCheckpoint).where(StageCheckpoint.job_id == job_id)).all():
        session.delete(cp)
    for tr in session.exec(select(Transcript).where(Transcript.job_id == job_id)).all():
        session.delete(tr)

    session.delete(job)
    session.commit()

    clear_job_events(job_id)
    get_storage().delete_job(job_id)
    return Response(status_code=204)


@app.get("/api/v1/jobs/{job_id}/transcript")
def get_job_transcript(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    _get_active_job_or_404(session, job_id)
    tr_row = session.exec(select(Transcript).where(Transcript.job_id == job_id)).first()
    tp = Path(tr_row.path_json) if tr_row and tr_row.path_json else None
    if tp is None or not tp.exists():
        tp = get_storage().job_dir(job_id) / "asr" / "transcript.json"
    if not tp.exists():
        raise ApiError(404, "VALIDATION_ERROR", "Transcript not ready yet")
    return json.loads(tp.read_text(encoding="utf-8"))


@app.patch("/api/v1/jobs/{job_id}/transcript")
def patch_job_transcript(
    job_id: str,
    req: PatchTranscriptRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _get_active_job_or_404(session, job_id)
    tr_row = session.exec(select(Transcript).where(Transcript.job_id == job_id)).first()
    tp = Path(tr_row.path_json) if tr_row and tr_row.path_json else None
    if tp is None or not tp.exists():
        tp = get_storage().job_dir(job_id) / "asr" / "transcript.json"
    if not tp.exists():
        raise ApiError(404, "VALIDATION_ERROR", "Transcript not ready yet")

    transcript = json.loads(tp.read_text(encoding="utf-8"))
    segments = transcript.get("segments", [])
    flat_words: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for seg in segments:
        for w in seg.get("words", []):
            flat_words.append((seg, w))

    touched_segments: set[int] = set()
    for edit in req.edits:
        if edit.idx < 0 or edit.idx >= len(flat_words):
            raise ApiError(
                422,
                "VALIDATION_ERROR",
                f"Word index {edit.idx} out of range (0..{max(0, len(flat_words) - 1)})",
            )
        seg, word = flat_words[edit.idx]
        word["w"] = edit.text
        word["script"] = detect_word_script(edit.text)
        flags = list(word.get("flags") or [])
        if "edited" not in flags:
            flags.append("edited")
        word["flags"] = flags
        touched_segments.add(id(seg))

    for seg in segments:
        if id(seg) in touched_segments:
            seg["text"] = " ".join(str(w.get("w", "")) for w in seg.get("words", []))

    transcript["edited"] = True
    _atomic_write_json(tp, transcript)

    if tr_row is not None:
        tr_row.edited = True
        tr_row.updated_at = _now_iso()
        session.add(tr_row)

    mark_stale_from(job_id, "analyze", session=session, reason="transcript_edited")
    return transcript


@app.post("/api/v1/jobs/{job_id}/reselect")
def reselect_job_clips(
    job_id: str,
    req: ReselectClipsRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> JSONResponse:
    job = _get_active_job_or_404(session, job_id)
    if job.status in ACTIVE_JOB_STATUSES:
        raise ApiError(
            409,
            "VALIDATION_ERROR",
            f"Job {job_id} is actively running ({job.status}) and cannot be reselected",
        )

    if req is not None:
        if "clip_brief" in req.model_fields_set:
            job.clip_brief = req.clip_brief
        if req.max_clips is not None:
            job.max_clips = req.max_clips
        if req.threshold is not None:
            snap = dict(job.settings_json or {})
            sel_cfg = dict(snap.get("select") or {})
            sel_cfg["threshold"] = req.threshold
            snap["select"] = sel_cfg
            job.settings_json = snap
        job.updated_at = _now_iso()
        session.add(job)
        session.commit()

    retry_job(job_id, from_stage="select", enqueue=True)
    session.expire_all()
    fresh_job = _get_active_job_or_404(session, job_id)
    return JSONResponse(status_code=202, content=_serialize_job(session, fresh_job))


@app.post("/api/v1/jobs/{job_id}/render-all")
def render_all_job_clips(
    job_id: str,
    req: RenderAllRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> JSONResponse:
    job = _get_active_job_or_404(session, job_id)
    if job.status in ACTIVE_JOB_STATUSES:
        raise ApiError(
            409,
            "VALIDATION_ERROR",
            f"Job {job_id} is actively running ({job.status}) and cannot render all clips",
        )

    if req is not None and req.preset_id is not None:
        job.preset_id = req.preset_id
        job.updated_at = _now_iso()
        session.add(job)
        for c in session.exec(select(Clip).where(Clip.job_id == job_id)).all():
            c.preset_id = req.preset_id
            session.add(c)
        session.commit()

    retry_job(job_id, from_stage="render", enqueue=True)
    session.expire_all()
    fresh_job = _get_active_job_or_404(session, job_id)
    return JSONResponse(status_code=202, content=_serialize_job(session, fresh_job))


@app.get("/api/v1/jobs/{job_id}/events")
async def job_events_sse(
    job_id: str,
    request: Request,
    last_event_id: str | None = Query(default=None),
    timeout: float = Query(default=30.0, ge=0.1, le=300.0),
) -> StreamingResponse:
    with Session(get_engine()) as session:
        _get_active_job_or_404(session, job_id)

    after_raw = last_event_id if last_event_id is not None else request.headers.get("last-event-id")
    start_after_id = _parse_event_id(after_raw)
    job_dir = get_storage().job_dir(job_id)

    async def event_generator():
        last_id = start_after_id
        deadline = time.monotonic() + float(timeout)
        last_emit_at = time.monotonic()

        while True:
            events = get_job_events(job_id, after_id=last_id, job_dir=job_dir)
            if events:
                for ev in events:
                    last_id = ev.id
                    last_emit_at = time.monotonic()
                    payload = json.dumps(ev.data, ensure_ascii=False)
                    yield f"id: {ev.id}\nevent: {ev.event}\ndata: {payload}\n\n"
                    if ev.event == "done":
                        return

            with Session(get_engine()) as s:
                cur = s.get(Job, job_id)
                if cur is None or cur.deleted_at is not None:
                    return
                if cur.status in TERMINAL_JOB_STATUSES:
                    all_events = get_job_events(job_id, after_id=0, job_dir=job_dir)
                    has_done = any(ev.event == "done" for ev in all_events)
                    if not has_done:
                        done_id = max(last_id, all_events[-1].id if all_events else 0) + 1
                        done_payload = json.dumps(
                            {
                                "job_id": cur.id,
                                "stage": cur.stage,
                                "status": cur.status,
                                "pct": cur.progress_pct,
                                "message": cur.message,
                            },
                            ensure_ascii=False,
                        )
                        yield f"id: {done_id}\nevent: done\ndata: {done_payload}\n\n"
                    return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return

            new_events = await asyncio.to_thread(
                wait_for_job_events,
                job_id,
                after_id=last_id,
                timeout=min(0.5, remaining),
                job_dir=job_dir,
            )
            if not new_events and (time.monotonic() - last_emit_at) >= 15.0:
                last_emit_at = time.monotonic()
                yield ": heartbeat\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/clips/{clip_id}")
def get_clip(clip_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if not clip:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    job = session.get(Job, clip.job_id)
    if job is not None and job.deleted_at is not None:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    return _serialize_clip_detail(clip, job)


@app.patch("/api/v1/clips/{clip_id}")
async def update_clip(
    clip_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if not clip:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    job = session.get(Job, clip.job_id)
    if job is not None and job.deleted_at is not None:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")

    try:
        body = await request.json()
    except Exception as exc:
        raise ApiError(400, "VALIDATION_ERROR", "Invalid JSON payload") from exc
    if not isinstance(body, dict):
        raise ApiError(422, "VALIDATION_ERROR", "Expected JSON object")

    plan = _load_or_build_clip_plan(clip, job)

    # Apply legacy flat fields when provided
    if "start" in body and body["start"] is not None:
        clip.start = round(float(body["start"]), 2)
        clip.start_s = clip.start
        if "segments" not in body and isinstance(plan.get("segments"), list) and plan["segments"]:
            plan["segments"][0]["start"] = clip.start
    if "end" in body and body["end"] is not None:
        clip.end = round(float(body["end"]), 2)
        clip.end_s = clip.end
        if "segments" not in body and isinstance(plan.get("segments"), list) and plan["segments"]:
            plan["segments"][0]["end"] = clip.end
    if "title" in body and body["title"] is not None:
        clip.title = str(body["title"])
    if "layout" in body and isinstance(body["layout"], str):
        clip.layout = body["layout"]
        layout_obj = plan.setdefault("layout", {})
        if isinstance(layout_obj, dict):
            layout_obj["force"] = body["layout"]
            layout_obj["template"] = f"{body['layout'].split('@')[0]}@latest"
    if "template_id" in body and body["template_id"] is not None:
        clip.template_id = str(body["template_id"])
        cap_obj = plan.setdefault("captions", {})
        if isinstance(cap_obj, dict):
            tpl_str = clip.template_id
            cap_obj["template"] = tpl_str if "@" in tpl_str else f"{tpl_str}@latest"
    if "preset_id" in body:
        clip.preset_id = body["preset_id"]
    if "aspect" in body and body["aspect"] is not None:
        clip.aspect = str(body["aspect"])
        out_obj = plan.setdefault("output", {})
        if isinstance(out_obj, dict):
            out_obj["aspect"] = clip.aspect
    if "words" in body and body["words"] is not None:
        if not isinstance(body["words"], list):
            raise ApiError(422, "VALIDATION_ERROR", "words must be a list")
        clip.words_json = json.dumps(body["words"], ensure_ascii=False)
        clip.transcript_snippet = " ".join(
            str(w.get("w", "")) for w in body["words"] if isinstance(w, dict)
        )

    # Apply RFC-7396 JSON Merge Patch on clip_plan
    plan_patch: dict[str, Any] = {}
    if isinstance(body.get("clip_plan"), dict):
        plan_patch = _json_merge_patch(plan_patch, body["clip_plan"])
    for key in CLIP_PLAN_KEYS:
        if key in body:
            if key == "layout" and isinstance(body["layout"], str):
                continue
            plan_patch[key] = body[key]

    if plan_patch:
        plan = _json_merge_patch(plan, plan_patch)

    # Sync DB Clip summary fields from authoritative clip_plan
    segments = plan.get("segments")
    if isinstance(segments, list) and segments and isinstance(segments[0], dict):
        seg0 = segments[0]
        if seg0.get("start") is not None:
            clip.start = round(float(seg0["start"]), 2)
            clip.start_s = clip.start
        if seg0.get("end") is not None:
            clip.end = round(float(seg0["end"]), 2)
            clip.end_s = clip.end
    clip.duration = round(max(1.0, clip.end - clip.start), 2)

    if isinstance(plan.get("layout"), dict):
        force_layout = plan["layout"].get("force")
        tpl_layout = plan["layout"].get("template")
        if force_layout:
            clip.layout = str(force_layout)
        elif isinstance(tpl_layout, str) and tpl_layout:
            clip.layout = tpl_layout.split("@")[0]

    if isinstance(plan.get("captions"), dict):
        cap_tpl = plan["captions"].get("template")
        if isinstance(cap_tpl, str) and cap_tpl:
            clip.template_id = cap_tpl.split("@")[0]

    if isinstance(plan.get("output"), dict):
        out_aspect = plan["output"].get("aspect")
        if isinstance(out_aspect, str) and out_aspect:
            clip.aspect = out_aspect

    plan_file = (
        Path(clip.plan_path)
        if clip.plan_path
        else (get_storage().job_dir(clip.job_id) / "clips" / clip.id / "clip_plan.json")
    )
    _atomic_write_json(plan_file, plan)
    clip.plan_path = str(plan_file)

    session.add(clip)
    session.commit()
    session.refresh(clip)
    return _serialize_clip_detail(clip, job)


@app.post("/api/v1/clips/{clip_id}/render", status_code=202)
def render_single_clip(
    clip_id: str,
    req: RenderClipRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if not clip:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    job = session.get(Job, clip.job_id)
    if job is not None and job.deleted_at is not None:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")

    if req is not None:
        if req.template_id:
            clip.template_id = req.template_id
        if req.aspect:
            clip.aspect = req.aspect
        if req.layout:
            clip.layout = req.layout
        if req.preset_id:
            clip.preset_id = req.preset_id

    profile = (req.profile if req and req.profile else None) or "1080p_fast"
    plan = _load_or_build_clip_plan(clip, job)
    plan.setdefault("output", {})["aspect"] = clip.aspect
    plan.setdefault("output", {})["profile"] = profile
    plan.setdefault("captions", {})["template"] = f"{clip.template_id.split('@')[0]}@latest"
    plan.setdefault("layout", {})["force"] = clip.layout
    plan_file = (
        Path(clip.plan_path)
        if clip.plan_path
        else (get_storage().job_dir(clip.job_id) / "clips" / clip.id / "clip_plan.json")
    )
    _atomic_write_json(plan_file, plan)
    clip.plan_path = str(plan_file)

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

    plan_hash = _compute_plan_hash(clip, plan, profile)
    ren = Render(
        clip_id=clip.id,
        plan_hash=plan_hash,
        template_versions_json={
            "caption": f"{clip.template_id}@1",
            "layout": f"{clip.layout}@1",
        },
        aspect=clip.aspect,
        profile=profile,
        path_mp4=res["mp4_path"],
        path_srt=res["srt_path"],
        path_ass=res["ass_path"],
        path_thumb=res["thumb_path"],
        status="DONE",
        progress_pct=100.0,
        duration_s=clip.duration,
    )
    session.add(clip)
    session.add(ren)
    session.commit()
    session.refresh(ren)
    return _serialize_render(ren)


@app.get("/api/v1/clips/{clip_id}/renders")
def list_clip_renders(clip_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if not clip:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    renders = list(
        session.exec(
            select(Render)
            .where(Render.clip_id == clip_id)
            .order_by(col(Render.created_at).desc())
        ).all()
    )
    return {"items": [_serialize_render(r) for r in renders]}


@app.get("/api/v1/renders/{render_id}")
def get_render(render_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    ren = session.get(Render, render_id)
    if not ren:
        raise ApiError(404, "VALIDATION_ERROR", f"Render {render_id} not found")
    return _serialize_render(ren)


@app.get("/api/v1/renders/{render_id}/file")
def get_render_file(render_id: str, session: Session = Depends(get_session)) -> FileResponse:
    ren = session.get(Render, render_id)
    if not ren or not ren.path_mp4 or not Path(ren.path_mp4).exists():
        raise ApiError(404, "VALIDATION_ERROR", f"Rendered video for {render_id} not found")
    return FileResponse(ren.path_mp4, media_type="video/mp4")


@app.post("/api/v1/clips/{clip_id}/feedback")
def submit_clip_feedback(
    clip_id: str,
    req: ClipFeedbackRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if not clip:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")
    job = session.get(Job, clip.job_id)
    if job is not None and job.deleted_at is not None:
        raise ApiError(404, "VALIDATION_ERROR", f"Clip {clip_id} not found")

    if req.value not in ("up", "down"):
        raise ApiError(422, "VALIDATION_ERROR", "feedback value must be 'up' or 'down'")

    clip.feedback = req.value
    if req.actual_views is not None:
        clip.actual_views = req.actual_views
    session.add(clip)

    session.add(FeedbackEvent(clip_id=clip.id, kind="rating", value=req.value))
    if req.actual_views is not None:
        session.add(
            FeedbackEvent(clip_id=clip.id, kind="actual_views", value=str(req.actual_views))
        )

    session.commit()
    session.refresh(clip)
    return _serialize_clip_detail(clip, job)


@app.get("/api/v1/clips/{clip_id}/video.mp4")
def serve_clip_video(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.render_mp4_path or not Path(clip.render_mp4_path).exists():
        raise ApiError(404, "VALIDATION_ERROR", "Rendered video not found")
    return FileResponse(clip.render_mp4_path, media_type="video/mp4")


@app.get("/api/v1/clips/{clip_id}/subtitles.ass")
def serve_clip_ass(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.render_ass_path or not Path(clip.render_ass_path).exists():
        raise ApiError(404, "VALIDATION_ERROR", "ASS file not found")
    return FileResponse(clip.render_ass_path, media_type="text/plain; charset=utf-8")


@app.get("/api/v1/clips/{clip_id}/subtitles.srt")
def serve_clip_srt(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.render_srt_path or not Path(clip.render_srt_path).exists():
        raise ApiError(404, "VALIDATION_ERROR", "SRT file not found")
    return FileResponse(clip.render_srt_path, media_type="text/plain; charset=utf-8")


@app.get("/api/v1/clips/{clip_id}/thumb.jpg")
def serve_clip_thumb(clip_id: str, session: Session = Depends(get_session)) -> FileResponse:
    clip = session.get(Clip, clip_id)
    if not clip or not clip.thumb_path or not Path(clip.thumb_path).exists():
        raise ApiError(404, "VALIDATION_ERROR", "Thumbnail not found")
    return FileResponse(clip.thumb_path, media_type="image/jpeg")


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
