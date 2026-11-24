"""Lịch: quét tin mỗi N giờ (+Autopilot nếu bật), làm mới trạng thái video đã upload. Chỉ chạy khi bật trong Settings."""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal

log = logging.getLogger(__name__)
_scheduler = None


def scan_and_autopilot() -> dict:
    from ..services.ingest import run_scan, scan_writer
    from .jobs import autopilot_tick, get_setting

    with SessionLocal() as db:
        if not get_setting(db, "scan_schedule_enabled", False):
            return {"ran": False, "reason": "Lịch quét đang tắt"}
        writer, warn = scan_writer()
        stats = run_scan(db, writer=writer, actor="scheduler", warnings=[warn] if warn else None)
        for w in stats.get("warnings", []):
            log.info("Quét tin theo lịch: %s", w)
        for r in stats.get("sources", []):
            if not r["ok"]:
                log.warning("Quét tin theo lịch — nguồn %s: %s", r["source"], r.get("error"), extra={"evcs_source": "scheduler"})
        db.commit()
        ap = autopilot_tick(db)
        return {"scan": stats, "autopilot": ap}


def refresh_statuses() -> int:
    from ..models import Story
    from ..providers import Providers
    from .jobs import refresh_status_job

    n = 0
    with SessionLocal() as db:
        for st in db.scalars(select(Story).where(Story.state.in_(["EDITOR_REVIEW", "APPROVED_PRIVATE"]))):
            try:
                refresh_status_job(db, st, Providers())
                db.commit()
                n += 1
            except Exception:
                db.rollback()
                log.exception("RefreshStatusJob lỗi cho %s", st.id, extra={"evcs_story_id": st.id})
    return n


def weekly_reflect() -> list:
    """Ban Đào tạo đúc kết bài học thành sổ tay văn phong (model rẻ, có trần ngân sách riêng 3%)."""
    from ..agents.learning import reflect_all
    from ..agents.writer import build_brain
    from ..errors import BlockedByCredentials

    s = get_settings()
    if s.is_demo or not s.agents_enabled:
        return []
    try:
        brain = build_brain()
    except BlockedByCredentials:
        return []
    return reflect_all(brain)


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    s = get_settings()
    _scheduler = BackgroundScheduler(timezone=s.timezone)
    _scheduler.add_job(scan_and_autopilot, "interval", hours=s.scan_interval_hours, id="scan", max_instances=1, coalesce=True)
    _scheduler.add_job(refresh_statuses, "interval", hours=6, id="refresh", max_instances=1, coalesce=True)
    _scheduler.add_job(weekly_reflect, "cron", day_of_week="sun", hour=3, minute=17, id="reflect", max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Scheduler đã chạy (quét mỗi %.1fh nếu bật trong Settings)", s.scan_interval_hours)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
