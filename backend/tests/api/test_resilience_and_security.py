from __future__ import annotations

import errno
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session

from backend.app.config import get_settings
from backend.app.db import init_schema, reset_engine
from backend.app.models.entities import Clip, Job, JobStatus, StageCheckpoint
from backend.app.services.admin import (
    ADMIN_SESSION_COOKIE,
    hash_admin_password,
    issue_admin_session_token,
    reset_login_rate_limits,
)
from backend.app.services.orchestrator import (
    STAGES,
    FatalStageError,
    RetryableError,
    StageContext,
    StageRegistry,
    StageResult,
    run_job_v2,
)
from backend.app.services.resilience import (
    VALID_ERROR_CODES,
    ErrorCode,
    RetryPolicy,
    clean_orphaned_temp_files,
    compute_dir_size_bytes,
    enforce_disk_quota_and_evict_lru,
    execute_with_retry,
    map_stage_error,
    recover_interrupted_jobs,
)
from backend.app.services.security import (
    ClipperSecurityMiddleware,
    SecurityError,
    SlidingWindowRateLimiter,
    TokenBucketRateLimiter,
    enforce_rights_confirmation,
    require_bearer_auth,
    validate_safe_file_path,
    validate_safe_source_url,
    verify_bearer_or_session_auth,
)
from backend.app.services.storage import LocalStorageBackend
from hitl.models import Blocker, BlockerRequired, BlockerType


@pytest.fixture
def res_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "test_resilience.db"
    db_url = f"sqlite:///{db_path}"

    pw = "resilience-admin-secret"
    pw_hash = hash_admin_password(pw)

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("ADMIN_ENABLED", "true")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", pw_hash)

    get_settings.cache_clear()
    reset_engine()
    reset_login_rate_limits()

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    init_schema(engine)
    storage = LocalStorageBackend(base_dir=data_dir)

    yield engine, storage, tmp_path, pw

    reset_login_rate_limits()
    engine.dispose()
    reset_engine()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# T-11.2 Tests: Error Mapping, Exponential Backoff + Jitter, Disk Quotas & Chaos
# ---------------------------------------------------------------------------


def test_retry_policy_exponential_backoff_with_jitter_and_execution():
    policy = RetryPolicy(
        max_attempts=3,
        base_delay_s=5.0,
        factor=4.0,
        max_delay_s=60.0,
        jitter_ratio=0.1,
        explicit_delays=(5.0, 20.0, 60.0),
    )

    # Zero-jitter RNG returns exact 5 / 20 / 60 schedule
    assert policy.delay_for_attempt(1, rng=lambda _a, _b: 0.0) == 5.0
    assert policy.delay_for_attempt(2, rng=lambda _a, _b: 0.0) == 20.0
    assert policy.delay_for_attempt(3, rng=lambda _a, _b: 0.0) == 60.0

    # Positive jitter stays within +/- 10%
    d1 = policy.delay_for_attempt(1, rng=lambda _a, b: b)
    assert 5.0 <= d1 <= 5.5

    # execute_with_retry retries transient failures and succeeds on attempt 3
    recorded_delays: list[float] = []
    attempts = 0

    def _flaky_network_op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableError("RATE_LIMITED", "HTTP 429 rate limited")
        return "ok-after-retries"

    res = execute_with_retry(
        _flaky_network_op,
        policy=policy,
        sleep_fn=lambda s: recorded_delays.append(s),
        rng=lambda _a, _b: 0.0,
    )
    assert res == "ok-after-retries"
    assert attempts == 3
    assert recorded_delays == [5.0, 20.0]

    # Non-retryable FatalStageError fails immediately on attempt 1 without sleeping
    fatal_attempts = 0

    def _fatal_op() -> None:
        nonlocal fatal_attempts
        fatal_attempts += 1
        raise FatalStageError("PRIVATE_VIDEO", "Video is private")

    with pytest.raises(FatalStageError):
        execute_with_retry(_fatal_op, policy=policy, sleep_fn=lambda _s: None)
    assert fatal_attempts == 1


