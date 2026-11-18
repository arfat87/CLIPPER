from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session

from backend.app.db import get_session
from backend.app.routes_admin import require_admin_session
from backend.app.services.health import (
    get_drive_status_service,
    get_drive_tree_service,
    get_health_service,
    list_all_blockers_service,
    list_drive_files_service,
    pick_drive_file_service,
    resolve_blocker_service,
    trigger_drive_sync_service,
)

router = APIRouter(prefix="/api/v1")


class DriveSyncRequest(BaseModel):
    full: bool = False
    dry_run: bool = False


class DrivePickRequest(BaseModel):
    file_id: str
    create_job: bool = False
    language: str = "auto"
    aspect: str = "9:16"
    preset_id: str | None = None
    auto_mode: bool = True


class ResolveBlockerRequest(BaseModel):
    note: str | None = None
    resolution_note: str | None = None
    resolved_by: str = "web"


@router.get("/health", tags=["Health"])
def get_health(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_health_service(session)


@router.get("/drive/status", tags=["Drive"])
def get_drive_status(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_drive_status_service(session)


@router.get("/drive/tree", tags=["Drive"])
def get_drive_tree(session: Session = Depends(get_session)) -> dict[str, Any]:
    return get_drive_tree_service(session)


@router.post("/drive/sync", tags=["Drive"])
def trigger_drive_sync(
    req: DriveSyncRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> JSONResponse:
    full = bool(req.full) if req is not None else False
    dry_run = bool(req.dry_run) if req is not None else False
    payload = trigger_drive_sync_service(
        session,
        full=full,
        dry_run=dry_run,
    )
    return JSONResponse(status_code=202 if not dry_run else 200, content=payload)


@router.get("/drive/files", tags=["Drive"])
def list_drive_files(
    route: str | None = Query(default=None),
    category: str | None = Query(default=None),
    subfolder: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return list_drive_files_service(
        session,
        route=route,
        category=category,
        subfolder=subfolder,
    )


@router.post("/drive/pick", tags=["Drive"])
def pick_drive_file(
    req: DrivePickRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return pick_drive_file_service(
        session,
        file_id=req.file_id,
        create_job=req.create_job,
        language=req.language,
        aspect=req.aspect,
        preset_id=req.preset_id,
        auto_mode=req.auto_mode,
    )


@router.get("/admin/drive", tags=["Admin"])
def admin_get_drive(
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_drive_status_service(session)


@router.get("/admin/blockers", tags=["Admin"])
def admin_list_blockers(
    status: str | None = Query(default=None),
    _actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    items = list_all_blockers_service(session, status_filter=status)
    return {"items": items, "total": len(items)}


@router.post("/admin/blockers/{blocker_id}/resolve", tags=["Admin"])
def admin_resolve_blocker(
    blocker_id: str,
    req: ResolveBlockerRequest | None = Body(default=None),
    actor: str = Depends(require_admin_session),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    raw_note = (req.resolution_note or req.note) if req is not None else None
    note = str(raw_note) if raw_note else "Resolved via Web UI"
    resolved_by = (req.resolved_by if req and req.resolved_by else "web").strip() or "web"
    return resolve_blocker_service(
        session,
        blocker_id,
        note=note,
        resolved_by=resolved_by,
        actor=actor,
    )
