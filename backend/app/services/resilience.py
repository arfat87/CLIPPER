from __future__ import annotations

import errno
import random
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from sqlmodel import Session, col, select

from backend.app.config import ROOT_DIR, get_settings
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
from backend.app.services.orchestrator import (
    STAGES,
    FatalStageError,
    RetryableError,
    clear_job_events,
    retry_job,
)
from backend.app.services.storage import StorageBackend, get_storage
from hitl.models import BlockerRequired

T = TypeVar("T")


class ErrorCode(str, Enum):
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    GEO_BLOCKED = "GEO_BLOCKED"
    PRIVATE_VIDEO = "PRIVATE_VIDEO"
    UNSUPPORTED_URL = "UNSUPPORTED_URL"
    TOO_LONG = "TOO_LONG"
    DISK_FULL = "DISK_FULL"
    ASR_FAILED = "ASR_FAILED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    NO_CLIPS_FOUND = "NO_CLIPS_FOUND"
    TEMPLATE_INVALID = "TEMPLATE_INVALID"
    FONT_MISSING = "FONT_MISSING"
    RENDER_FAILED = "RENDER_FAILED"
    DRIVE_AUTH = "DRIVE_AUTH"
    DRIVE_NOT_FOUND = "DRIVE_NOT_FOUND"
    BLOCKER_REQUIRED = "BLOCKER_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_ERROR = "VALIDATION_ERROR"


VALID_ERROR_CODES: frozenset[str] = frozenset(c.value for c in ErrorCode)

RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        ErrorCode.LLM_UNAVAILABLE.value,
        ErrorCode.RATE_LIMITED.value,
        ErrorCode.ASR_FAILED.value,
        ErrorCode.RENDER_FAILED.value,
        ErrorCode.DISK_FULL.value,
    }
)

ACTIVE_WORKER_STATUSES: frozenset[str] = frozenset(
    {
        JobStatus.FETCHING.value,
        JobStatus.PREPARING.value,
        JobStatus.DOWNLOADING.value,
        JobStatus.TRANSCRIBING.value,
        JobStatus.ANALYZING.value,
        JobStatus.SELECTING.value,
        JobStatus.RENDERING.value,
        JobStatus.PACKAGING.value,
    }
)

EVICTABLE_JOB_STATUSES: frozenset[str] = frozenset(
    {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }
)


@dataclass(frozen=True)
class MappedStageError:
    code: ErrorCode
    message: str
    retryable: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": dict(self.details),
                "retryable": self.retryable,
            }
        }


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 5.0
    factor: float = 4.0
    max_delay_s: float = 60.0
    jitter_ratio: float = 0.2
    explicit_delays: tuple[float, ...] | None = (5.0, 20.0, 60.0)

    def delay_for_attempt(
        self,
        attempt: int,
        *,
        rng: Callable[[float, float], float] | None = None,
    ) -> float:
        """Compute backoff delay in seconds for 1-based failed attempt number."""
        idx = max(0, int(attempt) - 1)
        if self.explicit_delays and idx < len(self.explicit_delays):
            base = float(self.explicit_delays[idx])
        else:
            base = min(self.max_delay_s, self.base_delay_s * (self.factor**idx))
        if self.jitter_ratio <= 0.0:
            return round(max(0.0, base), 4)
        rand_fn = rng or random.uniform
        delta = base * self.jitter_ratio
        jittered = base + rand_fn(-delta, delta)
        return round(max(0.0, min(self.max_delay_s * (1.0 + self.jitter_ratio), jittered)), 4)


@dataclass
class CleanupSummary:
    evicted_job_ids: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    orphaned_removed: int = 0
    remaining_usage_bytes: int = 0


def _coerce_error_code(raw_code: str | None, default: ErrorCode) -> ErrorCode:
    if raw_code:
        clean = raw_code.strip().upper()
        if clean in VALID_ERROR_CODES:
            return ErrorCode(clean)
    return default


