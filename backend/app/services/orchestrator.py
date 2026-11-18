from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from backend.app import config as config_mod
from backend.app.config import Settings, get_settings
from backend.app.db import get_engine
from backend.app.models.entities import (
    BlockerRecord,
    Clip,
    Job,
    JobStatus,
    PublishPack,
    Render,
    StageCheckpoint,
    Transcript,
)
from backend.app.services.queue import (
    clear_cancel,
    enqueue_job,
    is_cancel_requested,
    register_process,
    request_cancel,
    terminate_job_processes,
    unregister_process,
)
from backend.app.services.storage import StorageBackend, get_storage
from hitl.models import BlockerRequired
from hitl.raise_blocker import create_or_get_blocker

STAGES: tuple[str, ...] = (
    "fetch",
    "prepare",
    "transcribe",
    "analyze",
    "select",
    "render",
    "package",
)

DEFAULT_STAGE_WEIGHTS: dict[str, int] = {
    "fetch": 10,
    "prepare": 5,
    "transcribe": 30,
    "analyze": 15,
    "select": 15,
    "render": 20,
    "package": 5,
}

STAGE_TO_JOB_STATUS: dict[str, str] = {
    "fetch": JobStatus.FETCHING.value,
    "prepare": JobStatus.PREPARING.value,
    "transcribe": JobStatus.TRANSCRIBING.value,
    "analyze": JobStatus.ANALYZING.value,
    "select": JobStatus.SELECTING.value,
    "render": JobStatus.RENDERING.value,
    "package": JobStatus.PACKAGING.value,
}

DEFAULT_RETRY_BACKOFFS: tuple[float, ...] = (5.0, 20.0, 60.0)

_SAFE_JOB_UPDATE_FIELDS = {
    "platform",
    "title",
    "duration",
    "duration_s",
    "detected_language",
    "script",
    "genre",
}


class RetryableError(RuntimeError):
    """Transient stage error eligible for automatic retry with backoff."""

    def __init__(
        self,
        code: str = "RETRYABLE_ERROR",
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message or code)


class FatalStageError(RuntimeError):
    """Non-retryable stage error that transitions the job to FAILED."""

    def __init__(
        self,
        code: str = "STAGE_FAILED",
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message or code)


class JobCancelledError(RuntimeError):
    """Raised when cooperative job cancellation is triggered."""


@dataclass
class JobEvent:
    id: int
    job_id: str
    event: str
    data: dict[str, Any]
    created_at: str


_events_lock = threading.RLock()
_events_cond = threading.Condition(_events_lock)
_job_events: dict[str, list[JobEvent]] = {}
_job_event_seq: dict[str, int] = {}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _events_file_for(job_dir: Path | None) -> Path | None:
    if job_dir is None or not job_dir.exists():
        return None
    return job_dir / ".events.jsonl"


def record_job_event(
    job_id: str,
    event: str,
    data: dict[str, Any],
    *,
    job_dir: Path | None = None,
) -> JobEvent:
    """Append a sequence-numbered SSE-friendly event for a job."""
    with _events_cond:
        seq = _job_event_seq.get(job_id, 0) + 1
        _job_event_seq[job_id] = seq
        ev = JobEvent(
            id=seq,
            job_id=job_id,
            event=event,
            data={"job_id": job_id, **data},
            created_at=_now_iso(),
        )
        _job_events.setdefault(job_id, []).append(ev)

        ev_file = _events_file_for(job_dir)
        if ev_file is not None:
            line = json.dumps(
                {
                    "id": ev.id,
                    "job_id": ev.job_id,
                    "event": ev.event,
                    "data": ev.data,
                    "created_at": ev.created_at,
                },
                ensure_ascii=False,
            )
            with ev_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

        _events_cond.notify_all()
        return ev


def _parse_after_id(after_id: int | str | None) -> int:
    if after_id is None:
        return 0
    if isinstance(after_id, int):
        return max(0, after_id)
    raw = str(after_id).strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def get_job_events(
    job_id: str,
    *,
    after_id: int | str | None = None,
    job_dir: Path | None = None,
) -> list[JobEvent]:
    """Retrieve recorded events for a job strictly after `after_id` (Last-Event-ID)."""
    after_seq = _parse_after_id(after_id)
    with _events_lock:
        events = list(_job_events.get(job_id, []))
        if not events and job_dir is not None:
            ev_file = job_dir / ".events.jsonl"
            if ev_file.exists():
                loaded: list[JobEvent] = []
                for line in ev_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        loaded.append(
                            JobEvent(
                                id=int(raw["id"]),
                                job_id=str(raw["job_id"]),
                                event=str(raw["event"]),
                                data=dict(raw["data"]),
                                created_at=str(raw["created_at"]),
                            )
                        )
                    except (KeyError, ValueError, TypeError):
                        continue
                if loaded:
                    _job_events[job_id] = loaded
                    _job_event_seq[job_id] = max(_job_event_seq.get(job_id, 0), loaded[-1].id)
                    events = list(loaded)

    return [ev for ev in events if ev.id > after_seq]


def wait_for_job_events(
    job_id: str,
    *,
    after_id: int | str | None = None,
    timeout: float = 15.0,
    job_dir: Path | None = None,
) -> list[JobEvent]:
    """Wait up to `timeout` seconds for new events strictly after `after_id`."""
    after_seq = _parse_after_id(after_id)
    deadline = time.monotonic() + max(0.0, timeout)
    with _events_cond:
        while True:
            events = get_job_events(job_id, after_id=after_seq, job_dir=job_dir)
            if events:
                return events
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            _events_cond.wait(timeout=remaining)


def clear_job_events(job_id: str) -> None:
    """Clear in-memory recorded events for a job."""
    with _events_cond:
        _job_events.pop(job_id, None)
        _job_event_seq.pop(job_id, None)


def format_sse_event(ev: JobEvent) -> str:
    """Format a JobEvent into standard Server-Sent Events wire format."""
    payload = json.dumps(ev.data, ensure_ascii=False)
    return f"id: {ev.id}\nevent: {ev.event}\ndata: {payload}\n\n"


def compute_overall_progress(
    stage: str,
    stage_pct: float,
    *,
    stage_weights: Mapping[str, int] | None = None,
) -> int:
    """Compute overall [0, 100] job percentage from weighted stages."""
    weights = dict(DEFAULT_STAGE_WEIGHTS)
    if stage_weights:
        weights.update(stage_weights)
    total_weight = max(1, sum(max(0, int(weights.get(s, 0))) for s in STAGES))
    if stage not in STAGES:
        return 0
    idx = STAGES.index(stage)
    completed = sum(max(0, int(weights.get(s, 0))) for s in STAGES[:idx])
    clamped = min(100.0, max(0.0, float(stage_pct)))
    current = max(0, int(weights.get(stage, 0))) * (clamped / 100.0)
    pct = round(((completed + current) / total_weight) * 100.0)
    return min(100, max(0, pct))


