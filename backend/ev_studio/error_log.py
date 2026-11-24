"""Nhật ký lỗi của tool (màn hình "Nhật ký lỗi").

Mọi log từ WARNING trở lên của các logger `ev_studio.*` (và APScheduler) được ghi vào bảng `error_logs`:
- secrets được che bằng `redact` trước khi lưu;
- lỗi giống nhau (cùng nguồn, loại, nội dung đã chuẩn hoá, story) chưa xử lý được gộp lại (`count`, `last_seen_at`);
- ghi qua hàng đợi + thread nền để không bao giờ chặn pipeline (SQLite chỉ cho một writer tại một thời điểm);
- bảng tự cắt bớt khi vượt `MAX_ROWS`.

Ngữ cảnh (story/job) lấy từ `error_context(...)` hoặc từ `extra={"evcs_story_id": ...}` khi gọi logger.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import queue
import re
import threading
import traceback as tb
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

LEVELS = ("WARNING", "ERROR", "CRITICAL")
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}
MAX_ROWS = 5000
CAPTURE_PREFIXES = ("ev_studio", "apscheduler")

SOURCE_LABEL = {
    "pipeline": "Pipeline", "job": "Job", "ingest": "Quét tin", "factcheck": "Kiểm chứng", "llm": "Claude / kịch bản",
    "tts": "Giọng đọc", "media": "Tư liệu", "render": "Dựng video", "youtube": "YouTube", "telegram": "Telegram",
    "scheduler": "Lịch chạy", "api": "API / giao diện", "config": "Cấu hình", "system": "Hệ thống",
}

# thứ tự quan trọng: tiền tố dài/cụ thể đứng trước
_SOURCE_BY_LOGGER = [
    ("ev_studio.workers.orchestrator", "job"), ("ev_studio.workers.schedules", "scheduler"), ("ev_studio.workers", "pipeline"),
    ("ev_studio.services.ingest", "ingest"), ("ev_studio.services.sources", "ingest"), ("ev_studio.services.ranker", "ingest"),
    ("ev_studio.services.fact_checker", "factcheck"), ("ev_studio.services.claude_writer", "llm"), ("ev_studio.services.llm", "llm"),
    ("ev_studio.services.script_qa", "llm"), ("ev_studio.services.seo", "llm"), ("ev_studio.services.tts", "tts"),
    ("ev_studio.services.media_qa", "render"), ("ev_studio.services.media", "media"), ("ev_studio.services.storyboard", "media"),
    ("ev_studio.services.renderer", "render"), ("ev_studio.services.subtitles", "render"), ("ev_studio.services.thumbnail", "render"),
    ("ev_studio.services.youtube", "youtube"), ("ev_studio.services.telegram", "telegram"),
    ("ev_studio.services.connection_tests", "config"), ("ev_studio.runtime_config", "config"), ("ev_studio.config", "config"),
    ("ev_studio.api", "api"), ("apscheduler", "scheduler"),
]

_ctx: ContextVar[dict] = ContextVar("evcs_error_ctx", default={})
_local = threading.local()
_queue: "queue.Queue[dict]" = queue.Queue(maxsize=2000)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_writes = 0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ ngữ cảnh

@contextlib.contextmanager
def error_context(**kw: Any) -> Iterator[None]:
    """Gắn story_id / job_id / job_type cho mọi lỗi được log trong khối này (theo thread)."""
    token = _ctx.set({**_ctx.get(), **{k: v for k, v in kw.items() if v is not None}})
    try:
        yield
    finally:
        _ctx.reset(token)


def mark_logged(exc: BaseException) -> None:
    """Đánh dấu exception đã được ghi để tầng ngoài không ghi trùng."""
    with contextlib.suppress(Exception):
        exc._evcs_logged = True  # type: ignore[attr-defined]


def was_logged(exc: BaseException) -> bool:
    return bool(getattr(exc, "_evcs_logged", False))


def source_for_logger(name: str) -> str:
    for prefix, src in _SOURCE_BY_LOGGER:
        if name == prefix or name.startswith(prefix + "."):
            return src
    return "system"


# ------------------------------------------------------------------ fingerprint & gợi ý

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b[0-9a-f]{12,}\b", re.I)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def normalize(message: str) -> str:
    m = _UUID.sub("<id>", message)
    m = _HEX.sub("<hex>", m)
    m = _NUM.sub("#", m)
    return re.sub(r"\s+", " ", m).strip()[:400]


def fingerprint(source: str, exc_type: str | None, message: str, story_id: str | None, job_type: str | None) -> str:
    raw = "|".join([source, exc_type or "", normalize(message), story_id or "", job_type or ""])
    return hashlib.sha1(raw.encode()).hexdigest()


_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"BlockedByCredentials|BLOCKED_BY_CREDENTIALS|thiếu (key|credential|token)", re.I),
     "Thiếu hoặc sai key/token. Mở Thiết lập → Cấu hình API & dịch vụ, nhập lại rồi bấm “Kiểm tra”."),
    (re.compile(r"\b40[13]\b|unauthori[sz]ed|invalid[_ ]api[_ ]key|authentication|forbidden", re.I),
     "Dịch vụ từ chối xác thực: key/token có thể đã hết hạn hoặc bị thu hồi. Vào Thiết lập để nhập lại và bấm “Kiểm tra”."),
    (re.compile(r"ChannelMismatch|kênh .*không khớp", re.I),
     "Tài khoản OAuth đang trỏ sai kênh. Vào Thiết lập → Kết nối kênh YouTube và chọn đúng kênh đích."),
    (re.compile(r"PrivacyViolation|privacy", re.I),
     "Bảo vệ RIÊNG TƯ vừa chặn một thao tác. Video không bị công khai; kiểm tra lại trên YouTube Studio."),
    (re.compile(r"ffmpeg|ffprobe", re.I),
     "Không chạy được FFmpeg. Cài FFmpeg (Windows: winget install Gyan.FFmpeg) rồi khởi động lại ứng dụng."),
    (re.compile(r"quota|rate.?limit|\b429\b|too many requests|overloaded|\b529\b", re.I),
     "Dịch vụ đang giới hạn tần suất / hết quota. Chờ một lúc rồi chạy lại; tool đã tự thử lại có backoff."),
    (re.compile(r"timeout|timed out|connection|network|name resolution|ConnectError|RetryableError|\b5\d\d\b", re.I),
     "Lỗi mạng hoặc dịch vụ tạm thời. Thường tự hết — mở video và bấm chạy tiếp; tool sẽ resume từ checkpoint."),
    (re.compile(r"GateBlocked|QA|thời lượng|nguồn|claim|tư liệu|asset", re.I),
     "Một bước kiểm duyệt đã chặn video. Mở trang video để xem lý do, sửa rồi chạy tiếp."),
    (re.compile(r"database is locked|OperationalError", re.I),
     "Database đang bận hoặc lỗi. Nếu lặp lại nhiều lần, hãy khởi động lại ứng dụng."),
]


def hint_for(exc_type: str | None, message: str, source: str) -> str | None:
    text = f"{exc_type or ''} {message}"
    for pat, hint in _HINTS:
        if pat.search(text):
            return hint
    if source == "telegram":
        return "Telegram chưa gửi được. Video vẫn RIÊNG TƯ; kiểm tra bot token và chat được phép trong Thiết lập."
    return None


# ------------------------------------------------------------------ ghi lỗi

def _build(level: str, source: str, message: str, logger: str = "", exc: BaseException | None = None,
           trace: str | None = None, **ctx: Any) -> dict:
    from .security import redact

    level = level if level in LEVEL_RANK else "ERROR"
    c = {**_ctx.get(), **{k: v for k, v in ctx.items() if v is not None}}
    exc_type = type(exc).__name__ if exc is not None else c.pop("exc_type", None)
    details = dict(c.pop("details", None) or {})
    reasons = getattr(exc, "reasons", None)
    if reasons:
        details["reasons"] = [redact(r) for r in reasons]
    if exc is not None and trace is None:
        trace = "".join(tb.format_exception(type(exc), exc, exc.__traceback__))
    msg = redact(message).strip() or (redact(exc) if exc is not None else "(không có nội dung)")
    return {
        "level": level, "source": source, "logger": logger[:120], "message": msg[:4000], "exc_type": exc_type,
        "traceback": redact(trace)[-8000:] if trace else None, "story_id": c.get("story_id"), "job_id": c.get("job_id"),
        "job_type": c.get("job_type"), "details": {k: (redact(v) if isinstance(v, str) else v) for k, v in details.items()},
        "at": utcnow(),
    }


def record_error(level: str, source: str, message: str, *, exc: BaseException | None = None, logger: str = "",
                 story_id: str | None = None, job_id: str | None = None, job_type: str | None = None,
                 details: dict | None = None, sync: bool = False) -> int | None:
    """Ghi một lỗi. Mặc định bất đồng bộ (không chặn); `sync=True` ghi ngay và trả về id."""
    item = _build(level, source, message, logger, exc, story_id=story_id, job_id=job_id, job_type=job_type, details=details)
    if sync:
        return _write(item)
    _enqueue(item)
    return None


def prune(s, max_rows: int = MAX_ROWS) -> int:
    """Giữ lại `max_rows` nhóm lỗi mới nhất."""
    from sqlalchemy import delete, func, select

    from .models import ErrorLog

    if (s.scalar(select(func.count(ErrorLog.id))) or 0) <= max_rows:
        return 0
    old = select(ErrorLog.id).order_by(ErrorLog.last_seen_at.desc(), ErrorLog.id.desc()).offset(max_rows)
    n = s.execute(delete(ErrorLog).where(ErrorLog.id.in_(old.scalar_subquery()))).rowcount
    s.commit()
    return n


def _write(item: dict) -> int | None:
    global _writes
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import ErrorLog

    _local.busy = True
    try:
        fp = fingerprint(item["source"], item["exc_type"], item["message"], item["story_id"], item["job_type"])
        with SessionLocal() as s:
            row = s.scalar(select(ErrorLog).where(ErrorLog.fingerprint == fp, ErrorLog.resolved.is_(False))
                           .order_by(ErrorLog.id.desc()).limit(1))
            if row is None:
                row = ErrorLog(fingerprint=fp, level=item["level"], source=item["source"], logger=item["logger"],
                               message=item["message"], exc_type=item["exc_type"], traceback=item["traceback"],
                               story_id=item["story_id"], job_id=item["job_id"], job_type=item["job_type"],
                               details=item["details"], count=1, first_seen_at=item["at"], last_seen_at=item["at"])
                s.add(row)
            else:
                row.count += 1
                row.last_seen_at = item["at"]
                row.message = item["message"]
                row.job_id = item["job_id"] or row.job_id
                row.details = item["details"] or row.details
                if item["traceback"]:
                    row.traceback = item["traceback"]
                if LEVEL_RANK[item["level"]] > LEVEL_RANK.get(row.level, 0):
                    row.level = item["level"]
            s.commit()
            rid = row.id
            _writes += 1
            if _writes % 200 == 0:
                prune(s)
            return rid
    except Exception:  # ghi log lỗi không bao giờ được làm sập tool (vd: bảng chưa tạo)
        return None
    finally:
        _local.busy = False


def _run_worker() -> None:
    while True:
        item = _queue.get()
        try:
            _write(item)
        finally:
            _queue.task_done()


def _enqueue(item: dict) -> None:
    global _worker
    if _worker is None or not _worker.is_alive():
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_run_worker, daemon=True, name="evcs-error-log")
                _worker.start()
    with contextlib.suppress(queue.Full):  # quá tải → bỏ bớt, vẫn còn log ra console
        _queue.put_nowait(item)


def flush_pending(timeout: float = 10.0) -> None:
    """Chờ hàng đợi ghi xong (dùng trong test và trước khi trả danh sách lỗi)."""
    import time

    end = time.time() + timeout
    while _queue.unfinished_tasks and time.time() < end:
        time.sleep(0.02)


# ------------------------------------------------------------------ logging handler

class DBErrorHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(_local, "busy", False) or getattr(record, "evcs_skip_db", False):
            return
        if not record.name.startswith(CAPTURE_PREFIXES):
            return
        try:
            exc = record.exc_info[1] if record.exc_info and record.exc_info[1] is not None else None
            trace = "".join(tb.format_exception(*record.exc_info)) if exc is not None else None
            lv = record.levelname if record.levelname in LEVEL_RANK else ("CRITICAL" if record.levelno >= 50 else "ERROR")
            item = _build(lv, getattr(record, "evcs_source", None) or source_for_logger(record.name), record.getMessage(),
                          record.name, exc, trace, story_id=getattr(record, "evcs_story_id", None),
                          job_id=getattr(record, "evcs_job_id", None), job_type=getattr(record, "evcs_job_type", None),
                          details=getattr(record, "evcs_details", None))
            _enqueue(item)
        except Exception:  # pragma: no cover
            self.handleError(record)


def install_handler() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, DBErrorHandler) for h in root.handlers):
        root.addHandler(DBErrorHandler())
