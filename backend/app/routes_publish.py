from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session

from backend.app.db import get_session
from backend.app.services.publish_service import (
    build_job_zip_bundle,
    get_clip_publish_pack_service,
    regenerate_clip_publish_pack_service,
    update_clip_publish_pack_service,
)

router = APIRouter(prefix="/api/v1")


class UpdatePublishPackRequest(BaseModel):
    title: str | None = None
    title_options: list[str] | None = None
    description: str | None = None
    hashtags: list[str] | None = None
    tags: list[str] | None = None
    pinned_comment: str | None = None
    hook_line: str | None = None
    platform: str | None = None
    language: str | None = None
    chapters: list[Any] | str | None = None
    schedule_notes: str | None = None


class RegeneratePublishPackRequest(BaseModel):
    template_id: str | None = None
    publish_template_id: str | None = None
    platform: str | None = None
    language: str | None = None
    tone: str | None = None


@router.get("/clips/{clip_id}/publish-pack", tags=["Clips"])
def get_clip_publish_pack(
    clip_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_clip_publish_pack_service(session, clip_id)


@router.put("/clips/{clip_id}/publish-pack", tags=["Clips"])
def update_clip_publish_pack(
    clip_id: str,
    req: UpdatePublishPackRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for field_name in req.model_fields_set:
        updates[field_name] = getattr(req, field_name)
    return update_clip_publish_pack_service(session, clip_id, updates)


@router.post("/clips/{clip_id}/publish-pack/regenerate", tags=["Clips"])
def regenerate_clip_publish_pack(
    clip_id: str,
    req: RegeneratePublishPackRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return regenerate_clip_publish_pack_service(
        session,
        clip_id,
        template_id=req.template_id if req else None,
        publish_template_id=req.publish_template_id if req else None,
        platform=req.platform if req else None,
        language=req.language if req else None,
        tone=req.tone if req else None,
    )


@router.get("/jobs/{job_id}/download.zip", tags=["Jobs"])
def download_job_zip_bundle(
    job_id: str,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    buf = build_job_zip_bundle(session, job_id)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="clipper_{job_id}.zip"'},
    )
