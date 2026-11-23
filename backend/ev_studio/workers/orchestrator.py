"""Orchestrator: job có idempotency key + checkpoint, retry exponential backoff, lease theo story, state machine."""
from __future__ import annotations

import hashlib
import json
import logging
import socket
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import BlockedByCredentials, GateBlocked, PermanentError, RetryableError
from ..models import Job, Story, now
from ..security import audit, redact
from ..state_machine import StoryState, assert_transition

log = logging.getLogger(__name__)
WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"


def input_hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def transition(session: Session, story: Story, dst: StoryState, actor: str = "system", note: str = "") -> None:
    src = StoryState(story.state)
    if src == dst:
        return
    assert_transition(src, dst, StoryState(story.resume_state) if story.resume_state else None)
    if dst in (StoryState.FAILED_RETRYABLE, StoryState.FAILED_PERMANENT, StoryState.NEEDS_REVIEW):
        story.resume_state = src.value
    elif src in (StoryState.FAILED_RETRYABLE, StoryState.FAILED_PERMANENT, StoryState.NEEDS_REVIEW):
        story.resume_state = None
        story.blockers = []
    story.state = dst.value
    audit(session, actor, "story.transition", "story", story.id, src=src.value, dst=dst.value, note=note)
    session.flush()


def acquire_lease(session: Session, story_id: str, seconds: int = 3600) -> bool:
    t = now()
    res = session.execute(
        update(Story).where(Story.id == story_id, or_(Story.lease_owner.is_(None), Story.lease_expires_at < t,
                                                      Story.lease_owner == WORKER_ID))
        .values(lease_owner=WORKER_ID, lease_expires_at=t + timedelta(seconds=seconds))
    )
    session.commit()
    return res.rowcount == 1


def release_lease(session: Session, story_id: str) -> None:
    session.execute(update(Story).where(Story.id == story_id, Story.lease_owner == WORKER_ID)
                    .values(lease_owner=None, lease_expires_at=None))
    session.commit()


def run_job(session: Session, story: Story | None, job_type: str, ihash: str, fn: Callable[[], dict],
            max_attempts: int | None = None) -> dict:
    """Chạy fn một lần duy nhất cho mỗi idempotency key; succeeded → trả output cũ (checkpoint)."""
    s = get_settings()
    key = f"{job_type}:{story.id if story else '-'}:{ihash}"
    job = session.query(Job).filter_by(idempotency_key=key).one_or_none()
    if job and job.status == "succeeded":
        log.info("Checkpoint: bỏ qua %s (đã thành công)", key)
        return job.output
    if job is None:
        job = Job(story_id=story.id if story else None, job_type=job_type, idempotency_key=key, input_hash=ihash,
                  max_attempts=max_attempts or s.job_max_attempts)
        session.add(job)
        try:
            session.commit()
        except IntegrityError:  # worker khác vừa tạo → đọc lại
            session.rollback()
            job = session.query(Job).filter_by(idempotency_key=key).one()
            if job.status == "succeeded":
                return job.output
    while True:
        job.attempts += 1
        job.status, job.started_at = "running", now()
        session.commit()
        t0 = time.time()
        try:
            out = fn() or {}
            job.status, job.output, job.error = "succeeded", out, None
            job.finished_at, job.duration_seconds = now(), round(time.time() - t0, 3)
            job.history = job.history + [{"attempt": job.attempts, "status": "succeeded", "at": now().isoformat()}]
            session.commit()
            return out
        except RetryableError as e:
            session.rollback()
            job = session.get(Job, job.id)
            msg = redact(e)
            job.history = job.history + [{"attempt": job.attempts, "status": "retryable_error", "error": msg, "at": now().isoformat()}]
            job.error = msg
            if job.attempts >= job.max_attempts:
                job.status, job.finished_at = "failed_retryable", now()
                session.commit()
                raise
            job.status = "queued"
            session.commit()
            delay = s.job_backoff_base_seconds * (2 ** (job.attempts - 1))
            log.warning("%s lỗi tạm (%s), thử lại sau %.1fs", job_type, msg, delay)
            time.sleep(min(delay, 300))
        except (GateBlocked, BlockedByCredentials) as e:
            session.rollback()
            job = session.get(Job, job.id)
            job.status, job.error, job.finished_at = "blocked", redact(e), now()
            job.history = job.history + [{"attempt": job.attempts, "status": "blocked", "error": redact(e), "at": now().isoformat()}]
            session.commit()
            raise
        except Exception as e:
            session.rollback()
            job = session.get(Job, job.id)
            job.status, job.error, job.finished_at = "failed_permanent", redact(f"{type(e).__name__}: {e}"), now()
            job.history = job.history + [{"attempt": job.attempts, "status": "failed_permanent", "error": job.error,
                                          "trace": redact(traceback.format_exc()[-1500:]), "at": now().isoformat()}]
            session.commit()
            raise


def fail_story(session: Session, story: Story, e: Exception) -> None:
    session.rollback()
    story = session.get(Story, story.id)
    if isinstance(e, GateBlocked):
        story.blockers = e.reasons
        dst = StoryState.NEEDS_REVIEW
    elif isinstance(e, BlockedByCredentials):
        story.blockers = [f"BLOCKED_BY_CREDENTIALS: {redact(e)}"]
        dst = StoryState.NEEDS_REVIEW
    elif isinstance(e, RetryableError):
        dst = StoryState.FAILED_RETRYABLE
    else:
        dst = StoryState.FAILED_PERMANENT
    story.last_error = redact(f"{type(e).__name__}: {e}")[:4000]
    try:
        transition(session, story, dst, note=story.last_error[:300])
    except Exception:  # trạng thái không failable (vd: đã APPROVED) → chỉ ghi lỗi
        pass
    session.commit()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
