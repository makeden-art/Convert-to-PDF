"""Фоновые задачи конвертации — HTTP сразу возвращает job_id, клиент опрашивает статус."""
from __future__ import annotations

import gc
import threading
import time
import uuid
from typing import Any, Callable

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_MAX_JOBS = 30
_JOB_TTL_SEC = 3600


def _cleanup_old_jobs() -> None:
    now = time.time()
    stale = [
        jid
        for jid, job in _jobs.items()
        if job.get("status") in ("done", "error")
        and now - float(job.get("finished", job.get("created", now))) > _JOB_TTL_SEC
    ]
    for jid in stale:
        _jobs.pop(jid, None)


def create_job(fn: Callable[[], Any]) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        _cleanup_old_jobs()
        _jobs[job_id] = {"status": "queued", "created": time.time()}

    def worker() -> None:
        with _lock:
            _jobs[job_id]["status"] = "running"
            _jobs[job_id]["started"] = time.time()
        try:
            result = fn()
            with _lock:
                _jobs[job_id].update(
                    status="done",
                    result=result,
                    finished=time.time(),
                )
        except Exception as e:
            with _lock:
                _jobs[job_id].update(
                    status="error",
                    error=str(e),
                    finished=time.time(),
                )
        finally:
            gc.collect()

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"convert-job-{job_id[:8]}",
    ).start()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
