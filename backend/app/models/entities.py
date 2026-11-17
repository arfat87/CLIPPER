from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlmodel import Field, SQLModel


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    TRANSCRIBING = "TRANSCRIBING"
    ANALYZING = "ANALYZING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    RENDERING = "RENDERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Job(SQLModel, table=True):
    id: str = Field(default_factory=_new_id, primary_key=True)
    source_url: str
    platform: str = "youtube"
    title: str = "Untitled Video"
    duration: float = 0.0
    language: str = "auto"
    detected_language: str = "en"
    script_pref: str = "auto"  # auto | latn | deva | beng
    aspect: str = "9:16"
    auto_mode: bool = True
    template_id: str = "bold_pop_yellow"
    max_clips: int = 12
    status: str = JobStatus.QUEUED.value
    stage: str = "QUEUED"
    progress_pct: int = 0
    message: str = "Queued"
    error: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class StageCheckpoint(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    stage: str
    status: str = "DONE"
    artefact_path: str = ""
    attempt: int = 1
    started_at: str = Field(default_factory=_now_iso)
    finished_at: str = Field(default_factory=_now_iso)


class Clip(SQLModel, table=True):
    id: str = Field(default_factory=_new_id, primary_key=True)
    job_id: str = Field(index=True)
    rank: int = 1
    start: float = 0.0
    end: float = 30.0
    duration: float = 30.0
    score: float = 0.0
    score_breakdown_json: str = "{}"
    title: str = ""
    hook_line: str = ""
    hashtags_json: str = "[]"
    reason: str = ""
    transcript_snippet: str = ""
    words_json: str = "[]"
    layout: str = "fill"       # fill | fit_blur
    template_id: str = "bold_pop_yellow"
    aspect: str = "9:16"
    status: str = "PROPOSED"   # PROPOSED | RENDERING | RENDERED | FAILED
    render_mp4_path: str | None = None
    render_ass_path: str | None = None
    render_srt_path: str | None = None
    thumb_path: str | None = None
    created_at: str = Field(default_factory=_now_iso)
