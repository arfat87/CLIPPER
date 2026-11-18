from __future__ import annotations

import base64
import io
import json
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from backend.app.config import get_settings
from backend.app.db import get_session
from backend.app.models.entities import Clip, Job, JobStatus, StageCheckpoint
from backend.app.services.admin import (
    ADMIN_SESSION_COOKIE,
    AdminError,
    activate_prompt_version_service,
    archive_template_service,
    check_and_authenticate_admin,
    compute_job_disk_and_artifacts,
    dry_run_scoring_service,
    duplicate_template_service,
    export_template_zip_bytes,
    get_masked_settings_service,
    get_prompt_detail_service,
    get_scoring_config_service,
    get_template_detail_service,
    import_template_bytes,
    list_audit_logs,
    list_brandkits_service,
    list_fonts_service,
    list_presets_service,
    list_prompts_service,
    list_template_versions_service,
    list_templates_service,
    publish_template_service,
    register_font_service,
    render_template_preview_png,
    save_brandkit_service,
    save_preset_service,
    save_prompt_version_service,
    test_prompt_service,
    update_scoring_config_service,
    update_settings_service,
    verify_admin_session_token,
)

router = APIRouter(prefix="/api/v1")

_VALID_JOB_STATUSES = {s.value for s in JobStatus}


def require_admin_session(request: Request) -> str:
    settings = get_settings()
    if not settings.admin_enabled:
        raise AdminError(403, "VALIDATION_ERROR", "Admin panel is disabled (ADMIN_ENABLED=false)")
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not verify_admin_session_token(token):
        raise AdminError(401, "VALIDATION_ERROR", "Admin authentication required")
    return "admin"


class AdminLoginRequest(BaseModel):
    password: str


class TemplatePreviewRequest(BaseModel):
    language: str = "hinglish"
    sample_text: str | None = None


class DuplicateTemplateRequest(BaseModel):
    new_id: str | None = None
    new_name: str | None = None


class UpdatePromptRequest(BaseModel):
    content: str


class ActivatePromptRequest(BaseModel):
    version: int = Field(..., ge=1)


class ScoringDryRunRequest(BaseModel):
    job_id: str
    weights: dict[str, float] | None = None


# ---------------------------------------------------------------------------
# Public Templates & Presets
# ---------------------------------------------------------------------------


