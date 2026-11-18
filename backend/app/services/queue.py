from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from huey import Huey, MemoryHuey, RedisHuey, SqliteHuey  # type: ignore[import-untyped]

from backend.app.config import Settings, get_settings

_queue_lock = threading.RLock()
_cancel_lock = threading.RLock()
_cancel_requested: set[str] = set()
_active_processes: dict[str, dict[int, subprocess.Popen[Any] | None]] = {}


def _resolve_sqlite_path(queue_url: str, settings: Settings) -> Path:
    raw = (queue_url or "").strip()
    if raw.startswith("sqlite:///"):
        path_str = raw[len("sqlite:///") :]
        p = Path(path_str) if path_str else (settings.data_dir / "huey.db")
    elif raw:
        p = Path(raw)
    else:
        p = settings.data_dir / "huey.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def build_huey(
    settings: Settings | None = None,
    *,
    queue_url: str | None = None,
    immediate: bool | None = None,
) -> Huey:
    """Create a configured Huey instance (SqliteHuey, MemoryHuey, or RedisHuey)."""
    cfg = settings or get_settings()
    url = (cfg.queue_url if queue_url is None else queue_url).strip()
    mode = (cfg.queue_mode or "immediate").strip().lower()
    is_immediate = (mode == "immediate") if immediate is None else bool(immediate)

    if url.startswith("memory://"):
        return MemoryHuey(name="clipper", immediate=is_immediate)

    if mode == "huey_redis" or url.startswith("redis://"):
        redis_url = url if url.startswith("redis://") else cfg.redis_url
        return RedisHuey(name="clipper", url=redis_url, immediate=is_immediate)

    db_path = _resolve_sqlite_path(url, cfg)
    return SqliteHuey(
        name="clipper",
        filename=str(db_path),
        immediate=is_immediate,
    )


def _run_job_task_impl(job_id: str, from_stage: str | None = None) -> str:
    from backend.app.services.orchestrator import run_job_v2

    job = run_job_v2(job_id, from_stage=from_stage)
    return job.status


huey: Huey = build_huey()
run_job_task = huey.task()(_run_job_task_impl)


def get_huey() -> Huey:
    global huey, run_job_task
    with _queue_lock:
        if huey is None:  # pragma: no cover - defensive guard
            huey = build_huey()
            run_job_task = huey.task()(_run_job_task_impl)
        return huey


def configure_queue(
    *,
    settings: Settings | None = None,
    queue_url: str | None = None,
    immediate: bool | None = None,
) -> Huey:
    """Reconfigure the module-level Huey instance and rebind the job task."""
    global huey, run_job_task
    with _queue_lock:
        huey = build_huey(settings=settings, queue_url=queue_url, immediate=immediate)
        run_job_task = huey.task()(_run_job_task_impl)
        return huey


def reset_queue() -> Huey:
    """Rebuild the Huey instance from current settings."""
    return configure_queue(settings=get_settings())


def enqueue_job(job_id: str, from_stage: str | None = None) -> Any:
    """Enqueue a Spec v2 job execution task onto Huey."""
    with _queue_lock:
        task_fn = run_job_task
    return task_fn(job_id, from_stage=from_stage)


def get_queue_depth() -> int:
    """Return the number of pending + scheduled tasks in Huey."""
    q = get_huey()
    pending = int(q.pending_count()) if hasattr(q, "pending_count") else 0
    scheduled = int(q.scheduled_count()) if hasattr(q, "scheduled_count") else 0
    return pending + scheduled


def request_cancel(job_id: str) -> None:
    """Set the cooperative cancellation flag for a job."""
    with _cancel_lock:
        _cancel_requested.add(job_id)


def is_cancel_requested(job_id: str) -> bool:
    """Check whether cooperative cancellation has been requested for a job."""
    with _cancel_lock:
        return job_id in _cancel_requested


def clear_cancel(job_id: str) -> None:
    """Clear the cooperative cancellation flag for a job."""
    with _cancel_lock:
        _cancel_requested.discard(job_id)


