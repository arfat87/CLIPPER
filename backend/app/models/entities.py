from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    PREPARING = "PREPARING"
    DOWNLOADING = "DOWNLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    SELECTING = "SELECTING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    RENDERING = "RENDERING"
    PACKAGING = "PACKAGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Job(SQLModel, table=True):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_status_created_at", "status", "created_at"),)

    id: str = Field(default_factory=_new_id, primary_key=True)
    source_kind: str = "url"  # url | upload | drive
    source_url: str = ""
    source_ref: str | None = None
    platform: str = "youtube"
    title: str = "Untitled Video"
    duration: float = 0.0
    duration_s: float | None = None
    language: str = "auto"
    detected_language: str = "en"
    script_pref: str = "auto"  # auto | latn | deva | beng | native
    script: str | None = None
    genre: str | None = None
    aspect: str = "9:16"
    auto_mode: bool = True
    template_id: str = "bold_pop_yellow"
    preset_id: str | None = None
    max_clips: int = 12
    clip_brief: str | None = None
    status: str = JobStatus.QUEUED.value
    stage: str = "QUEUED"
    progress_pct: int = 0
    message: str = "Queued"
    error: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    settings_json: dict[str, Any] | None = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    versions_json: dict[str, Any] | None = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    deleted_at: str | None = None


class StageCheckpoint(SQLModel, table=True):
    __tablename__ = "stage_checkpoint"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", "attempt", name="uq_stage_checkpoint_job_stage_attempt"),
        Index("ix_stage_checkpoint_job_stage", "job_id", "stage"),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_id: str = Field(foreign_key="job.id", ondelete="CASCADE", index=True)
    stage: str
    status: str = "DONE"  # PENDING | RUNNING | DONE | FAILED | STALE
    input_hash: str | None = None
    artefact_path: str = ""
    attempt: int = 1
    started_at: str = Field(default_factory=_now_iso)
    finished_at: str | None = Field(default_factory=_now_iso)
    error_json: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    metrics_json: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )


class Transcript(SQLModel, table=True):
    __tablename__ = "transcript"

    id: str = Field(default_factory=_new_id, primary_key=True)
    job_id: str = Field(foreign_key="job.id", ondelete="CASCADE", index=True)
    language: str = "en"
    script: str = "latn"
    engine: str = "faster_whisper"
    model_id: str = "small"
    path_json: str = ""
    mean_conf: float | None = None
    edited: bool = False
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class Clip(SQLModel, table=True):
    __tablename__ = "clip"
    __table_args__ = (Index("ix_clip_job_rank", "job_id", "rank"),)

    id: str = Field(default_factory=_new_id, primary_key=True)
    job_id: str = Field(foreign_key="job.id", ondelete="CASCADE", index=True)
    rank: int = 1
    start: float = 0.0
    end: float = 30.0
    start_s: float | None = None
    end_s: float | None = None
    duration: float = 30.0
    score: float = 0.0
    score_mode: str = "hybrid"  # hybrid | signals_only
    score_breakdown_json: str = "{}"
    title: str = ""
    hook_line: str = ""
    hashtags_json: str = "[]"
    reason: str = ""
    transcript_snippet: str = ""
    words_json: str = "[]"
    layout: str = "fill"  # fill | fit_blur
    template_id: str = "bold_pop_yellow"
    preset_id: str | None = None
    plan_path: str | None = None
    aspect: str = "9:16"
    status: str = "PROPOSED"  # PROPOSED | RENDERING | RENDERED | FAILED
    feedback: str | None = None  # up | down | None
    actual_views: int | None = None
    render_mp4_path: str | None = None
    render_ass_path: str | None = None
    render_srt_path: str | None = None
    thumb_path: str | None = None
    created_at: str = Field(default_factory=_now_iso)


class Render(SQLModel, table=True):
    __tablename__ = "render"
    __table_args__ = (Index("ix_render_clip_plan_hash", "clip_id", "plan_hash"),)

    id: str = Field(default_factory=_new_id, primary_key=True)
    clip_id: str = Field(foreign_key="clip.id", ondelete="CASCADE", index=True)
    plan_hash: str = ""
    template_versions_json: dict[str, Any] | None = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    aspect: str = "9:16"
    profile: str = "1080p_fast"
    path_mp4: str | None = None
    path_srt: str | None = None
    path_ass: str | None = None
    path_thumb: str | None = None
    status: str = "PENDING"  # PENDING | RUNNING | DONE | FAILED
    progress_pct: float = 0.0
    duration_s: float | None = None
    created_at: str = Field(default_factory=_now_iso)