@router.get("/templates", tags=["Templates"])
def public_list_templates(
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    items = list_templates_service(
        session,
        kind=kind,
        status=status,
        include_non_published=False,
    )
    return {"items": items}


@router.get("/templates/{template_id}", tags=["Templates"])
def public_get_template(
    template_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_template_detail_service(session, template_id)


@router.get("/templates/{template_id}/versions", tags=["Templates"])
def public_list_template_versions(
    template_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"items": list_template_versions_service(session, template_id)}


@router.post("/templates/{template_id}/preview", tags=["Templates"])
def public_preview_template(
    template_id: str,
    req: TemplatePreviewRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> Response:
    lang = req.language if req else "hinglish"
    sample_text = req.sample_text if req else None
    png_bytes = render_template_preview_png(
        session,
        template_id,
        language=lang,
        sample_text=sample_text,
    )
    return Response(content=png_bytes, media_type="image/png")


@router.get("/presets", tags=["Templates"])
def public_list_presets(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"items": list_presets_service(session)}


# ---------------------------------------------------------------------------
# Admin Authentication
# ---------------------------------------------------------------------------


@router.post("/admin/auth/login", tags=["Admin"])
def admin_login(req: AdminLoginRequest, request: Request) -> JSONResponse:
    client_ip = request.client.host if request.client else "unknown"
    token = check_and_authenticate_admin(req.password, client_ip)
    resp = JSONResponse(status_code=200, content={"authenticated": True})
    resp.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=86400,
    )
    return resp


@router.post("/admin/auth/logout", tags=["Admin"])
def admin_logout() -> JSONResponse:
    resp = JSONResponse(status_code=200, content={"authenticated": False})
    resp.delete_cookie(key=ADMIN_SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------------------------
# Admin Templates & Presets
# ---------------------------------------------------------------------------


@router.get("/admin/templates", tags=["Admin"])
def admin_list_templates(
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    items = list_templates_service(
        session,
        kind=kind,
        status=status,
        include_non_published=True,
    )
    return {"items": items}


@router.post("/admin/templates", status_code=201, tags=["Admin"])
async def admin_import_template(
    request: Request,
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as exc:
            raise AdminError(400, "TEMPLATE_INVALID", "Invalid JSON body") from exc
        if not isinstance(body, dict):
            raise AdminError(422, "TEMPLATE_INVALID", "Expected JSON object")
        kind = str(body.pop("_kind", "caption") if "_kind" in body else "caption")
        raw_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return import_template_bytes(
            session,
            filename="template.json",
            raw_bytes=raw_bytes,
            kind=kind,
            actor=actor,
        )

    form = await request.form()
    file_obj = form.get("file")
    if file_obj is None or not hasattr(file_obj, "read"):
        raise AdminError(422, "VALIDATION_ERROR", "Missing multipart 'file' field")
    filename = str(getattr(file_obj, "filename", None) or "template.json")
    kind = str(form.get("kind") or "caption")
    raw_bytes = await file_obj.read()
    return import_template_bytes(
        session,
        filename=filename,
        raw_bytes=raw_bytes,
        kind=kind,
        actor=actor,
    )


@router.post("/admin/templates/{template_id}/publish", tags=["Admin"])
def admin_publish_template(
    template_id: str,
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return publish_template_service(session, template_id, actor=actor)


@router.post("/admin/templates/{template_id}/archive", tags=["Admin"])
def admin_archive_template(
    template_id: str,
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return archive_template_service(session, template_id, actor=actor)


@router.post("/admin/templates/{template_id}/duplicate", status_code=201, tags=["Admin"])
def admin_duplicate_template(
    template_id: str,
    req: DuplicateTemplateRequest | None = Body(default=None),
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return duplicate_template_service(
        session,
        template_id,
        new_id=req.new_id if req else None,
        new_name=req.new_name if req else None,
        actor=actor,
    )


@router.get("/admin/templates/{template_id}/export", tags=["Admin"])
def admin_export_template(
    template_id: str,
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    filename, zip_bytes = export_template_zip_bytes(session, template_id)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/admin/presets", tags=["Admin"])
def admin_list_presets(
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"items": list_presets_service(session)}


@router.post("/admin/presets", tags=["Admin"])
def admin_save_preset(
    payload: dict[str, Any],
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return save_preset_service(session, payload, actor=actor)


# ---------------------------------------------------------------------------
# Admin Fonts & Brand Kits
# ---------------------------------------------------------------------------


@router.get("/admin/fonts", tags=["Admin"])
def admin_list_fonts(
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"items": list_fonts_service(session)}


@router.post("/admin/fonts", status_code=201, tags=["Admin"])
async def admin_upload_font(
    file: UploadFile = File(...),
    license: str = Form(...),
    family: str | None = Form(default=None),
    style: str = Form(default="Regular"),
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    raw_bytes = await file.read()
    return register_font_service(
        session,
        filename=file.filename or "font.ttf",
        raw_bytes=raw_bytes,
        license_str=license,
        family=family,
        style=style,
        actor=actor,
    )


@router.get("/admin/brandkits", tags=["Admin"])
def admin_list_brandkits(
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"items": list_brandkits_service(session)}


@router.post("/admin/brandkits", status_code=201, tags=["Admin"])
def admin_save_brandkit(
    payload: dict[str, Any],
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return save_brandkit_service(session, payload, actor=actor)


# ---------------------------------------------------------------------------
# Admin Prompts
# ---------------------------------------------------------------------------


@router.get("/admin/prompts", tags=["Admin"])
def admin_list_prompts(
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return {"items": list_prompts_service(session)}


@router.get("/admin/prompts/{name}", tags=["Admin"])
def admin_get_prompt(
    name: str,
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_prompt_detail_service(session, name)


@router.put("/admin/prompts/{name}", tags=["Admin"])
def admin_update_prompt(
    name: str,
    req: UpdatePromptRequest,
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return save_prompt_version_service(session, name, req.content, actor=actor)


@router.post("/admin/prompts/{name}/activate", tags=["Admin"])
def admin_activate_prompt(
    name: str,
    req: ActivatePromptRequest,
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return activate_prompt_version_service(session, name, req.version, actor=actor)


@router.post("/admin/prompts/{name}/test", tags=["Admin"])
def admin_test_prompt(
    name: str,
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return test_prompt_service(session, name)


# ---------------------------------------------------------------------------
# Admin Scoring & Settings
# ---------------------------------------------------------------------------


@router.get("/admin/scoring", tags=["Admin"])
def admin_get_scoring(
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_scoring_config_service(session)


@router.put("/admin/scoring", tags=["Admin"])
def admin_update_scoring(
    payload: dict[str, Any],
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return update_scoring_config_service(session, payload, actor=actor)


@router.post("/admin/scoring/dry-run", tags=["Admin"])
def admin_dry_run_scoring(
    req: ScoringDryRunRequest,
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return dry_run_scoring_service(
        session,
        job_id=req.job_id,
        weights_override=req.weights,
    )


@router.get("/admin/settings", tags=["Admin"])
def admin_get_settings(
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_masked_settings_service(session)


@router.put("/admin/settings", tags=["Admin"])
def admin_update_settings(
    payload: dict[str, Any],
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return update_settings_service(session, payload, actor=actor)


# ---------------------------------------------------------------------------
# Admin Jobs & Audit Log
# ---------------------------------------------------------------------------


@router.get("/admin/jobs", tags=["Admin"])
def admin_list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    if status is not None and status not in _VALID_JOB_STATUSES:
        raise AdminError(422, "VALIDATION_ERROR", f"Invalid job status filter: {status!r}")

    filters = [col(Job.deleted_at).is_(None)]
    if status is not None:
        filters.append(col(Job.status) == status)

    total = int(session.exec(select(func.count()).select_from(Job).where(*filters)).one())
    query_filters = list(filters)
    if cursor:
        try:
            obj = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
            cur_ts, cur_id = str(obj["created_at"]), str(obj["id"])
        except Exception as exc:
            raise AdminError(400, "VALIDATION_ERROR", "Invalid pagination cursor") from exc
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
    page = rows[:limit]
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"created_at": page[-1].created_at, "id": page[-1].id}, separators=(",", ":")
            ).encode("utf-8")
        ).decode("ascii")
        if has_more and page
        else None
    )

    items: list[dict[str, Any]] = []
    for job in page:
        disk_bytes, artifacts = compute_job_disk_and_artifacts(job.id)
        clips = list(
            session.exec(select(Clip).where(Clip.job_id == job.id).order_by(col(Clip.rank))).all()
        )
        cps = list(
            session.exec(
                select(StageCheckpoint)
                .where(StageCheckpoint.job_id == job.id)
                .order_by(col(StageCheckpoint.id))
            ).all()
        )
        items.append(
            {
                "id": job.id,
                "source_kind": job.source_kind,
                "source_url": job.source_url,
                "source_ref": job.source_ref,
                "platform": job.platform,
                "title": job.title,
                "duration_s": job.duration_s if job.duration_s is not None else job.duration,
                "language": job.language,
                "script": job.script,
                "genre": job.genre,
                "status": job.status,
                "stage": job.stage,
                "error_code": job.error_code,
                "error_message": job.error_message,
                "auto_mode": job.auto_mode,
                "preset_id": job.preset_id,
                "clip_count": len(clips),
                "stages": [
                    {
                        "stage": cp.stage,
                        "status": cp.status,
                        "attempt": cp.attempt,
                        "started_at": cp.started_at,
                        "finished_at": cp.finished_at,
                        "metrics": cp.metrics_json or {},
                    }
                    for cp in cps
                ],
                "disk_usage_bytes": disk_bytes,
                "artifacts": artifacts,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }
        )

    return {"items": items, "next_cursor": next_cursor, "total": total}


@router.get("/admin/audit", tags=["Admin"])
def admin_list_audit(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target: str | None = Query(default=None),
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return list_audit_logs(
        session,
        limit=limit,
        cursor=cursor,
        action=action,
        target=target,
    )
