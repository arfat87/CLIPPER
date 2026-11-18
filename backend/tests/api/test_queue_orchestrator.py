from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, select

from backend.app.config import get_settings
from backend.app.db import init_schema, reset_engine
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
from backend.app.services import queue as queue_mod
from backend.app.services.orchestrator import (
    DEFAULT_STAGE_WEIGHTS,
    STAGES,
    FatalStageError,
    RetryableError,
    StageContext,
    StageRegistry,
    StageResult,
    cancel_job,
    clear_job_events,
    compute_input_hash,
    compute_overall_progress,
    default_stage_registry,
    format_sse_event,
    get_job_events,
    mark_stale_from,
    retry_job,
    run_job_v2,
)
from backend.app.services.storage import LocalStorageBackend
from hitl.models import Blocker, BlockerRequired, BlockerType, NeededItem
from hitl.raise_blocker import list_blockers


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test_orchestrator.db"
    db_url = f"sqlite:///{db_path}"

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("QUEUE_MODE", "immediate")
    monkeypatch.setenv("QUEUE_URL", "memory://")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    get_settings.cache_clear()
    reset_engine()

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_schema(engine)
    storage = LocalStorageBackend(base_dir=data_dir)
    queue_mod.configure_queue(queue_url="memory://", immediate=True)

    yield engine, storage, tmp_path

    queue_mod.clear_cancel("job_test")
    engine.dispose()
    reset_engine()
    get_settings.cache_clear()


