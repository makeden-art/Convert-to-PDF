"""Очередь задач конвертации + история на диске."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from converter import release_memory
from job_control import (
    JobCancelledError,
    begin_job,
    clear_cancelled,
    end_job,
    is_cancelled,
    kill_children,
    mark_cancelled,
)

JOBS_DIR = Path(os.getenv("CONVERT_JOBS_DIR", "/data/convert-jobs"))
HISTORY_FILE = JOBS_DIR / "history.json"
_HISTORY_MAX = int(os.getenv("CONVERT_JOBS_HISTORY_MAX", "100"))

_jobs: dict[str, dict[str, Any]] = {}
_fns: dict[str, Callable[[], Any]] = {}
_lock = threading.Lock()
_queue: queue.Queue[str] = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False
_pending_ids: list[str] = []


def _ensure_dir() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _load_history() -> None:
    _ensure_dir()
    if not HISTORY_FILE.exists():
        return
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            changed = False
            now = time.time()
            with _lock:
                for item in data:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    if item.get("status") in ("running", "queued"):
                        item["status"] = "error"
                        item["error"] = "Прервано перезапуском сервиса"
                        item["finished"] = now
                        changed = True
                    _jobs[item["id"]] = item
            if changed:
                _save_history()
    except (json.JSONDecodeError, OSError):
        pass


def _save_history() -> None:
    _ensure_dir()
    with _lock:
        items = sorted(
            _jobs.values(),
            key=lambda j: float(j.get("created", 0)),
            reverse=True,
        )[:_HISTORY_MAX]
        out = []
        for job in items:
            row = {k: v for k, v in job.items() if k != "result"}
            out.append(row)
    try:
        HISTORY_FILE.write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _summarize_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"note": str(result)[:300]}
    summary: dict[str, Any] = {}
    for key in ("merge", "merged_pdf", "pages_from", "total", "folder", "recursive"):
        if key in result:
            summary[key] = result[key]
    if "stats" in result and isinstance(result["stats"], dict):
        summary["stats"] = dict(result["stats"])
    for key in ("engine", "render_mode", "frames_rendered", "fallback"):
        if key in result:
            summary[key] = result[key]
    raw_files = result.get("files")
    if isinstance(raw_files, list):
        summary["files"] = [
            {
                "path": str(item.get("source") or item.get("path") or ""),
                "name": Path(str(item.get("source") or item.get("path") or "")).name,
                "status": item.get("status"),
                "message": (item.get("message") or "")[:120],
            }
            for item in raw_files[:500]
            if isinstance(item, dict)
        ]
        if len(raw_files) > 500:
            summary["files_truncated"] = True
    return summary


def _update_job(job_id: str, **fields: Any) -> None:
    with _lock:
        if job_id not in _jobs:
            _jobs[job_id] = {"id": job_id}
        _jobs[job_id].update(fields)
    _save_history()


def _queue_position(job_id: str) -> int | None:
    """0 = выполняется, 1+ = место в очереди, None = завершена."""
    with _lock:
        running = [jid for jid, j in _jobs.items() if j.get("status") == "running"]
        queued = sorted(
            [jid for jid, j in _jobs.items() if j.get("status") == "queued"],
            key=lambda jid: float(_jobs[jid].get("created", 0)),
        )
    if job_id in running:
        return 0
    if job_id in queued:
        return queued.index(job_id) + (1 if running else 0) + 1
    return None


def _public_job(job_id: str, *, include_result: bool = False) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        out = dict(job)
    out["job_id"] = job_id
    pos = _queue_position(job_id)
    if pos is not None:
        out["queue_position"] = pos
    if include_result and job.get("status") == "done" and "result" in job:
        out["result"] = job["result"]
    elif include_result and job.get("status") == "error":
        out["error"] = job.get("error", "ошибка")
    elif include_result and job.get("status") == "cancelled":
        out["error"] = job.get("error", "Отменено пользователем")
    out.pop("id", None)
    return out


def _run_job(job_id: str) -> None:
    fn = _fns.pop(job_id, None)
    if not fn:
        if is_cancelled(job_id):
            _update_job(
                job_id,
                status="cancelled",
                error="Отменено пользователем",
                finished=time.time(),
            )
            clear_cancelled(job_id)
        else:
            _update_job(job_id, status="error", error="Задача не найдена", finished=time.time())
        return

    if is_cancelled(job_id):
        _update_job(
            job_id,
            status="cancelled",
            error="Отменено пользователем",
            finished=time.time(),
        )
        clear_cancelled(job_id)
        return

    begin_job(job_id)
    _update_job(job_id, status="running", started=time.time())
    try:
        result = fn()
        if is_cancelled(job_id):
            raise JobCancelledError("Отменено пользователем")
        _update_job(
            job_id,
            status="done",
            result=result,
            result_summary=_summarize_result(result),
            finished=time.time(),
        )
    except JobCancelledError as e:
        _update_job(
            job_id,
            status="cancelled",
            error=str(e),
            finished=time.time(),
        )
    except Exception as e:
        if is_cancelled(job_id):
            _update_job(
                job_id,
                status="cancelled",
                error="Отменено пользователем",
                finished=time.time(),
            )
        else:
            _update_job(job_id, status="error", error=str(e), finished=time.time())
    finally:
        end_job(job_id)
        clear_cancelled(job_id)
        release_memory()


def _queue_worker() -> None:
    while True:
        job_id = _queue.get()
        try:
            _run_job(job_id)
        finally:
            _queue.task_done()
            release_memory()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(
            target=_queue_worker,
            daemon=True,
            name="convert-queue-worker",
        ).start()


def create_job(
    fn: Callable[[], Any],
    *,
    label: str,
    kind: str = "convert",
    meta: dict[str, Any] | None = None,
) -> str:
    job_id = uuid.uuid4().hex
    now = time.time()
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "label": label,
            "kind": kind,
            "meta": meta or {},
            "created": now,
        }
        _fns[job_id] = fn
    _save_history()
    _queue.put(job_id)
    _ensure_worker()
    return job_id


def cancel_job(job_id: str) -> dict[str, Any]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "Задача не найдена"}
        status = job.get("status")

    if status in ("done", "error", "cancelled"):
        return {"ok": False, "error": f"Задача уже завершена ({status})"}

    mark_cancelled(job_id)

    if status == "queued":
        _fns.pop(job_id, None)
        _update_job(
            job_id,
            status="cancelled",
            error="Отменено пользователем",
            finished=time.time(),
        )
        clear_cancelled(job_id)
        return {"ok": True, "job_id": job_id, "status": "cancelled"}

    if status == "running":
        kill_children()
        return {"ok": True, "job_id": job_id, "status": "cancelling"}

    return {"ok": False, "error": f"Нельзя отменить задачу в статусе {status}"}


def get_job(job_id: str) -> dict[str, Any] | None:
    return _public_job(job_id, include_result=True)


def list_jobs(*, limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        items = sorted(
            _jobs.values(),
            key=lambda j: float(j.get("created", 0)),
            reverse=True,
        )
    out: list[dict[str, Any]] = []
    for job in items[:limit]:
        jid = job.get("id") or job.get("job_id")
        if not jid:
            continue
        row = _public_job(jid, include_result=False)
        if row:
            out.append(row)
    return out


def queue_status() -> dict[str, Any]:
    with _lock:
        running = [
            j
            for j in _jobs.values()
            if j.get("status") == "running"
        ]
        queued = sorted(
            [j for j in _jobs.values() if j.get("status") == "queued"],
            key=lambda j: float(j.get("created", 0)),
        )
    return {
        "running": len(running),
        "queued": len(queued),
        "running_job": (
            {
                "job_id": running[0].get("id"),
                "label": running[0].get("label"),
                "started": running[0].get("started"),
            }
            if running
            else None
        ),
        "queue": [
            {"job_id": j.get("id"), "label": j.get("label"), "created": j.get("created")}
            for j in queued
        ],
    }


_load_history()