def map_stage_error(
    exc: BaseException,
    *,
    stage: str | None = None,
) -> MappedStageError:
    """Deterministically map any stage/pipeline exception to a contract ErrorCode envelope."""
    msg = str(exc).strip() or exc.__class__.__name__
    msg_lower = msg.lower()
    stage_norm = (stage or "").strip().lower()

    if isinstance(exc, BlockerRequired):
        return MappedStageError(
            code=ErrorCode.BLOCKER_REQUIRED,
            message=msg,
            retryable=True,
            details={
                "blocker_id": exc.blocker.id,
                "blocker_type": exc.blocker.type.value,
                "stage": stage_norm or None,
            },
        )

    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC:
        return MappedStageError(
            code=ErrorCode.DISK_FULL,
            message=f"Insufficient disk space: {msg}",
            retryable=True,
            details={"errno": errno.ENOSPC, "stage": stage_norm or None},
        )

    if isinstance(exc, RetryableError):
        raw_code = getattr(exc, "code", "")
        mapped = _classify_code_from_text_or_stage(raw_code, msg_lower, stage_norm, default_retryable=True)
        return MappedStageError(
            code=mapped,
            message=msg,
            retryable=True,
            details={**getattr(exc, "details", {}), "raw_code": raw_code, "stage": stage_norm or None},
        )

    if isinstance(exc, FatalStageError):
        raw_code = getattr(exc, "code", "")
        mapped = _classify_code_from_text_or_stage(raw_code, msg_lower, stage_norm, default_retryable=False)
        is_retryable = mapped.value in RETRYABLE_ERROR_CODES and mapped not in (
            ErrorCode.VALIDATION_ERROR,
            ErrorCode.UNSUPPORTED_URL,
            ErrorCode.PRIVATE_VIDEO,
            ErrorCode.GEO_BLOCKED,
            ErrorCode.TOO_LONG,
        )
        return MappedStageError(
            code=mapped,
            message=msg,
            retryable=is_retryable,
            details={**getattr(exc, "details", {}), "raw_code": raw_code, "stage": stage_norm or None},
        )

    code_attr = getattr(exc, "code", None)
    raw_attr = str(code_attr) if code_attr else ""
    is_transient = isinstance(exc, (TimeoutError, ConnectionError)) or any(
        tok in msg_lower
        for tok in (
            "timeout",
            "timed out",
            "connection reset",
            "temporarily unavailable",
            "502",
            "503",
            "504",
            "429",
            "rate limit",
            "network",
        )
    )
    mapped = _classify_code_from_text_or_stage(
        raw_attr,
        msg_lower,
        stage_norm,
        default_retryable=is_transient,
    )
    retryable = is_transient or mapped.value in RETRYABLE_ERROR_CODES
    return MappedStageError(
        code=mapped,
        message=msg,
        retryable=retryable,
        details={"stage": stage_norm or None, "exception": exc.__class__.__name__},
    )


def _classify_code_from_text_or_stage(
    raw_code: str,
    msg_lower: str,
    stage_norm: str,
    *,
    default_retryable: bool,
) -> ErrorCode:
    clean_code = (raw_code or "").strip().upper()
    if clean_code in VALID_ERROR_CODES:
        return ErrorCode(clean_code)

    if "login" in msg_lower or "sign in" in msg_lower or "cookie" in msg_lower:
        return ErrorCode.LOGIN_REQUIRED
    if "geo" in msg_lower or "country" in msg_lower or "region" in msg_lower:
        return ErrorCode.GEO_BLOCKED
    if "private" in msg_lower or "members-only" in msg_lower:
        return ErrorCode.PRIVATE_VIDEO
    if "unsupported url" in msg_lower or "invalid url" in msg_lower:
        return ErrorCode.UNSUPPORTED_URL
    if "too long" in msg_lower or "max_video_hours" in msg_lower or "duration exceeds" in msg_lower:
        return ErrorCode.TOO_LONG
    if "disk full" in msg_lower or "no space left" in msg_lower or "enospc" in msg_lower:
        return ErrorCode.DISK_FULL
    if "rate limit" in msg_lower or "429" in msg_lower or "too many requests" in msg_lower:
        return ErrorCode.RATE_LIMITED
    if "template" in msg_lower and ("invalid" in msg_lower or "schema" in msg_lower):
        return ErrorCode.TEMPLATE_INVALID
    if "font" in msg_lower and ("missing" in msg_lower or "not found" in msg_lower):
        return ErrorCode.FONT_MISSING
    if "no clips" in msg_lower or "0 candidates" in msg_lower:
        return ErrorCode.NO_CLIPS_FOUND
    if "drive" in msg_lower and ("auth" in msg_lower or "401" in msg_lower or "403" in msg_lower):
        return ErrorCode.DRIVE_AUTH
    if "drive" in msg_lower and ("not found" in msg_lower or "404" in msg_lower):
        return ErrorCode.DRIVE_NOT_FOUND
    if "llm" in msg_lower or "gemini" in msg_lower or "ollama" in msg_lower:
        return ErrorCode.LLM_UNAVAILABLE
    if "asr" in msg_lower or "whisper" in msg_lower or stage_norm == "transcribe":
        return ErrorCode.ASR_FAILED
    if "ffmpeg" in msg_lower or "render" in msg_lower or stage_norm in ("prepare", "render"):
        return ErrorCode.RENDER_FAILED
    if stage_norm == "select":
        return ErrorCode.NO_CLIPS_FOUND if not default_retryable else ErrorCode.LLM_UNAVAILABLE
    if stage_norm == "analyze":
        return ErrorCode.LLM_UNAVAILABLE
    if stage_norm == "fetch":
        return ErrorCode.RATE_LIMITED if default_retryable else ErrorCode.UNSUPPORTED_URL
    return ErrorCode.LLM_UNAVAILABLE if default_retryable else ErrorCode.VALIDATION_ERROR