def test_stage_error_mapper_covers_all_contract_error_codes():
    cases: list[tuple[BaseException, str | None, ErrorCode, bool]] = [
        (FatalStageError("LOGIN_REQUIRED", "Sign in to confirm"), "fetch", ErrorCode.LOGIN_REQUIRED, False),
        (FatalStageError("GEO_BLOCKED", "Not available in your country"), "fetch", ErrorCode.GEO_BLOCKED, False),
        (FatalStageError("PRIVATE_VIDEO", "Private video"), "fetch", ErrorCode.PRIVATE_VIDEO, False),
        (FatalStageError("UNSUPPORTED_URL", "Unsupported URL"), "fetch", ErrorCode.UNSUPPORTED_URL, False),
        (FatalStageError("TOO_LONG", "Duration exceeds MAX_VIDEO_HOURS"), "fetch", ErrorCode.TOO_LONG, False),
        (OSError(errno.ENOSPC, "No space left on device"), "render", ErrorCode.DISK_FULL, True),
        (RuntimeError("Whisper ASR model crashed"), "transcribe", ErrorCode.ASR_FAILED, True),
        (TimeoutError("Ollama LLM request timed out"), "select", ErrorCode.LLM_UNAVAILABLE, True),
        (FatalStageError("NO_CLIPS_FOUND", "0 candidates above threshold"), "select", ErrorCode.NO_CLIPS_FOUND, False),
        (FatalStageError("TEMPLATE_INVALID", "Invalid template schema"), "render", ErrorCode.TEMPLATE_INVALID, False),
        (FatalStageError("FONT_MISSING", "Font file not found"), "render", ErrorCode.FONT_MISSING, False),
        (RuntimeError("FFmpeg exited with code 1"), "render", ErrorCode.RENDER_FAILED, True),
        (FatalStageError("DRIVE_AUTH", "Drive 403 unauthorized"), "fetch", ErrorCode.DRIVE_AUTH, False),
        (FatalStageError("DRIVE_NOT_FOUND", "Drive file 404 not found"), "fetch", ErrorCode.DRIVE_NOT_FOUND, False),
        (RetryableError("RATE_LIMITED", "429 Too Many Requests"), "fetch", ErrorCode.RATE_LIMITED, True),
        (ValueError("Invalid input payload"), None, ErrorCode.VALIDATION_ERROR, False),
    ]

    for exc, stage, expected_code, expected_retryable in cases:
        mapped = map_stage_error(exc, stage=stage)
        assert mapped.code == expected_code
        assert mapped.code.value in VALID_ERROR_CODES
        assert mapped.retryable is expected_retryable
        env = mapped.to_envelope()
        assert env["error"]["code"] == expected_code.value
        assert env["error"]["retryable"] is expected_retryable

    blocker = Blocker(
        id="BLK-0099",
        type=BlockerType.SECRET,
        task_id="T-5.1",
        title="Missing GEMINI_API_KEY",
    )
    b_mapped = map_stage_error(BlockerRequired(blocker), stage="select")
    assert b_mapped.code == ErrorCode.BLOCKER_REQUIRED
    assert b_mapped.retryable is True
    assert b_mapped.details["blocker_id"] == "BLK-0099"


def test_disk_quota_lru_eviction_and_orphaned_temp_cleanup(res_env):
    engine, storage, tmp_path, _pw = res_env
    data_dir = tmp_path / "data"

    # Seed 3 completed jobs with different updated_at timestamps + 1 active running job
    jobs_meta = [
        ("job_old_1", JobStatus.COMPLETED.value, "2026-11-18T08:00:00+00:00", 4000),
        ("job_old_2", JobStatus.FAILED.value, "2026-11-18T09:00:00+00:00", 4000),
        ("job_recent_3", JobStatus.COMPLETED.value, "2026-11-18T10:00:00+00:00", 4000),
        ("job_active_4", JobStatus.RENDERING.value, "2026-11-18T07:00:00+00:00", 3000),
    ]

    with Session(engine) as session:
        for jid, st, ts, sz in jobs_meta:
            session.add(
                Job(
                    id=jid,
                    source_kind="url",
                    source_url=f"https://youtu.be/{jid}",
                    status=st,
                    created_at=ts,
                    updated_at=ts,
                )
            )
            session.add(Clip(id=f"clip_{jid}", job_id=jid, rank=1))
            jdir = storage.job_dir(jid)
            (jdir / "source").mkdir(parents=True, exist_ok=True)
            (jdir / "source" / "source.mp4").write_bytes(b"X" * sz)
        session.commit()

    # Create orphaned temp files and staging directory
    orphan1 = data_dir / "jobs" / "job_recent_3" / ".tmp_partial_write"
    orphan2 = data_dir / "jobs" / "job_active_4" / ".staging" / "transcribe_attempt_1"
    orphan1.write_bytes(b"orphan" * 100)
    orphan2.mkdir(parents=True, exist_ok=True)
    (orphan2 / "partial.json").write_text("{}", encoding="utf-8")

    # Enforce quota of 8000 bytes -> must clean orphans and evict job_old_1 and job_old_2 in LRU order,
    # while preserving job_recent_3 and active job_active_4
    with Session(engine) as session:
        summary = enforce_disk_quota_and_evict_lru(
            session,
            storage=storage,
            max_total_bytes=8000,
        )

    assert summary.orphaned_removed >= 2
    assert not orphan1.exists()
    assert not orphan2.exists()
    assert summary.evicted_job_ids == ["job_old_1", "job_old_2"]
    assert summary.freed_bytes >= 8000
    assert summary.remaining_usage_bytes <= 8000
    assert not (data_dir / "jobs" / "job_old_1").exists()
    assert not (data_dir / "jobs" / "job_old_2").exists()
    assert (data_dir / "jobs" / "job_recent_3").exists()
    assert (data_dir / "jobs" / "job_active_4").exists()

    # Unreachable quota raises DISK_FULL FatalStageError
    with Session(engine) as session, pytest.raises(FatalStageError) as exc_info:
        enforce_disk_quota_and_evict_lru(
            session,
            storage=storage,
            max_total_bytes=100,
        )
    assert exc_info.value.code == ErrorCode.DISK_FULL.value


