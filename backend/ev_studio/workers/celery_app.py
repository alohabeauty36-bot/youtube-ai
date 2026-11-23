"""Celery worker (tuỳ chọn, JOB_EXECUTOR=celery). Chạy: celery -A ev_studio.workers.celery_app worker -Q pipeline -c 2"""
from __future__ import annotations

from celery import Celery

from ..config import get_settings

s = get_settings()
app = Celery("ev_studio", broker=s.redis_url, backend=s.redis_url)
app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_default_queue="pipeline",
                task_routes={"ev_studio.pipeline": {"queue": "pipeline"}})


@app.task(name="ev_studio.pipeline", bind=True, max_retries=0)
def run_pipeline_task(self, story_id: str, actor: str = "system", stop_at: str | None = None):
    # retry/backoff/idempotency do orchestrator xử lý (theo job); lease ngăn hai worker chạy cùng story
    from .jobs import run_pipeline

    return run_pipeline(story_id, actor=actor, stop_at=stop_at)


@app.task(name="ev_studio.scan_and_autopilot")
def scan_and_autopilot_task():
    from .schedules import scan_and_autopilot

    return scan_and_autopilot()