def execute_with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    stage: str | None = None,
    is_retryable: Callable[[BaseException], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    rng: Callable[[float, float], float] | None = None,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """Execute `fn` with exponential backoff and jitter on retryable errors."""
    pol = policy or RetryPolicy()
    max_tries = max(1, int(pol.max_attempts))
    attempt = 0

    while True:
        attempt += 1
        try:
            return fn()
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit, BlockerRequired)):
                raise
            should_retry = (
                is_retryable(exc)
                if is_retryable is not None
                else (
                    isinstance(exc, RetryableError)
                    or (
                        not isinstance(exc, FatalStageError)
                        and map_stage_error(exc, stage=stage).retryable
                    )
                )
            )
            if not should_retry or attempt >= max_tries:
                raise
            delay = pol.delay_for_attempt(attempt, rng=rng)
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            if delay > 0:
                sleep_fn(delay)


def compute_dir_size_bytes(path: Path) -> int:
    """Compute recursive byte size of a directory."""
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += int(child.stat().st_size)
            except OSError:
                continue
    return total


def clean_orphaned_temp_files(
    data_dir: Path | None = None,
    *,
    max_age_seconds: float = 0.0,
    now_ts: float | None = None,
) -> int:
    """Remove orphaned `.tmp_*`, `.stage_*`, `*.tmp`, and staging directories under `data_dir`."""
    base = data_dir or get_settings().data_dir
    if not base.exists():
        return 0

    now = now_ts if now_ts is not None else time.time()
    removed = 0
    candidates: list[Path] = []
    for p in base.rglob("*"):
        name = p.name
        if (
            name.startswith(".tmp_")
            or name.startswith(".stage_")
            or name.endswith(".tmp")
            or name == ".staging"
        ):
            candidates.append(p)

    # Sort deepest paths first so children don't interfere with parent directory removal
    for target in sorted(candidates, key=lambda x: len(x.parts), reverse=True):
        if not target.exists():
            continue
        try:
            mtime = target.stat().st_mtime
            if max_age_seconds > 0.0 and (now - mtime) < max_age_seconds:
                continue
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def enforce_disk_quota_and_evict_lru(
    session: Session,
    *,
    storage: StorageBackend | None = None,
    max_total_bytes: int | None = None,
    min_free_bytes: int | None = None,
    protect_job_ids: Sequence[str] = (),
    delete_db_records: bool = False,
) -> CleanupSummary:
    """Enforce disk quota by evicting least-recently-updated terminal job workspaces (LRU)."""
    store = storage or get_storage()
    settings = get_settings()
    data_dir = settings.data_dir
    jobs_root = data_dir / "jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)

    orphaned_count = clean_orphaned_temp_files(data_dir)
    current_usage = compute_dir_size_bytes(jobs_root)
    protected = set(protect_job_ids)

    if min_free_bytes is not None and min_free_bytes > 0:
        try:
            free_now = shutil.disk_usage(data_dir if data_dir.exists() else ROOT_DIR).free
        except OSError:
            free_now = min_free_bytes
        if free_now < min_free_bytes and max_total_bytes is None:
            deficit = min_free_bytes - free_now
            max_total_bytes = max(0, current_usage - deficit)

    if max_total_bytes is None or current_usage <= max_total_bytes:
        if min_free_bytes is not None and min_free_bytes > 0:
            try:
                free_check = shutil.disk_usage(data_dir if data_dir.exists() else ROOT_DIR).free
            except OSError:
                free_check = min_free_bytes
            if free_check < min_free_bytes:
                raise FatalStageError(
                    ErrorCode.DISK_FULL.value,
                    f"Free disk space ({free_check} B) is below required minimum ({min_free_bytes} B)",
                )
        return CleanupSummary(
            evicted_job_ids=[],
            freed_bytes=0,
            orphaned_removed=orphaned_count,
            remaining_usage_bytes=current_usage,
        )

    candidates = list(
        session.exec(
            select(Job)
            .where(col(Job.deleted_at).is_(None))
            .where(col(Job.status).in_(list(EVICTABLE_JOB_STATUSES)))
            .order_by(col(Job.updated_at).asc(), col(Job.created_at).asc(), col(Job.id).asc())
        ).all()
    )

    evicted_ids: list[str] = []
    freed_total = 0

    for job in candidates:
        if current_usage <= max_total_bytes:
            break
        if job.id in protected:
            continue
        jdir = store.job_dir(job.id)
        jbytes = compute_dir_size_bytes(jdir)
        store.delete_job(job.id)
        clear_job_events(job.id)

        if delete_db_records:
            for c in session.exec(select(Clip).where(Clip.job_id == job.id)).all():
                for fb in session.exec(select(FeedbackEvent).where(FeedbackEvent.clip_id == c.id)).all():
                    session.delete(fb)
                for ren in session.exec(select(Render).where(Render.clip_id == c.id)).all():
                    session.delete(ren)
                for pp in session.exec(select(PublishPack).where(PublishPack.clip_id == c.id)).all():
                    session.delete(pp)
                session.delete(c)
            for cp in session.exec(select(StageCheckpoint).where(StageCheckpoint.job_id == job.id)).all():
                session.delete(cp)
            for tr in session.exec(select(Transcript).where(Transcript.job_id == job.id)).all():
                session.delete(tr)
            session.delete(job)
        else:
            now_iso = datetime.now(UTC).isoformat()
            job.deleted_at = now_iso
            job.updated_at = now_iso
            session.add(job)

        evicted_ids.append(job.id)
        freed_total += jbytes
        current_usage = max(0, current_usage - jbytes)

    session.commit()
    remaining = compute_dir_size_bytes(jobs_root)
    if remaining > max_total_bytes:
        raise FatalStageError(
            ErrorCode.DISK_FULL.value,
            f"Disk quota exceeded ({remaining} B > {max_total_bytes} B) after evicting {len(evicted_ids)} terminal jobs",
            details={"usage_bytes": remaining, "quota_bytes": max_total_bytes},
        )

    return CleanupSummary(
        evicted_job_ids=evicted_ids,
        freed_bytes=freed_total,
        orphaned_removed=orphaned_count,
        remaining_usage_bytes=remaining,
    )