def hash_path(path: Path) -> str:
    """Compute deterministic sha256 digest for a file or directory tree."""
    if not path.exists():
        return "missing"
    hasher = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    files = sorted(
        p for p in path.rglob("*") if p.is_file() and not p.name.startswith(".tmp_")
    )
    for fpath in files:
        rel = fpath.relative_to(path).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        with fpath.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                hasher.update(chunk)
        hasher.update(b"\x00")
    return hasher.hexdigest()


def compute_input_hash(
    *,
    stage: str,
    upstream_hashes: Mapping[str, str] | Sequence[str] | None = None,
    stage_config: Mapping[str, Any] | None = None,
    versions: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Compute deterministic sha256 input hash for a pipeline stage."""
    payload = {
        "stage": stage,
        "upstream": upstream_hashes if upstream_hashes is not None else {},
        "config": stage_config if stage_config is not None else {},
        "versions": versions if versions is not None else {},
        "extra": extra if extra is not None else {},
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class StageResult:
    primary_artefact: str | Path | None = None
    outputs: dict[str, str] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    job_updates: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    on_commit: Callable[[Session, Job, dict[str, Path]], None] | None = None


@dataclass
class StageContext:
    job: Job
    job_id: str
    stage: str
    attempt: int
    job_dir: Path
    staging_dir: Path
    settings: Settings
    stage_config: dict[str, Any]
    input_hash: str
    db_engine: Engine
    _progress_cb: Callable[[float, str, str | None], None]
    _warning_cb: Callable[[str], None]
    _cancel_check_cb: Callable[[], bool]

    def emit_progress(
        self,
        pct: float,
        message: str,
        *,
        clip_id: str | None = None,
    ) -> None:
        self._progress_cb(pct, message, clip_id)

    def emit_warning(self, message: str) -> None:
        self._warning_cb(message)

    def is_cancelled(self) -> bool:
        return self._cancel_check_cb()

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelledError(f"Job {self.job_id} cancelled during stage {self.stage}")

    def register_process(self, proc_or_pid: subprocess.Popen[Any] | int) -> int:
        return register_process(self.job_id, proc_or_pid)

    def unregister_process(self, proc_or_pid: subprocess.Popen[Any] | int) -> None:
        unregister_process(self.job_id, proc_or_pid)


StageHandler = Callable[[StageContext], StageResult | dict[str, Any] | Path | str | None]


@dataclass
class StageSpec:
    name: str
    handler: StageHandler
    version: str = "1"
    config_key: str | None = None


class StageRegistry:
    """Registry mapping canonical Spec v2 stage names to stage handlers."""

    def __init__(self, stages: Mapping[str, StageSpec] | None = None) -> None:
        self._stages: dict[str, StageSpec] = dict(stages or {})

    def register(
        self,
        stage: str,
        handler: StageHandler,
        *,
        version: str = "1",
        config_key: str | None = None,
    ) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage {stage!r}; expected one of {STAGES}")
        self._stages[stage] = StageSpec(
            name=stage,
            handler=handler,
            version=str(version),
            config_key=config_key,
        )

    def get(self, stage: str) -> StageSpec:
        if stage not in self._stages:
            raise KeyError(f"No handler registered for stage {stage!r}")
        return self._stages[stage]

    def has(self, stage: str) -> bool:
        return stage in self._stages

    def clone(self) -> StageRegistry:
        return StageRegistry(
            {
                k: StageSpec(
                    name=v.name,
                    handler=v.handler,
                    version=v.version,
                    config_key=v.config_key,
                )
                for k, v in self._stages.items()
            }
        )


def _link_or_copy(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if dst.exists() or dst.is_symlink():
        return
    try:
        os.symlink(src, dst)
    except OSError:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _create_work_view(ctx: StageContext, readonly_subdirs: Sequence[str], writable_subdirs: Sequence[str]) -> Path:
    work_view = ctx.staging_dir / ".work"
    work_view.mkdir(parents=True, exist_ok=True)
    for sub in readonly_subdirs:
        src = ctx.job_dir / sub
        if src.exists():
            _link_or_copy(src, work_view / sub)
    for sub in writable_subdirs:
        real_sub = ctx.staging_dir / sub
        real_sub.mkdir(parents=True, exist_ok=True)
        link_sub = work_view / sub
        if not link_sub.exists() and not link_sub.is_symlink():
            _link_or_copy(real_sub, link_sub)
    return work_view


def _default_fetch_stage(ctx: StageContext) -> StageResult:
    from backend.pipeline.ingest.downloader import (
        IngestError,
        detect_platform,
        download_and_normalize,
    )

    ctx.check_cancelled()
    source_input = ctx.job.source_url or ctx.job.source_ref or ""
    if not source_input:
        raise FatalStageError("UNSUPPORTED_URL", "Job has no source_url or source_ref")

    ctx.emit_progress(10.0, "Downloading & normalizing source media...")
    try:
        meta = download_and_normalize(source_input, ctx.staging_dir)
    except IngestError as exc:
        if exc.retryable:
            raise RetryableError(exc.code, str(exc)) from exc
        raise FatalStageError(exc.code, str(exc)) from exc

    ctx.check_cancelled()
    duration = float(meta.get("duration") or 0.0)
    return StageResult(
        primary_artefact="source/source.mp4",
        metrics={"duration_s": duration, "platform": meta.get("platform")},
        job_updates={
            "platform": meta.get("platform", detect_platform(source_input)),
            "title": meta.get("title") or ctx.job.title,
            "duration": duration,
            "duration_s": duration,
        },
    )


def _default_prepare_stage(ctx: StageContext) -> StageResult:
    ctx.check_cancelled()
    source_mp4 = ctx.job_dir / "source" / "source.mp4"
    if not source_mp4.exists():
        raise FatalStageError("SOURCE_MISSING", f"Missing source media: {source_mp4}")

    meta_path = ctx.job_dir / "source" / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    audio_wav = ctx.job_dir / "audio" / "audio.wav"
    if not audio_wav.exists():
        staged_audio_dir = ctx.staging_dir / "audio"
        staged_audio_dir.mkdir(parents=True, exist_ok=True)
        staged_wav = staged_audio_dir / "audio.wav"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_mp4),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(staged_wav),
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ctx.register_process(proc)
        try:
            _stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                raise FatalStageError(
                    "PREPARE_FAILED",
                    f"ffmpeg audio extraction failed: {stderr.decode('utf-8', errors='ignore')[:300]}",
                )
        finally:
            ctx.unregister_process(proc)

    ctx.check_cancelled()
    prepared_dir = ctx.staging_dir / "source"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    prepared_path = prepared_dir / "prepared.json"
    prepared_payload = {
        "source_mp4": str(source_mp4),
        "audio_wav": str(audio_wav if audio_wav.exists() else (ctx.job_dir / "audio" / "audio.wav")),
        "duration_s": float(meta.get("duration") or ctx.job.duration or 0.0),
        "source_sha256": hash_path(source_mp4),
    }
    prepared_path.write_text(json.dumps(prepared_payload, indent=2), encoding="utf-8")
    ctx.emit_progress(100.0, "Source media verified and normalized.")
    return StageResult(
        primary_artefact="source/prepared.json",
        metrics={"duration_s": prepared_payload["duration_s"]},
    )


def _default_transcribe_stage(ctx: StageContext) -> StageResult:
    from backend.pipeline.asr.engine import transcribe_job

    ctx.check_cancelled()
    ctx.emit_progress(15.0, "Transcribing audio with word-level timestamps...")
    work_view = _create_work_view(ctx, readonly_subdirs=("source", "audio"), writable_subdirs=("asr",))
    try:
        transcript = transcribe_job(
            work_view,
            requested_lang=ctx.job.language,
            script_pref=ctx.job.script_pref,
        )
    finally:
        shutil.rmtree(work_view, ignore_errors=True)

    ctx.check_cancelled()
    segments = transcript.get("segments", [])
    confs = [
        float(w.get("p", 0.9))
        for seg in segments
        for w in seg.get("words", [])
        if isinstance(w, dict)
    ]
    mean_conf = round(sum(confs) / len(confs), 4) if confs else None

    def _on_commit(session: Session, job: Job, committed: dict[str, Path]) -> None:
        tr_path = str(committed.get("asr/transcript.json", ctx.job_dir / "asr" / "transcript.json"))
        existing = session.exec(select(Transcript).where(Transcript.job_id == job.id)).first()
        if existing is None:
            existing = Transcript(
                job_id=job.id,
                language=str(transcript.get("language", "en")),
                script=str(transcript.get("script", "latn")),
                engine=str(transcript.get("engine", "heuristic_fallback")),
                model_id=ctx.settings.asr_model_size,
                path_json=tr_path,
                mean_conf=mean_conf,
                edited=False,
            )
        else:
            existing.language = str(transcript.get("language", existing.language))
            existing.script = str(transcript.get("script", existing.script))
            existing.engine = str(transcript.get("engine", existing.engine))
            existing.path_json = tr_path
            existing.mean_conf = mean_conf
            existing.updated_at = _now_iso()
        session.add(existing)

    ctx.emit_progress(100.0, "Transcription complete.")
    return StageResult(
        primary_artefact="asr/transcript.json",
        metrics={"segment_count": len(segments), "mean_conf": mean_conf},
        job_updates={
            "detected_language": str(transcript.get("language", "en")),
            "script": str(transcript.get("script", "latn")),
        },
        on_commit=_on_commit,
    )


def _default_analyze_stage(ctx: StageContext) -> StageResult:
    from backend.pipeline.highlights.ranker import analyze_and_rank_highlights

    ctx.check_cancelled()
    transcript_path = ctx.job_dir / "asr" / "transcript.json"
    if not transcript_path.exists():
        raise FatalStageError("TRANSCRIPT_MISSING", f"Missing transcript: {transcript_path}")
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

    ctx.emit_progress(25.0, "Analyzing highlight candidates and scoring moments...")
    work_view = _create_work_view(ctx, readonly_subdirs=("source", "asr"), writable_subdirs=("highlights",))
    try:
        candidates = analyze_and_rank_highlights(
            job_dir=work_view,
            transcript=transcript,
            max_clips=max(int(ctx.job.max_clips or 12), 20),
        )
    finally:
        shutil.rmtree(work_view, ignore_errors=True)

    ctx.check_cancelled()
    analysis_path = ctx.staging_dir / "highlights" / "analysis.json"
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_text(
        json.dumps(
            {
                "language": transcript.get("language", "en"),
                "candidates": candidates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    # Remove intermediate clips.json produced by ranker inside staging_dir so select owns clips.json
    temp_clips = ctx.staging_dir / "highlights" / "clips.json"
    if temp_clips.exists():
        temp_clips.unlink()

    ctx.emit_progress(100.0, f"Scored {len(candidates)} candidate highlight moments.")
    return StageResult(
        primary_artefact="highlights/analysis.json",
        metrics={"candidate_count": len(candidates)},
    )


def _default_select_stage(ctx: StageContext) -> StageResult:
    from backend.pipeline.highlights.ranker import analyze_and_rank_highlights

    ctx.check_cancelled()
    analysis_path = ctx.job_dir / "highlights" / "analysis.json"
    if analysis_path.exists():
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        candidates = list(analysis.get("candidates", []))
    else:
        transcript_path = ctx.job_dir / "asr" / "transcript.json"
        if not transcript_path.exists():
            raise FatalStageError("TRANSCRIPT_MISSING", f"Missing transcript: {transcript_path}")
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        work_view = _create_work_view(ctx, readonly_subdirs=("source", "asr"), writable_subdirs=("highlights",))
        try:
            candidates = analyze_and_rank_highlights(
                job_dir=work_view,
                transcript=transcript,
                max_clips=max(1, int(ctx.job.max_clips or 12)),
            )
        finally:
            shutil.rmtree(work_view, ignore_errors=True)

    limit = max(1, int(ctx.job.max_clips or 12))
    selected = candidates[:limit]
    for idx, item in enumerate(selected, start=1):
        item["rank"] = idx

    clips_dir = ctx.staging_dir / "highlights"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips_json = clips_dir / "clips.json"
    clips_json.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")

    def _on_commit(session: Session, job: Job, _committed: dict[str, Path]) -> None:
        old_clips = list(session.exec(select(Clip).where(Clip.job_id == job.id)).all())
        for old in old_clips:
            session.delete(old)
        session.flush()
        for item in selected:
            start_val = float(item["start"])
            end_val = float(item["end"])
            c = Clip(
                job_id=job.id,
                rank=int(item["rank"]),
                start=start_val,
                end=end_val,
                start_s=start_val,
                end_s=end_val,
                duration=float(item.get("duration", max(1.0, end_val - start_val))),
                score=float(item.get("score", 0.0)),
                score_mode="hybrid",
                score_breakdown_json=json.dumps(item.get("score_breakdown", {})),
                title=str(item.get("title", "")),
                hook_line=str(item.get("hook_line", "")),
                hashtags_json=json.dumps(item.get("hashtags", [])),
                reason=str(item.get("reason", "")),
                transcript_snippet=str(item.get("transcript_snippet", "")),
                words_json=json.dumps(item.get("words", []), ensure_ascii=False),
                layout="fill",
                template_id=job.template_id,
                preset_id=job.preset_id,
                aspect=job.aspect,
                status="PROPOSED",
            )
            session.add(c)

    ctx.emit_progress(100.0, f"Selected {len(selected)} clips.")
    return StageResult(
        primary_artefact="highlights/clips.json",
        metrics={"selected_count": len(selected)},
        on_commit=_on_commit,
    )


def _default_render_stage(ctx: StageContext) -> StageResult:
    from backend.pipeline.render.renderer import render_clip_media

    ctx.check_cancelled()
    with Session(ctx.db_engine) as session:
        clips = list(
            session.exec(select(Clip).where(Clip.job_id == ctx.job_id).order_by(col(Clip.rank))).all()
        )

    renders_dir = ctx.staging_dir / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = renders_dir / "manifest.json"

    if not clips:
        manifest_path.write_text(
            json.dumps({"rendered": [], "failed": [], "no_clips": True}, indent=2),
            encoding="utf-8",
        )
        return StageResult(
            primary_artefact="renders/manifest.json",
            metrics={"rendered_count": 0, "failed_count": 0, "no_clips": True},
        )

    work_view = _create_work_view(ctx, readonly_subdirs=("source",), writable_subdirs=("renders",))
    rendered_outcomes: dict[str, dict[str, Any]] = {}
    failed_clips: list[dict[str, str]] = []
    try:
        total = len(clips)
        for idx, clip in enumerate(clips):
            ctx.check_cancelled()
            ctx.emit_progress(
                round((idx / total) * 100.0, 1),
                f"Rendering clip {idx + 1}/{total}...",
                clip_id=clip.id,
            )
            words = json.loads(clip.words_json or "[]")
            try:
                res = render_clip_media(
                    job_dir=work_view,
                    clip_id=clip.id,
                    start=clip.start,
                    end=clip.end,
                    words=words,
                    template_id=clip.template_id,
                    aspect=clip.aspect,
                    layout=clip.layout,
                )
                rendered_outcomes[clip.id] = {
                    "mp4_name": Path(res["mp4_path"]).name if res.get("mp4_path") else "",
                    "ass_name": Path(res["ass_path"]).name if res.get("ass_path") else "",
                    "srt_name": Path(res["srt_path"]).name if res.get("srt_path") else "",
                    "thumb_name": Path(res["thumb_path"]).name if res.get("thumb_path") else "",
                    "duration_s": clip.duration,
                    "aspect": clip.aspect,
                }
            except Exception as exc:
                # Per JOB_STATE_MACHINE.md: a failed clip render never fails the whole job
                failed_clips.append({"clip_id": clip.id, "error": str(exc)})
                ctx.emit_warning(f"Clip {clip.id} render failed: {exc}")
    finally:
        shutil.rmtree(work_view, ignore_errors=True)

    manifest_path.write_text(
        json.dumps(
            {
                "rendered": list(rendered_outcomes.keys()),
                "failed": failed_clips,
                "no_clips": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def _on_commit(session: Session, job: Job, _committed: dict[str, Path]) -> None:
        final_renders_dir = ctx.job_dir / "renders"
        failed_ids = {f["clip_id"] for f in failed_clips}
        db_clips = list(
            session.exec(select(Clip).where(Clip.job_id == job.id).order_by(col(Clip.rank))).all()
        )
        for db_clip in db_clips:
            if db_clip.id in rendered_outcomes:
                info = rendered_outcomes[db_clip.id]
                mp4_p = str(final_renders_dir / info["mp4_name"]) if info["mp4_name"] else None
                ass_p = str(final_renders_dir / info["ass_name"]) if info["ass_name"] else None
                srt_p = str(final_renders_dir / info["srt_name"]) if info["srt_name"] else None
                thumb_p = str(final_renders_dir / info["thumb_name"]) if info["thumb_name"] else None
                db_clip.render_mp4_path = mp4_p
                db_clip.render_ass_path = ass_p
                db_clip.render_srt_path = srt_p
                db_clip.thumb_path = thumb_p
                db_clip.status = "RENDERED"
                session.add(db_clip)

                plan_hash = hashlib.sha256(
                    f"{db_clip.id}:{db_clip.start}:{db_clip.end}:{db_clip.template_id}:{db_clip.aspect}:{db_clip.layout}".encode()
                ).hexdigest()[:16]
                ren = Render(
                    clip_id=db_clip.id,
                    plan_hash=plan_hash,
                    template_versions_json={"caption": f"{db_clip.template_id}@1"},
                    aspect=db_clip.aspect,
                    profile="1080p_fast",
                    path_mp4=mp4_p,
                    path_srt=srt_p,
                    path_ass=ass_p,
                    path_thumb=thumb_p,
                    status="DONE",
                    progress_pct=100.0,
                    duration_s=db_clip.duration,
                )
                session.add(ren)
            elif db_clip.id in failed_ids:
                db_clip.status = "FAILED"
                session.add(db_clip)
                ren = Render(
                    clip_id=db_clip.id,
                    aspect=db_clip.aspect,
                    profile="1080p_fast",
                    status="FAILED",
                    progress_pct=0.0,
                    duration_s=db_clip.duration,
                )
                session.add(ren)

    ctx.emit_progress(100.0, f"Rendered {len(rendered_outcomes)} clips.")
    return StageResult(
        primary_artefact="renders/manifest.json",
        metrics={
            "rendered_count": len(rendered_outcomes),
            "failed_count": len(failed_clips),
        },
        on_commit=_on_commit,
    )


def _default_package_stage(ctx: StageContext) -> StageResult:
    ctx.check_cancelled()
    with Session(ctx.db_engine) as session:
        clips = list(
            session.exec(select(Clip).where(Clip.job_id == ctx.job_id).order_by(col(Clip.rank))).all()
        )

    clips_dir = ctx.staging_dir / "clips"
    package_dir = ctx.staging_dir / "package"
    clips_dir.mkdir(parents=True, exist_ok=True)
    package_dir.mkdir(parents=True, exist_ok=True)

    packs: list[dict[str, Any]] = []
    for clip in clips:
        hashtags = json.loads(clip.hashtags_json or "[]")
        pack_payload = {
            "schema_version": 1,
            "clip_id": clip.id,
            "platform": "youtube_shorts",
            "language": ctx.job.detected_language or ctx.job.language or "en",
            "title": (clip.title or f"Clip #{clip.rank}")[:100],
            "description": (clip.reason or clip.transcript_snippet or "")[:5000],
            "hashtags": hashtags,
            "tags": [h.lstrip("#") for h in hashtags],
            "hook_line": clip.hook_line or None,
            "valid": True,
            "warnings": [],
        }
        pack_file = clips_dir / f"{clip.id}_publish_pack.json"
        pack_file.write_text(json.dumps(pack_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        packs.append({"clip_id": clip.id, "pack_rel": f"clips/{clip.id}_publish_pack.json"})

    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": ctx.job_id,
                "clip_count": len(clips),
                "packs": packs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def _on_commit(session: Session, job: Job, _committed: dict[str, Path]) -> None:
        for pinfo in packs:
            cid = pinfo["clip_id"]
            p_path = str(ctx.job_dir / pinfo["pack_rel"])
            existing = session.exec(
                select(PublishPack).where(PublishPack.clip_id == cid)
            ).first()
            if existing is None:
                session.add(
                    PublishPack(
                        clip_id=cid,
                        platform="youtube_shorts",
                        path_json=p_path,
                        valid=True,
                        warnings_json=[],
                    )
                )
            else:
                existing.path_json = p_path
                existing.updated_at = _now_iso()
                session.add(existing)

    ctx.emit_progress(100.0, f"Packaged {len(packs)} clip publish packs.")
    return StageResult(
        primary_artefact="package/manifest.json",
        metrics={"pack_count": len(packs)},
        on_commit=_on_commit,
    )


def default_stage_registry() -> StageRegistry:
    """Build the default Spec v2 StageRegistry wired to backend.pipeline.* adapters."""
    reg = StageRegistry()
    reg.register("fetch", _default_fetch_stage, version="1", config_key=None)
    reg.register("prepare", _default_prepare_stage, version="1", config_key=None)
    reg.register("transcribe", _default_transcribe_stage, version="1", config_key="asr")
    reg.register("analyze", _default_analyze_stage, version="1", config_key="highlights")
    reg.register("select", _default_select_stage, version="1", config_key="highlights")
    reg.register("render", _default_render_stage, version="1", config_key="render")
    reg.register("package", _default_package_stage, version="1", config_key="platforms")
    return reg


def cleanup_stage_temp_dirs(job_dir: Path, stage: str | None = None) -> int:
    """Remove leftover temporary staging directories from crashed or cancelled workers."""
    if not job_dir.exists():
        return 0
    removed = 0
    prefixes = (f".staging_{stage}_",) if stage else (".staging_", ".work_")
    for entry in list(job_dir.iterdir()):
        if entry.is_dir() and any(entry.name.startswith(pref) for pref in prefixes):
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


def _fsync_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir_best_effort(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _resolve_target_in_job_dir(job_dir: Path, raw_target: str | Path) -> Path:
    resolved_job = job_dir.resolve()
    cand = Path(raw_target)
    target = cand.resolve() if cand.is_absolute() else (resolved_job / cand).resolve()
    try:
        target.relative_to(resolved_job)
    except ValueError as exc:
        raise FatalStageError(
            "INVALID_ARTEFACT_PATH",
            f"Target artefact path {raw_target!r} escapes job directory {job_dir}",
        ) from exc
    return target


def _commit_staging_dir(
    staging_dir: Path,
    job_dir: Path,
    result: StageResult,
) -> tuple[Path, dict[str, Path]]:
    """Verify, fsync, and atomically rename staged artefacts into `job_dir`."""
    committed: dict[str, Path] = {}
    resolved_staging = staging_dir.resolve()
    resolved_job = job_dir.resolve()

    if result.outputs:
        for src_raw, dst_raw in result.outputs.items():
            src_cand = Path(src_raw)
            src = src_cand if src_cand.is_absolute() else (resolved_staging / src_cand)
            if not src.exists() or not src.is_file():
                raise FatalStageError(
                    "MISSING_STAGED_OUTPUT",
                    f"Declared staged artefact missing: {src}",
                )
            dst = _resolve_target_in_job_dir(resolved_job, dst_raw)
            dst.parent.mkdir(parents=True, exist_ok=True)
            _fsync_file(src)
            os.replace(src, dst)
            _fsync_dir_best_effort(dst.parent)
            rel_key = dst.relative_to(resolved_job).as_posix()
            committed[rel_key] = dst

    for src in sorted(resolved_staging.rglob("*")):
        if src.is_dir() or src.is_symlink():
            continue
        rel = src.relative_to(resolved_staging)
        if rel.parts and (rel.parts[0] == ".work" or src.name.startswith(".tmp_")):
            continue
        dst = (resolved_job / rel).resolve()
        dst.relative_to(resolved_job)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _fsync_file(src)
        os.replace(src, dst)
        _fsync_dir_best_effort(dst.parent)
        committed[rel.as_posix()] = dst

    primary_path: Path | None = None
    if result.primary_artefact is not None:
        raw_p = Path(result.primary_artefact)
        if raw_p.is_absolute():
            try:
                rel_from_staging = raw_p.resolve().relative_to(resolved_staging)
                primary_path = (resolved_job / rel_from_staging).resolve()
            except ValueError:
                primary_path = _resolve_target_in_job_dir(resolved_job, raw_p)
        else:
            primary_path = _resolve_target_in_job_dir(resolved_job, raw_p)
    elif committed:
        first_key = sorted(committed.keys())[0]
        primary_path = committed[first_key]
    else:
        primary_path = resolved_job

    return primary_path, committed


def _normalize_stage_result(raw: StageResult | dict[str, Any] | Path | str | None) -> StageResult:
    if raw is None:
        return StageResult()
    if isinstance(raw, StageResult):
        return raw
    if isinstance(raw, (Path, str)):
        return StageResult(primary_artefact=raw)
    if isinstance(raw, dict):
        return StageResult(
            primary_artefact=raw.get("primary_artefact") or raw.get("artefact_path"),
            outputs=raw.get("outputs"),
            metrics=dict(raw.get("metrics") or {}),
            job_updates=dict(raw.get("job_updates") or {}),
            warnings=list(raw.get("warnings") or []),
            on_commit=raw.get("on_commit"),
        )
    raise TypeError(f"Unsupported stage return type: {type(raw)!r}")


def _latest_checkpoint(session: Session, job_id: str, stage: str) -> StageCheckpoint | None:
    return session.exec(
        select(StageCheckpoint)
        .where(StageCheckpoint.job_id == job_id, StageCheckpoint.stage == stage)
        .order_by(col(StageCheckpoint.attempt).desc(), col(StageCheckpoint.id).desc())
    ).first()


def _max_attempt(session: Session, job_id: str, stage: str) -> int:
    cp = _latest_checkpoint(session, job_id, stage)
    return int(cp.attempt) if cp is not None else 0


def _build_stage_config_snapshot(
    *,
    job: Job,
    stage: str,
    spec: StageSpec,
    settings: Settings,
    extra_stage_configs: Mapping[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    job_fields: dict[str, Any]
    if stage == "fetch":
        job_fields = {
            "source_kind": job.source_kind,
            "source_url": job.source_url,
            "source_ref": job.source_ref,
        }
    elif stage == "prepare":
        job_fields = {
            "source_kind": job.source_kind,
            "max_video_hours": settings.max_video_hours,
        }
    elif stage == "transcribe":
        job_fields = {
            "language": job.language,
            "script_pref": job.script_pref,
            "asr_engine": settings.asr_engine,
            "asr_model_size": settings.asr_model_size,
        }
    elif stage == "analyze":
        job_fields = {
            "clip_brief": job.clip_brief,
            "llm_provider": settings.llm_provider,
        }
    elif stage == "select":
        job_fields = {
            "max_clips": job.max_clips,
            "clip_brief": job.clip_brief,
        }
    elif stage == "render":
        job_fields = {
            "aspect": job.aspect,
            "template_id": job.template_id,
            "preset_id": job.preset_id,
        }
    else:
        job_fields = {
            "preset_id": job.preset_id,
            "language": job.language,
        }

    yaml_cfg: dict[str, Any] = {}
    if spec.config_key:
        yaml_cfg = settings.load_spec_v2_yaml_config(spec.config_key)

    settings_snapshot = dict(job.settings_json or {})
    stage_override = dict((extra_stage_configs or {}).get(stage, {}))
    return {
        "job": job_fields,
        "yaml": yaml_cfg,
        "settings_json": settings_snapshot.get(stage, settings_snapshot if stage == "select" else {}),
        "override": stage_override,
    }


def _build_stage_versions(job: Job, stage: str, spec: StageSpec) -> dict[str, Any]:
    job_vers = dict(job.versions_json or {})
    versions: dict[str, Any] = {
        "handler_version": spec.version,
        "stage_version": job_vers.get(stage),
    }
    if spec.config_key and spec.config_key in job_vers:
        versions["config_version"] = job_vers.get(spec.config_key)
    return versions


def _compute_upstream_hashes(
    session: Session,
    job_id: str,
    stage_idx: int,
) -> dict[str, str]:
    upstream: dict[str, str] = {}
    for prev_stage in STAGES[:stage_idx]:
        cp = _latest_checkpoint(session, job_id, prev_stage)
        if cp is None:
            upstream[prev_stage] = "missing"
        else:
            art_hash = hash_path(Path(cp.artefact_path)) if cp.artefact_path else "none"
            upstream[prev_stage] = f"{cp.status}:{cp.input_hash or ''}:{art_hash}"
    return upstream


def mark_stale_from(
    job_id: str,
    stage: str,
    *,
    session: Session | None = None,
    engine: Engine | None = None,
    reason: str = "upstream_changed",
) -> list[str]:
    """Mark `stage` and all downstream stages as STALE."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}; expected one of {STAGES}")

    def _apply(sess: Session) -> list[str]:
        idx = STAGES.index(stage)
        marked: list[str] = []
        for s in STAGES[idx:]:
            cp = _latest_checkpoint(sess, job_id, s)
            if cp is not None and cp.status in ("DONE", "RUNNING", "FAILED", "STALE"):
                cp.status = "STALE"
                cp.error_json = {"stale_reason": reason}
                sess.add(cp)
                marked.append(s)
        sess.commit()
        return marked

    if session is not None:
        return _apply(session)

    db_engine = engine or get_engine()
    with Session(db_engine) as own_session:
        return _apply(own_session)