def test_chaos_worker_crash_and_network_loss_recovery(res_env):
    engine, storage, _tmp_path, _pw = res_env
    job_id = "job_chaos_01"

    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                source_kind="url",
                source_url="https://youtu.be/chaos01",
                status=JobStatus.QUEUED.value,
                stage="QUEUED",
            )
        )
        session.commit()

    stage_calls: dict[str, int] = {}

    def _make_chaos_registry(*, crash_on_transcribe: bool, flaky_analyze: bool) -> StageRegistry:
        reg = StageRegistry()
        for st in STAGES:
            def _make_handler(name: str):
                def _runner(ctx: StageContext) -> StageResult:
                    stage_calls[name] = stage_calls.get(name, 0) + 1
                    if name == "transcribe" and crash_on_transcribe:
                        # Simulate orphaned partial temp file left behind when worker is killed mid-stage
                        (ctx.job_dir / ".tmp_crashed_whisper.bin").write_bytes(b"partial")
                        raise RuntimeError("SIMULATED_WORKER_SIGKILL")
                    if name == "analyze" and flaky_analyze and stage_calls[name] == 1:
                        raise RetryableError("LLM_UNAVAILABLE", "Simulated transient network drop")
                    out_dir = ctx.staging_dir / name
                    out_dir.mkdir(parents=True, exist_ok=True)
                    (out_dir / f"{name}.json").write_text(f'{{"stage":"{name}"}}', encoding="utf-8")
                    return StageResult(
                        primary_artefact=f"{name}/{name}.json",
                        metrics={"stage": name},
                    )

                return _runner

            reg.register(st, _make_handler(st), version="1")
        return reg

    # 1. Run job until simulated crash in `transcribe`
    crashed_job = run_job_v2(
        job_id,
        engine=engine,
        storage=storage,
        registry=_make_chaos_registry(crash_on_transcribe=True, flaky_analyze=False),
        retry_backoffs=(0.0, 0.0, 0.0),
    )
    assert crashed_job.status == JobStatus.FAILED.value

    # Simulate a job that was left in TRANSCRIBING state with a RUNNING checkpoint when worker died abruptly
    with Session(engine) as session:
        j = session.get(Job, job_id)
        assert j is not None
        j.status = JobStatus.TRANSCRIBING.value
        j.stage = "transcribe"
        session.add(j)
        session.add(
            StageCheckpoint(
                job_id=job_id,
                stage="transcribe",
                status="RUNNING",
                attempt=2,
            )
        )
        session.commit()

    # 2. Recover interrupted jobs -> cleans orphaned temp files, marks RUNNING checkpoint FAILED, re-queues at transcribe
    with Session(engine) as session:
        recovered = recover_interrupted_jobs(session, storage=storage)
        assert recovered == [job_id]
        j_rec = session.get(Job, job_id)
        assert j_rec is not None
        assert j_rec.status == JobStatus.QUEUED.value
        assert j_rec.stage == "transcribe"
        assert not (storage.job_dir(job_id) / ".tmp_crashed_whisper.bin").exists()

    # 3. Resume job with transient network glitch on `analyze` -> fetch & prepare hit checkpoint cache,
    # analyze retries once and succeeds, job reaches COMPLETED
    stage_calls.clear()
    completed_job = run_job_v2(
        job_id,
        from_stage="transcribe",
        engine=engine,
        storage=storage,
        registry=_make_chaos_registry(crash_on_transcribe=False, flaky_analyze=True),
        retry_backoffs=(0.0, 0.0, 0.0),
    )
    assert completed_job.status == JobStatus.COMPLETED.value
    assert stage_calls.get("fetch", 0) == 0
    assert stage_calls.get("prepare", 0) == 0
    assert stage_calls["transcribe"] == 1
    assert stage_calls["analyze"] == 2  # 1 network failure + 1 retry success
    assert stage_calls["package"] == 1
    assert compute_dir_size_bytes(storage.job_dir(job_id)) > 0
    assert clean_orphaned_temp_files(storage.job_dir(job_id)) == 0