class PublishPack(SQLModel, table=True):
    __tablename__ = "publish_pack"

    id: str = Field(default_factory=_new_id, primary_key=True)
    clip_id: str = Field(foreign_key="clip.id", ondelete="CASCADE", index=True)
    platform: str = "youtube_shorts"
    path_json: str = ""
    valid: bool = True
    warnings_json: list[str] | None = Field(
        default_factory=list, sa_column=Column(JSON, nullable=True)
    )
    template_id: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class Template(SQLModel, table=True):
    __tablename__ = "template"

    id: str = Field(primary_key=True)
    kind: str = "caption"  # caption | layout | brand_kit | overlay | publish | preset
    name: str = ""
    status: str = "draft"  # draft | published | archived | invalid
    latest_version: int = 1
    source: str = "builtin"  # builtin | import | drive | admin
    created_at: str = Field(default_factory=_now_iso)


class TemplateVersion(SQLModel, table=True):
    __tablename__ = "template_version"
    __table_args__ = (Index("ix_template_version_tpl_ver", "template_id", "version"),)

    id: str = Field(default_factory=_new_id, primary_key=True)
    template_id: str = Field(foreign_key="template.id", ondelete="CASCADE", index=True)
    version: int = 1
    path: str = ""
    content_hash: str = ""
    valid: bool = True
    validation_json: dict[str, Any] | None = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    license_note: str | None = None
    published_at: str | None = None


class Preset(SQLModel, table=True):
    __tablename__ = "preset"

    id: str = Field(default_factory=_new_id, primary_key=True)
    name: str
    caption_tpl: str = "bold_pop_yellow@latest"
    layout_tpl: str = "fill@latest"
    brandkit_tpl: str | None = None
    overlay_tpls_json: list[str] | None = Field(
        default_factory=list, sa_column=Column(JSON, nullable=True)
    )
    publish_tpl: str = "default@latest"
    render_profile: str = "1080p_fast"
    is_default: bool = False
    created_at: str = Field(default_factory=_now_iso)


class Font(SQLModel, table=True):
    __tablename__ = "font"

    id: str = Field(default_factory=_new_id, primary_key=True)
    family: str
    style: str = "Regular"
    path: str = ""
    scripts_json: dict[str, Any] | None = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    license: str = ""
    license_ok: bool = False
    source: str = "builtin"
    created_at: str = Field(default_factory=_now_iso)


class PromptVersion(SQLModel, table=True):
    __tablename__ = "prompt_version"

    id: str = Field(default_factory=_new_id, primary_key=True)
    name: str = Field(index=True)
    version: int = 1
    path: str = ""
    content_hash: str = ""
    active: bool = False
    created_by: str = "system"
    created_at: str = Field(default_factory=_now_iso)


class Setting(SQLModel, table=True):
    __tablename__ = "setting"

    key: str = Field(primary_key=True)
    value_json: Any = Field(default=None, sa_column=Column(JSON, nullable=True))
    updated_at: str = Field(default_factory=_now_iso)


class DriveState(SQLModel, table=True):
    __tablename__ = "drive_state"

    id: str = Field(default="singleton", primary_key=True)
    root_folder_id: str | None = None
    start_page_token: str | None = None
    last_sync_at: str | None = None
    last_error: str | None = None
    status: str = "DISABLED"


class DriveFile(SQLModel, table=True):
    __tablename__ = "drive_file"
    __table_args__ = (Index("ix_drive_file_parent_ids_status", "parent_ids_json", "status"),)

    id: str = Field(primary_key=True)
    parent_ids_json: list[str] | None = Field(
        default_factory=list, sa_column=Column(JSON, nullable=True)
    )
    name: str = ""
    mime: str = ""
    size: int = 0
    md5: str | None = None
    modified_time: str | None = None
    path_in_mirror: str = ""
    route: str = "unsorted"  # template | font | doc | source | unsorted
    local_hash: str | None = None
    status: str = "SYNCED"  # SYNCED | STALE | REMOVED | ERROR
    seen_at: str = Field(default_factory=_now_iso)


class BlockerRecord(SQLModel, table=True):
    __tablename__ = "blocker"

    id: str = Field(primary_key=True)
    type: str
    status: str = "OPEN"
    task_id: str
    step: str | None = None
    title: str
    payload_json: dict[str, Any] | None = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    created_at: str = Field(default_factory=_now_iso)
    resolved_at: str | None = None


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: str = Field(default_factory=_new_id, primary_key=True)
    actor: str = "admin"
    action: str
    target: str
    diff_json: dict[str, Any] | None = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    created_at: str = Field(default_factory=_now_iso, index=True)


class FeedbackEvent(SQLModel, table=True):
    __tablename__ = "feedback_event"

    id: str = Field(default_factory=_new_id, primary_key=True)
    clip_id: str = Field(foreign_key="clip.id", ondelete="CASCADE", index=True)
    kind: str = "rating"
    value: str
    created_at: str = Field(default_factory=_now_iso)