def _sleep_checking_cancel(job_id: str, delay: float, session: Session) -> None:
    if delay <= 0:
        return
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if is_cancel_requested(job_id):
            raise JobCancelledError(f"Job {job_id} cancelled during retry backoff")
        session.expire_all()
        cur = session.get(Job, job_id)
        if cur is not None and cur.status == JobStatus.CANCELLED.value:
            raise JobCancelledError(f"Job {job_id} cancelled during retry backoff")
        time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))


def _is_cancelled(job_id: str, session: Session) -> bool:
    if is_cancel_requested(job_id):
        return True
    session.expire_all()
    cur = session.get(Job, job_id)
    return cur is not None and cur.status == JobStatus.CANCELLED.value


def _mark_job_cancelled(session: Session, job: Job, job_dir: Path, stage: str | None = None) -> None:
    terminate_job_processes(job.id, grace_seconds=10.0)
    cleanup_stage_temp_dirs(job_dir)
    if stage is not None:
        cp = _latest_checkpoint(session, job.id, stage)
        if cp is not None and cp.status == "RUNNING":
            cp.status = "FAILED"
            cp.finished_at = _now_iso()
            cp.error_json = {"code": "CANCELLED", "message": "Job cancelled"}
            session.add(cp)
    job.status = JobStatus.CANCELLED.value
    job.stage = "CANCELLED"
    job.message = "Job cancelled"
    job.updated_at = _now_iso()
    session.add(job)
    session.commit()
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
    record_job_event(
        job.id,
        "done",
        {
            "stage": job.stage,
            "status": job.status,
            "pct": job.progress_pct,
            "message": job.message,
        },
        job_dir=job_dir,
    )