# ---------------------------------------------------------------------------
# T-12.3 Tests: Auth Hardening, Rate Limits, Rights Checkbox & SSRF/Traversal Guards
# ---------------------------------------------------------------------------


def test_bearer_auth_session_cookie_and_rate_limiting_middleware(res_env, monkeypatch: pytest.MonkeyPatch):
    _engine, _storage, _tmp_path, _pw = res_env
    monkeypatch.setenv("CLIPPER_AUTH_TOKEN", "prod-bearer-token-999")

    limiter = TokenBucketRateLimiter(capacity=3, refill_rate_per_sec=0.5, now_fn=lambda: 100.0)
    mini_app = FastAPI()
    mini_app.add_middleware(
        ClipperSecurityMiddleware,
        rate_limiter=limiter,
        auth_token="prod-bearer-token-999",
    )

    @mini_app.get("/api/v1/health")
    def _health():
        return {"ok": True}

    @mini_app.get("/api/v1/protected", dependencies=[Depends(require_bearer_auth)])
    def _protected():
        return {"secret_data": "allowed"}

    client = TestClient(mini_app)

    # 1. /api/v1/health is public even when CLIPPER_AUTH_TOKEN is set
    assert client.get("/api/v1/health").status_code == 200

    # 2. Protected route rejects missing or wrong Bearer token with 401 LOGIN_REQUIRED
    r_unauth = client.get("/api/v1/protected")
    assert r_unauth.status_code == 401
    assert r_unauth.json()["error"]["code"] == "LOGIN_REQUIRED"

    r_bad = client.get("/api/v1/protected", headers={"Authorization": "Bearer wrong-token"})
    assert r_bad.status_code == 401

    # 3. Valid Bearer token succeeds up to rate limit capacity (3 requests)
    headers = {"Authorization": "Bearer prod-bearer-token-999"}
    for _ in range(3):
        ok_resp = client.get("/api/v1/protected", headers=headers)
        assert ok_resp.status_code == 200
        assert ok_resp.json()["secret_data"] == "allowed"

    # 4. 4th request exceeds token bucket -> 429 RATE_LIMITED with Retry-After header
    r_limited = client.get("/api/v1/protected", headers=headers)
    assert r_limited.status_code == 429
    assert r_limited.json()["error"]["code"] == "RATE_LIMITED"
    assert "Retry-After" in r_limited.headers

    # 5. Admin session cookie is also accepted as valid auth
    limiter.reset()
    admin_tok = issue_admin_session_token()
    assert verify_bearer_or_session_auth(
        headers={},
        cookies={ADMIN_SESSION_COOKIE: admin_tok},
        expected_token="prod-bearer-token-999",
        path="/api/v1/protected",
    ) is True


def test_sliding_window_rate_limiter_per_ip_and_route():
    now_val = 1000.0
    sw = SlidingWindowRateLimiter(
        max_requests=2,
        window_seconds=10.0,
        now_fn=lambda: now_val,
    )

    assert sw.check("10.0.0.1", "/api/v1/jobs").allowed is True
    assert sw.check("10.0.0.1", "/api/v1/jobs").allowed is True
    d3 = sw.check("10.0.0.1", "/api/v1/jobs")
    assert d3.allowed is False
    assert d3.retry_after_s == 10.0

    # Different route or IP has its own independent window
    assert sw.check("10.0.0.1", "/api/v1/clips").allowed is True
    assert sw.check("10.0.0.2", "/api/v1/jobs").allowed is True

    # Advance clock past window -> allowed again
    now_val = 1011.0
    assert sw.check("10.0.0.1", "/api/v1/jobs").allowed is True


