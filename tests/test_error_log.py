"""Màn hình Nhật ký lỗi: ghi lỗi từ logger, che secret, gộp lỗi trùng, ngữ cảnh job/story, API lọc/xử lý/xoá, lỗi 500 của API."""
import logging
import uuid

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ev_studio.db import SessionLocal
from ev_studio.error_log import flush_pending, hint_for, normalize, prune, record_error
from ev_studio.errors import BlockedByCredentials, RetryableError
from ev_studio.models import ErrorLog, Job

SECRET = "sk-ant-api03-LEAKYSECRET-abcdef123456"


def tag() -> str:
    return f"T{uuid.uuid4().hex[:10]}"


def rows_with(marker: str) -> list[ErrorLog]:
    flush_pending()
    with SessionLocal() as s:
        return list(s.scalars(select(ErrorLog).where(ErrorLog.message.contains(marker)).order_by(ErrorLog.id)))


@pytest.fixture()
def client(db_ready):
    from ev_studio.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_logger_error_is_captured_redacted_and_classified(db_ready):
    m = tag()
    try:
        raise ValueError(f"hỏng {SECRET}")
    except ValueError:
        logging.getLogger("ev_studio.services.renderer").exception("Render %s lỗi với key %s", m, SECRET)
    logging.getLogger("httpx").error("không thuộc tool %s", m)  # logger ngoài tool → bỏ qua
    logging.getLogger("ev_studio.services.ingest").info("chỉ là info %s", m)  # dưới WARNING → bỏ qua
    rows = rows_with(m)
    assert len(rows) == 1
    r = rows[0]
    assert r.level == "ERROR" and r.source == "render" and r.exc_type == "ValueError"
    assert SECRET not in r.message and SECRET not in (r.traceback or "") and "REDACTED" in r.message
    assert "Traceback" in r.traceback


def test_same_error_is_grouped_until_resolved(db_ready):
    m = tag()
    lg = logging.getLogger("ev_studio.services.sources.fetcher")
    lg.warning("Feed %s trả %s", m, 503)
    lg.warning("Feed %s trả %s", m, 504)  # khác số → vẫn cùng nhóm
    rows = rows_with(m)
    assert len(rows) == 1 and rows[0].count == 2 and rows[0].source == "ingest" and rows[0].level == "WARNING"
    lg.error("Feed %s trả %s", m, 500)  # cùng nội dung, mức cao hơn → nâng mức nhóm
    assert rows_with(m)[0].level == "ERROR"
    with SessionLocal() as s:
        s.get(ErrorLog, rows[0].id).resolved = True
        s.commit()
    lg.warning("Feed %s trả %s", m, 503)  # tái phát sau khi đã xử lý → nhóm mới
    assert [r.resolved for r in rows_with(m)] == [True, False]
    assert normalize("story 1b2c3d4e-0000-4000-8000-123456789abc lỗi 42") == "story <id> lỗi #"


def test_job_failures_carry_job_and_story_context(db_ready):
    from ev_studio.workers.orchestrator import run_job

    m = tag()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise RetryableError(f"mạng chập chờn {m}")

    with SessionLocal() as s:
        with pytest.raises(RetryableError):
            run_job(s, None, "RenderJob", m, flaky, max_attempts=3)
        job = s.scalar(select(Job).where(Job.idempotency_key.contains(m)))
    rows = rows_with(m)
    warn = [r for r in rows if r.level == "WARNING"]
    err = [r for r in rows if r.level == "ERROR"]
    assert calls["n"] == 3
    assert len(warn) == 1 and warn[0].count == 2  # 2 lần thử lại gộp thành 1 dòng
    assert len(err) == 1 and err[0].job_id == job.id and err[0].job_type == "RenderJob" and err[0].source == "job"
    assert err[0].details.get("attempts") == 3


def test_auto_handled_gate_is_not_logged(db_ready):
    from ev_studio.workers.jobs import DurationOutOfRange
    from ev_studio.workers.orchestrator import run_job

    m = tag()

    def too_long():
        raise DurationOutOfRange(999.0)

    with SessionLocal() as s:
        before = s.scalar(select(func.count(ErrorLog.id)).where(ErrorLog.message.contains("999.0s")))
        with pytest.raises(DurationOutOfRange):
            run_job(s, None, "TTSJob", m, too_long)
    flush_pending()
    with SessionLocal() as s:
        assert s.scalar(select(func.count(ErrorLog.id)).where(ErrorLog.message.contains("999.0s"))) == before


