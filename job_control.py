"""Отмена фоновых задач конвертации (очередь + прерывание subprocess)."""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Any

_lock = threading.Lock()
_cancelled: set[str] = set()
_active_job_id: str | None = None
_child_procs: list[subprocess.Popen[Any]] = []


class JobCancelledError(Exception):
    """Задача отменена пользователем."""


def begin_job(job_id: str) -> None:
    global _active_job_id
    with _lock:
        _active_job_id = job_id


def end_job(job_id: str) -> None:
    global _active_job_id
    with _lock:
        if _active_job_id == job_id:
            _active_job_id = None
        _child_procs.clear()


def mark_cancelled(job_id: str) -> None:
    with _lock:
        _cancelled.add(job_id)


def clear_cancelled(job_id: str) -> None:
    with _lock:
        _cancelled.discard(job_id)


def is_cancelled(job_id: str | None) -> bool:
    if not job_id:
        return False
    with _lock:
        return job_id in _cancelled


def check_cancelled() -> None:
    with _lock:
        job_id = _active_job_id
        cancelled = job_id in _cancelled if job_id else False
    if cancelled:
        kill_children()
        raise JobCancelledError("Отменено пользователем")


def kill_children() -> None:
    with _lock:
        procs = list(_child_procs)
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def register_popen(proc: subprocess.Popen[Any]) -> None:
    with _lock:
        _child_procs.append(proc)


def unregister_popen(proc: subprocess.Popen[Any]) -> None:
    with _lock:
        try:
            _child_procs.remove(proc)
        except ValueError:
            pass


def run_monitored(
    cmd: list[str],
    *,
    timeout: float | None = None,
    text: bool = True,
    preexec_fn=None,
) -> subprocess.CompletedProcess[str]:
    """subprocess.run с опросом отмены и возможностью kill."""
    check_cancelled()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        preexec_fn=preexec_fn,
    )
    register_popen(proc)
    started = time.time()
    try:
        while True:
            if is_cancelled(_active_job_id):
                proc.kill()
                proc.wait(timeout=5)
                raise JobCancelledError("Отменено пользователем")
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if timeout is not None and time.time() - started > timeout:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise subprocess.TimeoutExpired(cmd, timeout) from None
        return subprocess.CompletedProcess(
            cmd, proc.returncode or 0, stdout, stderr
        )
    finally:
        unregister_popen(proc)