def recover_interrupted_jobs(
    session: Session,
    *,
    storage: StorageBackend | None = None,
    enqueue: bool = False,
    run_inline: bool = False,
) -> list[str]:
    """Recover jobs stranded in an active worker status after a worker kill/crash."""
    store = storage or get_storage()
    settings = get_settings()
    clean_orphaned_temp_files(settings.data_dir)

    stranded = list(
        session.exec(
            select(Job)
            .where(col(Job.deleted_at).is_(None))
            .where(col(Job.status).in_(list(ACTIVE_WORKER_STATUSES)))
            .order_by(col(Job.created_at).asc(), col(Job.id).asc())
        ).all()
    )

    recovered_ids: list[str] = []
    now_iso = datetime.now(UTC).isoformat()

    for job in stranded:
        jdir = store.job_dir(job.id)
        clean_orphaned_temp_files(jdir)

        checkpoints = list(
            session.exec(
                select(StageCheckpoint)
                .where(StageCheckpoint.job_id == job.id)
                .order_by(col(StageCheckpoint.id).asc())
            ).all()
        )
        done_stages: set[str] = set()
        for cp in checkpoints:
            if cp.status == "RUNNING":
                cp.status = "FAILED"
                cp.finished_at = now_iso
                cp.error_json = {"code": "WORKER_INTERRUPTED", "message": "Worker process interrupted mid-stage"}
                session.add(cp)
            elif cp.status == "DONE":
                done_stages.add(cp.stage)

        resume_stage = next((st for st in STAGES if st not in done_stages), STAGES[0])
        job.status = JobStatus.QUEUED.value
        job.stage = resume_stage
        job.message = f"Recovered from worker interruption; resuming at {resume_stage}"
        job.updated_at = now_iso
        session.add(job)
        recovered_ids.append(job.id)

    session.commit()

    if enqueue or run_inline:
        for jid in recovered_ids:
            job_row = session.get(Job, jid)
            from_st = job_row.stage if job_row and job_row.stage in STAGES else None
            if run_inline:
                from backend.app.services.orchestrator import run_job_v2

                run_job_v2(jid, from_stage=from_st)
            elif enqueue:
                retry_job(jid, from_stage=from_st, enqueue=True)

    return recovered_ids


__all__ = [
    "ACTIVE_WORKER_STATUSES",
    "EVICTABLE_JOB_STATUSES",
    "RETRYABLE_ERROR_CODES",
    "VALID_ERROR_CODES",
    "CleanupSummary",
    "ErrorCode",
    "MappedStageError",
    "RetryPolicy",
    "clean_orphaned_temp_files",
    "compute_dir_size_bytes",
    "enforce_disk_quota_and_evict_lru",
    "execute_with_retry",
    "map_stage_error",
    "recover_interrupted_jobs",
]