def _extract_pid(proc_or_pid: subprocess.Popen[Any] | int) -> tuple[int, subprocess.Popen[Any] | None]:
    if isinstance(proc_or_pid, int):
        return proc_or_pid, None
    return int(proc_or_pid.pid), proc_or_pid


def register_process(job_id: str, proc_or_pid: subprocess.Popen[Any] | int) -> int:
    """Register an active subprocess (Popen or PID) for a job so cancel can terminate it."""
    pid, proc = _extract_pid(proc_or_pid)
    with _cancel_lock:
        _active_processes.setdefault(job_id, {})[pid] = proc
    return pid


def unregister_process(job_id: str, proc_or_pid: subprocess.Popen[Any] | int) -> None:
    """Remove a finished subprocess from the active job process registry."""
    pid, _ = _extract_pid(proc_or_pid)
    with _cancel_lock:
        procs = _active_processes.get(job_id)
        if procs is not None:
            procs.pop(pid, None)
            if not procs:
                _active_processes.pop(job_id, None)


def get_registered_pids(job_id: str) -> list[int]:
    """Return registered active PIDs for a job."""
    with _cancel_lock:
        return sorted(_active_processes.get(job_id, {}).keys())


@contextmanager
def track_process(job_id: str, proc: subprocess.Popen[Any]) -> Iterator[subprocess.Popen[Any]]:
    """Context manager registering a Popen subprocess for cancellation cleanup."""
    register_process(job_id, proc)
    try:
        yield proc
    finally:
        unregister_process(job_id, proc)


def _is_pid_alive(pid: int, proc: subprocess.Popen[Any] | None) -> bool:
    if proc is not None:
        return proc.poll() is None

    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return False
        if waited_pid == 0:
            return True
    except ChildProcessError:
        pass
    except OSError:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            stat_text = stat_path.read_text(encoding="utf-8", errors="ignore")
            rparen = stat_text.rfind(")")
            if rparen != -1 and len(stat_text) > rparen + 2:
                state = stat_text[rparen + 2]
                if state == "Z":
                    return False
        except OSError:
            pass
    return True


def _send_signal(pid: int, proc: subprocess.Popen[Any] | None, sig: signal.Signals) -> bool:
    if proc is not None:
        if proc.poll() is not None:
            return False
        try:
            if sig == signal.SIGTERM:
                proc.terminate()
            elif sig == signal.SIGKILL:
                proc.kill()
            else:
                os.kill(pid, sig)
            return True
        except ProcessLookupError:
            return False

    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False


def terminate_job_processes(
    job_id: str,
    *,
    grace_seconds: float = 10.0,
    poll_interval: float = 0.05,
) -> list[int]:
    """Send SIGTERM to active job subprocesses, then SIGKILL after grace_seconds if still alive."""
    with _cancel_lock:
        entries = list(_active_processes.get(job_id, {}).items())

    if not entries:
        return []

    signaled: list[int] = []
    still_running: list[tuple[int, subprocess.Popen[Any] | None]] = []

    for pid, proc in entries:
        if _is_pid_alive(pid, proc) and _send_signal(pid, proc, signal.SIGTERM):
            signaled.append(pid)
            still_running.append((pid, proc))

    deadline = time.monotonic() + max(0.0, grace_seconds)
    while still_running and time.monotonic() < deadline:
        remaining: list[tuple[int, subprocess.Popen[Any] | None]] = []
        for pid, proc in still_running:
            if _is_pid_alive(pid, proc):
                remaining.append((pid, proc))
        still_running = remaining
        if still_running:
            time.sleep(min(poll_interval, max(0.001, deadline - time.monotonic())))

    for pid, proc in still_running:
        if _is_pid_alive(pid, proc):
            _send_signal(pid, proc, signal.SIGKILL)
            if proc is not None:
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
            else:
                try:
                    os.waitpid(pid, os.WNOHANG)
                except OSError:
                    pass

    with _cancel_lock:
        procs = _active_processes.get(job_id)
        if procs is not None:
            for pid, proc in entries:
                if not _is_pid_alive(pid, proc):
                    procs.pop(pid, None)
            if not procs:
                _active_processes.pop(job_id, None)

    return signaled