def run_job_v2(
    job_id: str,
    *,
    from_stage: str | None = None,
    registry: StageRegistry | None = None,
    stage_weights: Mapping[str, int] | None = None,
    max_attempts: int = 3,
    retry_backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    stage_configs: Mapping[str, dict[str, Any]] | None = None,
    blockers_root: Path | None = None,
    blockers_state_dir: Path | str | None = None,
    storage: StorageBackend | None = None,
    engine: Engine | None = None,
) -> Job:
    """Execute a job through Spec v2 stages with input_hash caching, atomic staging, retry, and cancel."""
    if from_stage is not None and from_stage not in STAGES:
        raise ValueError(f"Unknown from_stage {from_stage!r}; expected one of {STAGES}")

    settings = get_settings()
    reg = registry or default_stage_registry()
    store = storage or get_storage()
    db_engine = engine or get_engine()
    job_dir = store.job_dir(job_id)
    started_at_monotonic = time.monotonic()

    # Ignore/clean leftover temp staging dirs from crashed workers before resuming
    cleanup_stage_temp_dirs(job_dir)

    with Session(db_engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        if _is_cancelled(job_id, session):
            _mark_job_cancelled(session, job, job_dir)
            session.refresh(job)
            return job

        if from_stage is not None:
            mark_stale_from(job_id, from_stage, session=session, reason="from_stage_requested")

        allow_manual_render = from_stage in ("render", "package")

        for idx, stage in enumerate(STAGES):
            if _is_cancelled(job_id, session):
                _mark_job_cancelled(session, job, job_dir, stage=stage)
                session.refresh(job)
                return job

            if stage == "render" and not job.auto_mode and not allow_manual_render:
                job.status = JobStatus.AWAITING_REVIEW.value
                job.stage = "AWAITING_REVIEW"
                job.progress_pct = compute_overall_progress(
                    "select", 100.0, stage_weights=stage_weights
                )
                job.message = "Awaiting human review before rendering."
                job.updated_at = _now_iso()
                session.add(job)
                session.commit()
                record_job_event(
                    job.id,
                    "state",
                    {
                        "stage": job.stage,
                        "status": job.status,
                        "pct": job.progress_pct,
                        "message": job.message,
                        "eta_s": None,
                    },
                    job_dir=job_dir,
                )
                record_job_event(
                    job.id,
                    "done",
                    {
                        "stage": job.stage,
                        "status": job.status,
                        "pct": job.progress_pct,
                        "message": job.message,
                    },
                    job_dir=job_dir,
                )
                session.refresh(job)
                return job

            spec = reg.get(stage)
            upstream_hashes = _compute_upstream_hashes(session, job.id, idx)
            stage_cfg = _build_stage_config_snapshot(
                job=job,
                stage=stage,
                spec=spec,
                settings=settings,
                extra_stage_configs=stage_configs,
            )
            versions = _build_stage_versions(job, stage, spec)
            expected_hash = compute_input_hash(
                stage=stage,
                upstream_hashes=upstream_hashes,
                stage_config=stage_cfg,
                versions=versions,
            )

            latest_cp = _latest_checkpoint(session, job.id, stage)
            if (
                latest_cp is not None
                and latest_cp.status == "DONE"
                and latest_cp.input_hash == expected_hash
                and latest_cp.artefact_path
                and Path(latest_cp.artefact_path).exists()
            ):
                pct = compute_overall_progress(stage, 100.0, stage_weights=stage_weights)
                job.progress_pct = pct
                job.updated_at = _now_iso()
                session.add(job)
                session.commit()
                record_job_event(
                    job.id,
                    "progress",
                    {
                        "stage": stage,
                        "status": "DONE",
                        "pct": pct,
                        "message": f"Cached {stage} checkpoint hit.",
                        "eta_s": None,
                        "cached": True,
                    },
                    job_dir=job_dir,
                )
                continue

            job.status = STAGE_TO_JOB_STATUS[stage]
            job.stage = stage
            job.progress_pct = compute_overall_progress(stage, 0.0, stage_weights=stage_weights)
            job.message = f"Running {stage}..."
            job.error = None
            job.error_code = None
            job.error_message = None
            job.updated_at = _now_iso()
            session.add(job)
            session.commit()
            record_job_event(
                job.id,
                "state",
                {
                    "stage": stage,
                    "status": job.status,
                    "pct": job.progress_pct,
                    "message": job.message,
                    "eta_s": None,
                },
                job_dir=job_dir,
            )

            attempts_limit = max(1, int(max_attempts))
            stage_succeeded = False
            for retry_idx in range(attempts_limit):
                if _is_cancelled(job_id, session):
                    _mark_job_cancelled(session, job, job_dir, stage=stage)
                    session.refresh(job)
                    return job

                attempt_num = _max_attempt(session, job.id, stage) + 1
                cp = StageCheckpoint(
                    job_id=job.id,
                    stage=stage,
                    status="RUNNING",
                    input_hash=expected_hash,
                    artefact_path="",
                    attempt=attempt_num,
                    started_at=_now_iso(),
                    finished_at=None,
                )
                session.add(cp)
                session.commit()
                session.refresh(cp)

                staging_dir = Path(
                    tempfile.mkdtemp(prefix=f".staging_{stage}_", dir=str(job_dir))
                )

                def _emit_progress(pct_val: float, msg: str, clip_id: str | None = None) -> None:
                    overall = compute_overall_progress(
                        stage, pct_val, stage_weights=stage_weights
                    )
                    elapsed = time.monotonic() - started_at_monotonic
                    eta_s = (
                        round(elapsed * (100 - overall) / overall, 1)
                        if 0 < overall < 100 and elapsed > 0
                        else (0.0 if overall >= 100 else None)
                    )
                    payload: dict[str, Any] = {
                        "stage": stage,
                        "status": job.status,
                        "pct": overall,
                        "message": msg,
                        "eta_s": eta_s,
                    }
                    if clip_id is not None:
                        payload["clip_id"] = clip_id
                    record_job_event(job.id, "progress", payload, job_dir=job_dir)

                def _emit_warning(msg: str) -> None:
                    record_job_event(
                        job.id,
                        "warning",
                        {"stage": stage, "status": job.status, "message": msg},
                        job_dir=job_dir,
                    )

                ctx = StageContext(
                    job=job,
                    job_id=job.id,
                    stage=stage,
                    attempt=attempt_num,
                    job_dir=job_dir,
                    staging_dir=staging_dir,
                    settings=settings,
                    stage_config=stage_cfg,
                    input_hash=expected_hash,
                    db_engine=db_engine,
                    _progress_cb=_emit_progress,
                    _warning_cb=_emit_warning,
                    _cancel_check_cb=lambda: _is_cancelled(job.id, session),
                )

                try:
                    t0 = time.monotonic()
                    raw_res = spec.handler(ctx)
                    result = _normalize_stage_result(raw_res)

                    if _is_cancelled(job_id, session):
                        raise JobCancelledError(f"Job {job.id} cancelled before committing {stage}")

                    primary_path, committed = _commit_staging_dir(staging_dir, job_dir, result)
                    duration_ms = round((time.monotonic() - t0) * 1000.0, 2)
                    metrics = {**result.metrics, "duration_ms": duration_ms}

                    for w_msg in result.warnings:
                        _emit_warning(w_msg)

                    for key, val in result.job_updates.items():
                        if key in _SAFE_JOB_UPDATE_FIELDS:
                            setattr(job, key, val)

                    if result.on_commit is not None:
                        result.on_commit(session, job, committed)

                    cp.status = "DONE"
                    cp.artefact_path = str(primary_path)
                    cp.finished_at = _now_iso()
                    cp.metrics_json = metrics
                    cp.error_json = None
                    session.add(cp)

                    overall_done = compute_overall_progress(
                        stage, 100.0, stage_weights=stage_weights
                    )
                    job.progress_pct = overall_done
                    job.updated_at = _now_iso()
                    session.add(job)
                    session.commit()

                    record_job_event(
                        job.id,
                        "progress",
                        {
                            "stage": stage,
                            "status": "DONE",
                            "pct": overall_done,
                            "message": f"Completed {stage}.",
                            "eta_s": 0.0 if overall_done >= 100 else None,
                        },
                        job_dir=job_dir,
                    )
                    stage_succeeded = True
                    break

                except JobCancelledError:
                    cp.status = "FAILED"
                    cp.finished_at = _now_iso()
                    cp.error_json = {"code": "CANCELLED", "message": "Job cancelled"}
                    session.add(cp)
                    session.commit()
                    _mark_job_cancelled(session, job, job_dir, stage=stage)
                    session.refresh(job)
                    return job

                except RetryableError as exc:
                    cp.status = "FAILED"
                    cp.finished_at = _now_iso()
                    cp.error_json = {
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": True,
                        "details": exc.details,
                    }
                    session.add(cp)
                    session.commit()

                    if retry_idx + 1 < attempts_limit:
                        delay = (
                            float(retry_backoffs[min(retry_idx, len(retry_backoffs) - 1)])
                            if retry_backoffs
                            else 0.0
                        )
                        _emit_warning(
                            f"Retryable error at {stage} (attempt {retry_idx + 1}/{attempts_limit}): {exc}"
                        )
                        try:
                            _sleep_checking_cancel(job.id, delay, session)
                        except JobCancelledError:
                            _mark_job_cancelled(session, job, job_dir, stage=stage)
                            session.refresh(job)
                            return job
                        continue

                    job.status = JobStatus.FAILED.value
                    job.error_code = exc.code
                    job.error_message = str(exc)
                    job.error = str(exc)
                    job.message = f"Failed at {stage}: {exc}"
                    job.updated_at = _now_iso()
                    session.add(job)
                    session.commit()
                    record_job_event(
                        job.id,
                        "state",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": job.error_code,
                        },
                        job_dir=job_dir,
                    )
                    record_job_event(
                        job.id,
                        "done",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": job.error_code,
                        },
                        job_dir=job_dir,
                    )
                    session.refresh(job)
                    return job

                except BlockerRequired as exc:
                    b = exc.blocker
                    persisted = create_or_get_blocker(
                        btype=b.type,
                        task_id=b.task_id,
                        title=b.title,
                        needed=b.needed,
                        why=b.why or str(exc),
                        step=b.step,
                        alternatives=b.alternatives,
                        unblocked_meanwhile=b.unblocked_meanwhile,
                        root_dir=blockers_root or config_mod.ROOT_DIR,
                        state_dir=blockers_state_dir,
                        raise_exc=False,
                    )
                    existing_rec = session.get(BlockerRecord, persisted.id)
                    if existing_rec is None:
                        session.add(
                            BlockerRecord(
                                id=persisted.id,
                                type=persisted.type.value,
                                status=persisted.status.value,
                                task_id=persisted.task_id,
                                step=str(persisted.step),
                                title=persisted.title,
                                payload_json=persisted.model_dump(mode="json"),
                            )
                        )
                    cp.status = "FAILED"
                    cp.finished_at = _now_iso()
                    cp.error_json = {
                        "code": "BLOCKER_REQUIRED",
                        "blocker_id": persisted.id,
                        "message": str(exc),
                    }
                    session.add(cp)

                    job.status = JobStatus.FAILED.value
                    job.error_code = "BLOCKER_REQUIRED"
                    job.error_message = str(exc)
                    job.error = str(exc)
                    job.message = f"Blocked at {stage}: {persisted.title}"
                    job.updated_at = _now_iso()
                    session.add(job)
                    session.commit()
                    record_job_event(
                        job.id,
                        "state",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": "BLOCKER_REQUIRED",
                            "blocker_id": persisted.id,
                        },
                        job_dir=job_dir,
                    )
                    record_job_event(
                        job.id,
                        "done",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": "BLOCKER_REQUIRED",
                            "blocker_id": persisted.id,
                        },
                        job_dir=job_dir,
                    )
                    session.refresh(job)
                    return job

                except FatalStageError as exc:
                    cp.status = "FAILED"
                    cp.finished_at = _now_iso()
                    cp.error_json = {
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": False,
                        "details": exc.details,
                    }
                    session.add(cp)
                    job.status = JobStatus.FAILED.value
                    job.error_code = exc.code
                    job.error_message = str(exc)
                    job.error = str(exc)
                    job.message = f"Failed at {stage}: {exc}"
                    job.updated_at = _now_iso()
                    session.add(job)
                    session.commit()
                    record_job_event(
                        job.id,
                        "state",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": job.error_code,
                        },
                        job_dir=job_dir,
                    )
                    record_job_event(
                        job.id,
                        "done",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": job.error_code,
                        },
                        job_dir=job_dir,
                    )
                    session.refresh(job)
                    return job

                except Exception as exc:
                    cp.status = "FAILED"
                    cp.finished_at = _now_iso()
                    cp.error_json = {
                        "code": "STAGE_FAILED",
                        "message": str(exc),
                        "retryable": False,
                    }
                    session.add(cp)
                    job.status = JobStatus.FAILED.value
                    job.error_code = "STAGE_FAILED"
                    job.error_message = str(exc)
                    job.error = str(exc)
                    job.message = f"Failed at {stage}: {exc}"
                    job.updated_at = _now_iso()
                    session.add(job)
                    session.commit()
                    record_job_event(
                        job.id,
                        "state",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": job.error_code,
                        },
                        job_dir=job_dir,
                    )
                    record_job_event(
                        job.id,
                        "done",
                        {
                            "stage": stage,
                            "status": job.status,
                            "pct": job.progress_pct,
                            "message": job.message,
                            "error_code": job.error_code,
                        },
                        job_dir=job_dir,
                    )
                    session.refresh(job)
                    return job

                finally:
                    if staging_dir.exists():
                        shutil.rmtree(staging_dir, ignore_errors=True)

            if not stage_succeeded:  # pragma: no cover - defensive guard
                session.refresh(job)
                return job

        job.status = JobStatus.COMPLETED.value
        job.stage = "COMPLETED"
        job.progress_pct = 100
        job.message = "Completed all stages."
        job.error = None
        job.error_code = None
        job.error_message = None
        job.updated_at = _now_iso()
        session.add(job)
        session.commit()
        record_job_event(
            job.id,
            "state",
            {
                "stage": "COMPLETED",
                "status": job.status,
                "pct": 100,
                "message": job.message,
                "eta_s": 0.0,
            },
            job_dir=job_dir,
        )
        record_job_event(
            job.id,
            "done",
            {
                "stage": "COMPLETED",
                "status": job.status,
                "pct": 100,
                "message": job.message,
                "eta_s": 0.0,
            },
            job_dir=job_dir,
        )
        session.refresh(job)
        return job