def _create_job(
    engine,
    job_id: str = "job_test",
    *,
    auto_mode: bool = True,
    source_url: str = "sample://sample_hinglish_podcast",
    versions_json: dict[str, Any] | None = None,
) -> Job:
    clear_job_events(job_id)
    with Session(engine) as session:
        job = Job(
            id=job_id,
            source_kind="url",
            source_url=source_url,
            platform="sample",
            title="Test Video",
            language="hinglish",
            auto_mode=auto_mode,
            max_clips=2,
            versions_json=versions_json or {},
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def _make_stub_registry(
    calls: list[str],
    *,
    hooks: dict[str, Any] | None = None,
    versions: dict[str, str] | None = None,
) -> StageRegistry:
    reg = StageRegistry()
    hooks = hooks or {}
    versions = versions or {}

    for stage in STAGES:
        def _make_handler(st: str):
            def _handler(ctx: StageContext) -> StageResult:
                calls.append(st)
                ctx.emit_progress(50.0, f"Working on {st}")
                if st in hooks:
                    res = hooks[st](ctx)
                    if res is not None:
                        return res
                out_dir = ctx.staging_dir / st
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / f"{st}.json"
                out_file.write_text(
                    json.dumps({"stage": st, "attempt": ctx.attempt, "hash": ctx.input_hash}),
                    encoding="utf-8",
                )
                return StageResult(
                    primary_artefact=f"{st}/{st}.json",
                    metrics={"stage": st},
                )

            return _handler

        reg.register(stage, _make_handler(stage), version=versions.get(stage, "1"))

    return reg


def test_huey_queue_modes_and_enqueue_job(
    isolated_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    engine, storage, _ = isolated_env
    _create_job(engine, "job_q_imm")

    # 1. SqliteHuey in non-immediate mode queues task into huey.db
    huey_db = tmp_path / "huey_queue.db"
    q_async = queue_mod.configure_queue(queue_url=f"sqlite:///{huey_db}", immediate=False)
    assert queue_mod.get_queue_depth() == 0
    res = queue_mod.enqueue_job("job_q_imm", from_stage="transcribe")
    assert res is not None
    assert huey_db.exists()
    assert queue_mod.get_queue_depth() == 1
    task = q_async.dequeue()
    assert task is not None
    assert task.args == ("job_q_imm",)
    assert task.kwargs == {"from_stage": "transcribe"}

    # 2. MemoryHuey in immediate=True mode executes run_job_v2 via Huey task wrapper
    invoked: list[tuple[str, str | None]] = []

    def _fake_run_job_v2(job_id: str, *, from_stage: str | None = None) -> Job:
        invoked.append((job_id, from_stage))
        with Session(engine) as s:
            j = s.get(Job, job_id)
            assert j is not None
            j.status = JobStatus.COMPLETED.value
            s.add(j)
            s.commit()
            s.refresh(j)
            return j

    monkeypatch.setattr("backend.app.services.orchestrator.run_job_v2", _fake_run_job_v2)
    queue_mod.configure_queue(queue_url="memory://", immediate=True)
    result = queue_mod.enqueue_job("job_q_imm", from_stage="analyze")
    assert result() == JobStatus.COMPLETED.value
    assert invoked == [("job_q_imm", "analyze")]


def test_mid_stage_crash_atomic_staging_and_resume(isolated_env):
    engine, storage, _ = isolated_env
    job = _create_job(engine, "job_crash")
    job_dir = storage.job_dir(job.id)

    # Pre-seed an older valid transcribe artefact to verify partial crash never overwrites/corrupts it
    valid_dir = job_dir / "transcribe"
    valid_dir.mkdir(parents=True, exist_ok=True)
    valid_file = valid_dir / "transcribe.json"
    valid_file.write_text('{"valid": true}', encoding="utf-8")

    # Simulate a leftover crashed staging directory in job_dir
    leftover_dir = job_dir / ".staging_transcribe_leftover"
    leftover_dir.mkdir(parents=True, exist_ok=True)
    (leftover_dir / "corrupt.tmp").write_text("garbage", encoding="utf-8")

    calls_run1: list[str] = []

    def _crashing_transcribe(ctx: StageContext) -> StageResult:
        partial_dir = ctx.staging_dir / "transcribe"
        partial_dir.mkdir(parents=True, exist_ok=True)
        (partial_dir / "transcribe.json").write_text("{PARTIAL CORRUPT", encoding="utf-8")
        raise KeyboardInterrupt("Simulated SIGKILL / worker crash mid-stage")

    reg_crash = _make_stub_registry(calls_run1, hooks={"transcribe": _crashing_transcribe})

    with pytest.raises(KeyboardInterrupt):
        run_job_v2(job.id, registry=reg_crash, storage=storage, engine=engine)

    # Target artefact was NOT corrupted by partial write
    assert valid_file.read_text(encoding="utf-8") == '{"valid": true}'
    # Staging temp dir was cleaned up on unwind
    assert not list(job_dir.glob(".staging_*"))

    # Verify fetch and prepare are DONE, transcribe was left RUNNING (interrupted)
    with Session(engine) as session:
        cps = {
            (cp.stage, cp.attempt): cp
            for cp in session.exec(select(StageCheckpoint).where(StageCheckpoint.job_id == job.id)).all()
        }
        assert cps[("fetch", 1)].status == "DONE"
        assert cps[("prepare", 1)].status == "DONE"
        assert cps[("transcribe", 1)].status == "RUNNING"

    # Place another leftover temp dir to ensure retry ignores/cleans crashed worker temp dirs
    crashed_tmp = job_dir / ".staging_analyze_crashed"
    crashed_tmp.mkdir(parents=True, exist_ok=True)
    (crashed_tmp / "partial.bin").write_bytes(b"bad")

    # Resume via retry_job: fetch & prepare MUST be skipped (cache hit), transcribe..package MUST run
    calls_run2: list[str] = []
    reg_ok = _make_stub_registry(calls_run2)
    resumed = retry_job(job.id, registry=reg_ok, storage=storage, engine=engine)

    assert resumed.status == JobStatus.COMPLETED.value
    assert resumed.progress_pct == 100
    assert calls_run2 == ["transcribe", "analyze", "select", "render", "package"]
    assert not crashed_tmp.exists()

    with Session(engine) as session:
        done_stages = {
            cp.stage
            for cp in session.exec(
                select(StageCheckpoint).where(
                    StageCheckpoint.job_id == job.id, StageCheckpoint.status == "DONE"
                )
            ).all()
        }
        assert done_stages == set(STAGES)
        tr_cps = session.exec(
            select(StageCheckpoint).where(
                StageCheckpoint.job_id == job.id, StageCheckpoint.stage == "transcribe"
            )
        ).all()
        assert len(tr_cps) == 2
        assert sorted(cp.attempt for cp in tr_cps) == [1, 2]
        assert json.loads(valid_file.read_text(encoding="utf-8"))["stage"] == "transcribe"


def test_input_hash_cache_hit_and_staleness_invalidation(isolated_env):
    engine, storage, _ = isolated_env
    job = _create_job(engine, "job_cache", versions_json={"analyze": "1"})

    h1 = compute_input_hash(
        stage="transcribe",
        upstream_hashes={"prepare": "abc"},
        stage_config={"beam": 5},
        versions={"handler_version": "1"},
    )
    h2 = compute_input_hash(
        stage="transcribe",
        upstream_hashes={"prepare": "abc"},
        stage_config={"beam": 5},
        versions={"handler_version": "1"},
    )
    h3 = compute_input_hash(
        stage="transcribe",
        upstream_hashes={"prepare": "abc"},
        stage_config={"beam": 8},
        versions={"handler_version": "1"},
    )
    assert h1 == h2
    assert h1 != h3

    # Initial run executes all 7 stages
    calls: list[str] = []
    reg = _make_stub_registry(calls)
    res = run_job_v2(job.id, registry=reg, storage=storage, engine=engine)
    assert res.status == JobStatus.COMPLETED.value
    assert calls == list(STAGES)

    # Re-run with identical inputs -> 100% cache hit
    calls.clear()
    res2 = run_job_v2(job.id, registry=reg, storage=storage, engine=engine)
    assert res2.status == JobStatus.COMPLETED.value
    assert calls == []

    # Config change on transcribe invalidates transcribe + all downstream stages
    calls.clear()
    res3 = run_job_v2(
        job.id,
        registry=reg,
        stage_configs={"transcribe": {"beam": 8}},
        storage=storage,
        engine=engine,
    )
    assert res3.status == JobStatus.COMPLETED.value
    assert calls == ["transcribe", "analyze", "select", "render", "package"]

    # Version change in Job.versions_json invalidates analyze + downstream
    with Session(engine) as session:
        db_job = session.get(Job, job.id)
        assert db_job is not None
        db_job.versions_json = {"analyze": "2"}
        session.add(db_job)
        session.commit()

    calls.clear()
    res4 = run_job_v2(
        job.id,
        registry=reg,
        stage_configs={"transcribe": {"beam": 8}},
        storage=storage,
        engine=engine,
    )
    assert res4.status == JobStatus.COMPLETED.value
    assert calls == ["analyze", "select", "render", "package"]

    # Mutating upstream artefact on disk invalidates downstream even if config unchanged
    job_dir = storage.job_dir(job.id)
    (job_dir / "analyze" / "analyze.json").write_text('{"edited": true}', encoding="utf-8")
    calls.clear()
    res5 = run_job_v2(
        job.id,
        registry=reg,
        stage_configs={"transcribe": {"beam": 8}},
        storage=storage,
        engine=engine,
    )
    assert res5.status == JobStatus.COMPLETED.value
    assert calls == ["select", "render", "package"]

    # Explicit mark_stale_from + retry_job(from_stage="render") reruns render + package only
    marked = mark_stale_from(job.id, "render", engine=engine, reason="template_changed")
    assert marked == ["render", "package"]
    calls.clear()
    res6 = retry_job(
        job.id,
        from_stage="render",
        registry=reg,
        stage_configs={"transcribe": {"beam": 8}},
        storage=storage,
        engine=engine,
    )
    assert res6.status == JobStatus.COMPLETED.value
    assert calls == ["render", "package"]


def test_retryable_fatal_and_blocker_error_handling(isolated_env, tmp_path: Path):
    engine, storage, _ = isolated_env

    # 1. RetryableError succeeds on 3rd attempt with 0s backoff
    job_retry = _create_job(engine, "job_retry_ok")
    attempts_seen = 0

    def _flaky_fetch(ctx: StageContext) -> StageResult:
        nonlocal attempts_seen
        attempts_seen += 1
        if attempts_seen < 3:
            raise RetryableError("RATE_LIMITED", f"429 attempt {attempts_seen}")
        out = ctx.staging_dir / "fetch" / "fetch.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"ok": true}', encoding="utf-8")
        return StageResult(primary_artefact="fetch/fetch.json")

    calls_ok: list[str] = []
    reg_retry_ok = _make_stub_registry(calls_ok, hooks={"fetch": _flaky_fetch})
    res_ok = run_job_v2(
        job_retry.id,
        registry=reg_retry_ok,
        max_attempts=3,
        retry_backoffs=(0.0, 0.0, 0.0),
        storage=storage,
        engine=engine,
    )
    assert res_ok.status == JobStatus.COMPLETED.value
    assert attempts_seen == 3

    with Session(engine) as session:
        fetch_cps = session.exec(
            select(StageCheckpoint).where(
                StageCheckpoint.job_id == job_retry.id, StageCheckpoint.stage == "fetch"
            )
        ).all()
        assert [cp.status for cp in sorted(fetch_cps, key=lambda c: c.attempt)] == [
            "FAILED",
            "FAILED",
            "DONE",
        ]

    # 2. RetryableError exhausting max_attempts -> Job FAILED
    job_retry_fail = _create_job(engine, "job_retry_fail")

    def _always_retryable(ctx: StageContext) -> StageResult:
        raise RetryableError("RATE_LIMITED", "Still rate limited")

    reg_retry_fail = _make_stub_registry([], hooks={"prepare": _always_retryable})
    res_rf = run_job_v2(
        job_retry_fail.id,
        registry=reg_retry_fail,
        max_attempts=3,
        retry_backoffs=(0.0, 0.0, 0.0),
        storage=storage,
        engine=engine,
    )
    assert res_rf.status == JobStatus.FAILED.value
    assert res_rf.error_code == "RATE_LIMITED"
    assert "Still rate limited" in (res_rf.error_message or "")

    # 3. FatalStageError -> immediate FAILED without retries
    job_fatal = _create_job(engine, "job_fatal")
    fatal_calls = 0

    def _fatal_transcribe(ctx: StageContext) -> StageResult:
        nonlocal fatal_calls
        fatal_calls += 1
        raise FatalStageError("ASR_FAILED", "Corrupt audio stream")

    reg_fatal = _make_stub_registry([], hooks={"transcribe": _fatal_transcribe})
    res_fatal = run_job_v2(
        job_fatal.id,
        registry=reg_fatal,
        max_attempts=3,
        retry_backoffs=(0.0, 0.0, 0.0),
        storage=storage,
        engine=engine,
    )
    assert fatal_calls == 1
    assert res_fatal.status == JobStatus.FAILED.value
    assert res_fatal.error_code == "ASR_FAILED"
    assert "Corrupt audio stream" in (res_fatal.error_message or "")

    # 4. BlockerRequired -> FAILED(error_code="BLOCKER_REQUIRED") + blocker file in tmp_path
    job_blk = _create_job(engine, "job_blocker")
    blocker_root = tmp_path / "hitl_root"
    blocker_root.mkdir(parents=True, exist_ok=True)

    def _blocker_analyze(ctx: StageContext) -> StageResult:
        blk = Blocker(
            id="BLK-PENDING",
            type=BlockerType.SECRET,
            task_id="T-4.3",
            title="Gemini API key required",
            needed=[NeededItem(key="GEMINI_API_KEY", where=".env", how_to_get="AI Studio")],
            why="Requested strict Gemini scoring",
        )
        raise BlockerRequired(blk)

    reg_blk = _make_stub_registry([], hooks={"analyze": _blocker_analyze})
    res_blk = run_job_v2(
        job_blk.id,
        registry=reg_blk,
        blockers_root=blocker_root,
        storage=storage,
        engine=engine,
    )
    assert res_blk.status == JobStatus.FAILED.value
    assert res_blk.error_code == "BLOCKER_REQUIRED"
    blockers = list_blockers(root_dir=blocker_root)
    assert len(blockers) == 1
    assert blockers[0].id == "BLK-0001"
    assert (blocker_root / ".clipper-state" / "blockers" / "BLK-0001.yaml").exists()
    assert (blocker_root / "BLOCKERS.md").exists()

    with Session(engine) as session:
        rec = session.get(BlockerRecord, "BLK-0001")
        assert rec is not None
        assert rec.status == "OPEN"


def test_auto_mode_false_pauses_at_awaiting_review_then_resumes(isolated_env):
    engine, storage, _ = isolated_env
    job = _create_job(engine, "job_manual", auto_mode=False)

    calls: list[str] = []
    reg = _make_stub_registry(calls)
    paused = run_job_v2(job.id, registry=reg, storage=storage, engine=engine)
    assert paused.status == JobStatus.AWAITING_REVIEW.value
    assert paused.stage == "AWAITING_REVIEW"
    assert calls == ["fetch", "prepare", "transcribe", "analyze", "select"]

    # Manual trigger from_stage="render" completes render -> package -> COMPLETED
    calls.clear()
    completed = retry_job(job.id, from_stage="render", registry=reg, storage=storage, engine=engine)
    assert completed.status == JobStatus.COMPLETED.value
    assert completed.progress_pct == 100
    assert calls == ["render", "package"]


def test_cooperative_cancellation_and_process_termination(isolated_env):
    engine, storage, _ = isolated_env
    job = _create_job(engine, "job_cancel")
    job_dir = storage.job_dir(job.id)

    # 1. Verify SIGTERM -> SIGKILL process termination helper on a child process ignoring SIGTERM
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    queue_mod.register_process("job_sigkill_test", proc)
    assert proc.poll() is None
    signaled = queue_mod.terminate_job_processes(
        "job_sigkill_test", grace_seconds=0.15, poll_interval=0.02
    )
    assert proc.pid in signaled
    assert proc.wait(timeout=2.0) is not None
    assert queue_mod.get_registered_pids("job_sigkill_test") == []

    # 2. Cooperative cancellation during running stage cleans temp dirs & marks Job CANCELLED
    entered_stage = threading.Event()
    proceed_stage = threading.Event()

    def _cancellable_transcribe(ctx: StageContext) -> StageResult:
        (ctx.staging_dir / "partial.txt").write_text("partial", encoding="utf-8")
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        ctx.register_process(child)
        entered_stage.set()
        proceed_stage.wait(timeout=5.0)
        ctx.check_cancelled()
        return StageResult(primary_artefact="transcribe/transcribe.json")

    calls: list[str] = []
    reg = _make_stub_registry(calls, hooks={"transcribe": _cancellable_transcribe})
    result_holder: list[Job] = []

    def _worker():
        result_holder.append(run_job_v2(job.id, registry=reg, storage=storage, engine=engine))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert entered_stage.wait(timeout=5.0)

    cancelled_job = cancel_job(job.id, grace_seconds=0.2, storage=storage, engine=engine)
    proceed_stage.set()
    t.join(timeout=5.0)

    assert cancelled_job.status == JobStatus.CANCELLED.value
    assert len(result_holder) == 1
    assert result_holder[0].status == JobStatus.CANCELLED.value
    assert not list(job_dir.glob(".staging_*"))
    assert queue_mod.get_registered_pids(job.id) == []
    assert not (job_dir / "partial.txt").exists()


def test_sse_events_last_event_id_and_default_pipeline_adapters(isolated_env):
    engine, storage, _ = isolated_env
    assert sum(DEFAULT_STAGE_WEIGHTS.values()) == 100
    assert compute_overall_progress("fetch", 0.0) == 0
    assert compute_overall_progress("fetch", 100.0) == 10
    assert compute_overall_progress("package", 100.0) == 100

    job = _create_job(engine, "job_e2e_v2", auto_mode=True)
    reg = default_stage_registry()
    completed = run_job_v2(job.id, registry=reg, storage=storage, engine=engine)
    assert completed.status == JobStatus.COMPLETED.value
    assert completed.progress_pct == 100

    # Verify DB artefacts created by default Spec v2 adapters
    with Session(engine) as session:
        tr = session.exec(select(Transcript).where(Transcript.job_id == job.id)).first()
        assert tr is not None
        assert Path(tr.path_json).exists()

        clips = list(session.exec(select(Clip).where(Clip.job_id == job.id)).all())
        assert len(clips) >= 1
        assert all(c.status == "RENDERED" for c in clips)
        assert Path(clips[0].render_mp4_path or "").exists()

        renders = list(session.exec(select(Render).where(Render.clip_id == clips[0].id)).all())
        assert len(renders) == 1
        assert renders[0].status == "DONE"

        packs = list(session.exec(select(PublishPack).where(PublishPack.clip_id == clips[0].id)).all())
        assert len(packs) == 1
        assert Path(packs[0].path_json).exists()

    # Verify SSE event sequence numbering and Last-Event-ID resumption
    all_events = get_job_events(job.id, job_dir=storage.job_dir(job.id))
    assert len(all_events) >= 14
    ids = [ev.id for ev in all_events]
    assert ids == list(range(1, len(all_events) + 1))
    assert all_events[-1].event == "done"
    assert all_events[-1].data["status"] == "COMPLETED"

    resumed_events = get_job_events(job.id, after_id=str(all_events[2].id))
    assert resumed_events[0].id == all_events[3].id
    sse_wire = format_sse_event(resumed_events[0])
    assert sse_wire.startswith(f"id: {resumed_events[0].id}\nevent: ")
    assert sse_wire.endswith("\n\n")