def test_rights_confirmation_enforcement():
    # Explicit false is always rejected
    with pytest.raises(SecurityError) as exc1:
        enforce_rights_confirmation({"rights_confirmed": False})
    assert exc1.value.status_code == 422

    with pytest.raises(SecurityError) as exc2:
        enforce_rights_confirmation(confirm_rights=False)
    assert exc2.value.status_code == 422

    # In strict mode or CLIPPER_REQUIRE_RIGHTS_CONFIRMATION=true, omitted/None is rejected
    with pytest.raises(SecurityError) as exc3:
        enforce_rights_confirmation({}, env={"CLIPPER_REQUIRE_RIGHTS_CONFIRMATION": "true"})
    assert exc3.value.status_code == 422

    with pytest.raises(SecurityError) as exc4:
        enforce_rights_confirmation({}, strict=True)
    assert exc4.value.status_code == 422

    # Explicit True passes in strict mode (both rights_confirmed and confirm_rights aliases)
    assert enforce_rights_confirmation(
        {"rights_confirmed": True},
        env={"CLIPPER_REQUIRE_RIGHTS_CONFIRMATION": "true"},
    ) is True
    assert enforce_rights_confirmation(
        {"confirm_rights": True},
        strict=True,
    ) is True


def test_ssrf_and_path_traversal_guards(tmp_path: Path):
    # Valid external URLs and internal schemes allowed
    assert (
        validate_safe_source_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    assert validate_safe_source_url("sample://sample_hinglish_podcast") == "sample://sample_hinglish_podcast"
    assert validate_safe_source_url("drive://16EnG42pTWexYiJYao3sSM5XLhqmaeNB-") == "drive://16EnG42pTWexYiJYao3sSM5XLhqmaeNB-"

    # Forbidden SSRF targets: file://, gopher://, localhost, loopback, RFC1918, cloud metadata, IPv6 loopback
    forbidden_urls = [
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_INFO",
        "http://localhost:8000/api/v1/admin/settings",
        "http://127.0.0.1:11434/api/tags",
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://10.0.0.5/internal.mp4",
        "http://192.168.1.10/router.mp4",
        "http://172.16.0.12/secret.mp4",
        "http://[::1]:8000/admin",
        "sample://../etc/passwd",
    ]
    for bad_url in forbidden_urls:
        with pytest.raises(SecurityError) as exc:
            validate_safe_source_url(bad_url)
        assert exc.value.code == ErrorCode.UNSUPPORTED_URL.value

    # DNS rebinding check via custom resolver
    with pytest.raises(SecurityError):
        validate_safe_source_url(
            "https://evil-rebind.example.com/video.mp4",
            resolver=lambda _host: ["169.254.169.254"],
        )

    assert (
        validate_safe_source_url(
            "https://cdn.example.com/video.mp4",
            resolver=lambda _host: ["93.184.216.34"],
        )
        == "https://cdn.example.com/video.mp4"
    )

    # Path traversal and symlink escape protection
    base_dir = tmp_path / "workspace"
    base_dir.mkdir(parents=True, exist_ok=True)
    valid_file = base_dir / "jobs" / "job_01" / "source.mp4"
    valid_file.parent.mkdir(parents=True, exist_ok=True)
    valid_file.write_bytes(b"ok")

    resolved = validate_safe_file_path(base_dir, "jobs/job_01/source.mp4")
    assert resolved == valid_file.resolve()

    for bad_path in (
        "../outside.txt",
        "jobs/../../etc/passwd",
        "/etc/passwd",
        "jobs/job_01/bad\x00.mp4",
    ):
        with pytest.raises(SecurityError):
            validate_safe_file_path(base_dir, bad_path)

    outside_target = tmp_path / "secret.txt"
    outside_target.write_text("top-secret", encoding="utf-8")
    symlink_path = base_dir / "jobs" / "escape_link.txt"
    symlink_path.symlink_to(outside_target)

    with pytest.raises(SecurityError):
        validate_safe_file_path(base_dir, "jobs/escape_link.txt")
