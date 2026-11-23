"""Thực thi nền: thread pool giới hạn concurrency (inline) hoặc Celery (Redis)."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from ..config import get_settings

log = logging.getLogger(__name__)
_pool: ThreadPoolExecutor | None = None
_inflight: dict[str, Future] = {}
_lock = threading.Lock()


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=get_settings().job_concurrency, thread_name_prefix="evcs-job")
    return _pool


def submit_pipeline(story_id: str, actor: str = "system", stop_at: str | None = None) -> str:
    s = get_settings()
    if s.job_executor == "celery":
        from .celery_app import run_pipeline_task

        r = run_pipeline_task.delay(story_id, actor, stop_at)
        return f"celery:{r.id}"
    with _lock:
        f = _inflight.get(story_id)
        if f and not f.done():
            return "already-running"

        def _run():
            from .jobs import run_pipeline

            try:
                return run_pipeline(story_id, actor=actor, stop_at=stop_at)
            except Exception:  # pragma: no cover
                log.exception("Pipeline nền lỗi")

        _inflight[story_id] = _get_pool().submit(_run)
    return "queued"


def submit_callable(key: str, fn) -> str:
    with _lock:
        f = _inflight.get(key)
        if f and not f.done():
            return "already-running"
        _inflight[key] = _get_pool().submit(fn)
    return "queued"


def is_running(story_id: str) -> bool:
    f = _inflight.get(story_id)
    return bool(f and not f.done())