def retry_job(
    job_id: str,
    from_stage: str | None = None,
    *,
    enqueue: bool = False,
    storage: StorageBackend | None = None,
    engine: Engine | None = None,
    **run_kwargs: Any,
) -> Job:
    """Prepare a job for resume/retry from `from_stage` (or first non-DONE/STALE stage)."""
    if from_stage is not None and from_stage not in STAGES:
        raise ValueError(f"Unknown from_stage {from_stage!r}; expected one of {STAGES}")

    store = storage or get_storage()
    db_engine = engine or get_engine()
    job_dir = store.job_dir(job_id)

    clear_cancel(job_id)
    cleanup_stage_temp_dirs(job_dir)

    with Session(db_engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        if from_stage is not None:
            mark_stale_from(job_id, from_stage, session=session, reason="retry_from_stage")

        job.status = JobStatus.QUEUED.value
        job.stage = from_stage or "QUEUED"
        job.error = None
        job.error_code = None
        job.error_message = None
        job.message = "Re-queued for retry"
        job.updated_at = _now_iso()
        session.add(job)
        session.commit()
        session.refresh(job)

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

    if enqueue:
        enqueue_job(job_id, from_stage=from_stage)
        with Session(db_engine) as session:
            refreshed = session.get(Job, job_id)
            assert refreshed is not None
            return refreshed

    return run_job_v2(
        job_id,
        from_stage=from_stage,
        storage=store,
        engine=db_engine,
        **run_kwargs,
    )


def cancel_job(
    job_id: str,
    *,
    grace_seconds: float = 10.0,
    storage: StorageBackend | None = None,
    engine: Engine | None = None,
) -> Job:
    """Cooperatively cancel a job, terminate active subprocesses, and clean stage temp dirs."""
    store = storage or get_storage()
    db_engine = engine or get_engine()
    job_dir = store.job_dir(job_id)

    request_cancel(job_id)
    terminate_job_processes(job_id, grace_seconds=grace_seconds)
    cleanup_stage_temp_dirs(job_dir)

    with Session(db_engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")
        _mark_job_cancelled(session, job, job_dir, stage=job.stage if job.stage in STAGES else None)
        session.refresh(job)
        return job