def test_hints_and_prune(db_ready):
    assert "Thiết lập" in hint_for("BlockedByCredentials", "Thiếu ANTHROPIC_API_KEY", "llm")
    assert "FFmpeg" in hint_for("FileNotFoundError", "No such file: 'ffmpeg'", "render")
    assert hint_for(None, "something odd", "system") is None
    m = tag()
    for i in range(5):
        record_error("ERROR", "system", f"prune {m} case-{chr(97 + i)}", sync=True)
    with SessionLocal() as s:
        total = s.scalar(select(func.count(ErrorLog.id)))
        assert prune(s, max_rows=total - 2) == 2
        assert s.scalar(select(func.count(ErrorLog.id))) == total - 2


def test_errors_api_filter_detail_resolve_bulk_purge(client):
    m = tag()
    try:
        raise BlockedByCredentials(f"Thiếu key {m}")
    except BlockedByCredentials as e:
        a = record_error("ERROR", "llm", f"Viết kịch bản {m}", exc=e, job_type="ScriptJob", sync=True)
    b = record_error("WARNING", "telegram", f"Telegram {m}", sync=True)

    r = client.get("/api/errors", params={"q": m}).json()
    assert r["total"] == 2 and {x["id"] for x in r["items"]} == {a, b}
    assert r["summary"]["open"] >= 2 and any(s["source"] == "llm" for s in r["summary"]["by_source"])
    assert client.get("/api/errors", params={"q": m, "level": "ERROR"}).json()["total"] == 1
    assert client.get("/api/errors", params={"q": m, "source": "telegram"}).json()["items"][0]["id"] == b

    d = client.get(f"/api/errors/{a}").json()
    assert d["exc_type"] == "BlockedByCredentials" and "Traceback" in d["traceback"] and "Thiết lập" in d["hint"]
    assert d["source_label"] == "Claude / kịch bản"
    assert client.get("/api/errors/999999999").status_code == 404

    x = client.post(f"/api/errors/{a}/resolve", json={"resolved": True, "note": "đã nhập key"}).json()
    assert x["resolved"] and x["resolved_by"] and x["resolution_note"] == "đã nhập key"
    assert client.get("/api/errors", params={"q": m}).json()["total"] == 1  # mặc định chỉ lỗi đang mở
    assert client.get("/api/errors", params={"q": m, "status": "resolved"}).json()["items"][0]["id"] == a
    assert not client.post(f"/api/errors/{a}/resolve", json={"resolved": False}).json()["resolved"]  # mở lại

    assert client.post("/api/errors/resolve-bulk", json={"q": m}).json()["resolved"] == 2
    assert client.get("/api/errors", params={"q": m}).json()["total"] == 0

    keep = record_error("ERROR", "system", f"vẫn mở {m}", sync=True)
    assert client.delete("/api/errors").json()["deleted"] >= 2  # chỉ xoá lỗi đã xử lý
    left = client.get("/api/errors", params={"q": m, "status": "all"}).json()["items"]
    assert [x["id"] for x in left] == [keep]
    assert client.get("/api/dashboard").json()["open_errors"]["total"] >= 1


def test_unhandled_api_exception_is_logged_and_hidden(client):
    from ev_studio.api.main import app

    m = tag()
    router = APIRouter()

    @router.get(f"/api/_boom_{m}")
    def boom():
        raise RuntimeError(f"nổ {m} {SECRET}")

    app.router.routes.insert(0, router.routes[0])  # trước static mount "/"
    try:
        r = client.get(f"/api/_boom_{m}")
    finally:
        app.router.routes.pop(0)
    assert r.status_code == 500 and "Nhật ký lỗi" in r.json()["detail"] and SECRET not in r.text and "Traceback" not in r.text
    rows = rows_with(m)
    assert len(rows) == 1 and rows[0].source == "api" and rows[0].details["path"] == f"/api/_boom_{m}"
    assert SECRET not in rows[0].message
