"""Chọn chế độ production → xoá sạch dữ liệu DEMO, giữ nguyên dữ liệu thật và sổ cái ngân sách.

Chạy trên database + thư mục riêng (không đụng dữ liệu DEMO dùng chung của các test khác).
"""
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ev_studio.config import get_settings, reset_settings_cache
from ev_studio.db import SessionLocal, init_db, reset_engine

ISOLATED_ENV = ("DATABASE_URL", "ARTIFACTS_DIR", "MEDIA_LIBRARY_DIR")


@pytest.fixture()
def isolated(db_ready):
    old = {k: os.environ.get(k) for k in ISOLATED_ENV}
    tmp = Path(tempfile.mkdtemp(prefix="evcs-purge-"))
    os.environ.update({"DATABASE_URL": f"sqlite:///{tmp / 'p.db'}", "ARTIFACTS_DIR": str(tmp / "artifacts"),
                       "MEDIA_LIBRARY_DIR": str(tmp / "media")})
    reset_settings_cache(base_too=True)
    reset_engine()
    init_db()
    yield tmp
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_settings_cache(base_too=True)
    reset_engine()


def _run_demo_pipeline() -> str:
    from ev_studio.demo import seed_demo
    from ev_studio.providers import get_writer
    from ev_studio.services.ingest import run_scan
    from ev_studio.workers.jobs import autopilot_tick, set_setting

    with SessionLocal() as s:
        seed_demo(s)
        run_scan(s, writer=get_writer(), actor="test")
        set_setting(s, "autopilot_enabled", True)
        s.commit()
        res = autopilot_tick(s)
    assert res.get("ran"), res
    return res["story_id"]


def _add_real_data() -> dict:
    """Dữ liệu thật phải còn nguyên sau khi xoá DEMO."""
    from ev_studio.agents.memory import remember
    from ev_studio.models import BudgetLedger, Channel, Essay, NewsItem, Source, Story

    with SessionLocal() as s:
        src = Source(name="VinFast", domain="vinfastauto.com", kind="manual")
        ch = Channel(youtube_channel_id="UC_REAL_CHANNEL", title="Kênh thật")
        s.add_all([src, ch])
        s.flush()
        n = NewsItem(title="Tin thật", url="https://vinfastauto.com/a", canonical_url="https://vinfastauto.com/a",
                     article_hash="h" * 64, source_id=src.id)
        s.add(n)
        s.flush()
        st = Story(news_item_id=n.id, pillar="P1", topic="Video thật", channel_id=ch.id)
        e = Essay(topic="Bài luận thật", status="done")
        s.add_all([st, e, BudgetLedger(department="bien_kich", provider="anthropic", model="claude-sonnet-5-5",
                                       status="settled", usd=0.12, story_id="bat-ky")])
        s.flush()
        m = remember(s, "bien_kich", "lesson", key="real-lesson", text="Bài học thật", source="owner", story_id=st.id)
        s.commit()
        return {"source": src.id, "channel": ch.id, "news": n.id, "story": st.id, "essay": e.id, "memory": m.id}


def _count(model, *conds) -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count()).select_from(model).where(*conds))


def test_switch_to_production_purges_all_demo_data(isolated):
    from ev_studio.demo import demo_data_summary
    from ev_studio.models import (
        AgentMemory, AppSetting, Asset, AuditLog, BudgetLedger, Channel, Claim, CostUsage, Essay, Job, NewsItem, Render,
        ScriptVersion, Source, Story, TTSSegment, YouTubeUpload,
    )
    from ev_studio.runtime_config import save

    story_id = _run_demo_pipeline()
    real = _add_real_data()
    art = get_settings().artifacts_dir
    assert (art / story_id).is_dir() and (get_settings().media_library_dir / "demo").is_dir()
    cache = art / "_tts_cache"
    assert any(cache.glob("*.wav"))
    with SessionLocal() as s:
        before = demo_data_summary(s)
    assert before["stories"] >= 1 and before["news"] >= 1 and before["channels"] == 1 and before["running_stories"] == 0
    assert _count(Render) and _count(TTSSegment) and _count(YouTubeUpload) and _count(Job, Job.story_id == story_id)

    with SessionLocal() as s:
        res = save(s, {"app_mode": "production"}, [], "owner")
    assert get_settings().app_mode == "production"
    purged = res.demo_purged
    assert purged and purged["total"] > 0 and purged["deleted"]["stories"] == before["stories"]
    assert purged["autopilot_reset"] is True and purged["files_removed"] >= 3

    # DEMO: không còn gì
    for model in (Story, NewsItem, Source, Channel, Essay, CostUsage):
        assert _count(model, model.is_demo.is_(True)) == 0, model.__tablename__
    for model in (Render, ScriptVersion, YouTubeUpload, Asset):
        assert _count(model) == 0, model.__tablename__
    assert _count(TTSSegment) == 0 and _count(Claim) == 0 and _count(Job, Job.story_id == story_id) == 0
    assert not (art / story_id).exists() and not (art / "demo_youtube").exists()
    assert not (get_settings().media_library_dir / "demo").exists()
    assert not any(cache.glob("*.wav")), "cache giọng giả lập phải bị xoá"
    with SessionLocal() as s:
        assert demo_data_summary(s)["total"] == 0
        assert s.get(AppSetting, "autopilot_enabled").value is False
        assert s.scalar(select(AuditLog).where(AuditLog.action == "demo.purge")) is not None

    # Dữ liệu thật + sổ cái ngân sách: còn nguyên
    with SessionLocal() as s:
        assert s.get(Story, real["story"]) and s.get(NewsItem, real["news"]) and s.get(Source, real["source"])
        assert s.get(Channel, real["channel"]) and s.get(Essay, real["essay"]) and s.get(AgentMemory, real["memory"])
    assert _count(BudgetLedger) == 1

    # Lưu lại production lần nữa: không còn gì để xoá
    with SessionLocal() as s:
        again = save(s, {"app_mode": "production"}, [], "owner")
    assert again.demo_purged["total"] == 0


def test_purge_blocked_while_demo_story_running(isolated):
    from datetime import timedelta

    from ev_studio.demo import DemoPurgeBlocked
    from ev_studio.models import AppSetting, Story, now
    from ev_studio.runtime_config import PREFIX, save
    from ev_studio.workers.orchestrator import WORKER_ID

    with SessionLocal() as s:
        st = Story(pillar="P1", topic="[DEMO] đang chạy", is_demo=True, lease_owner=WORKER_ID,
                   lease_expires_at=now() + timedelta(minutes=30))
        s.add(st)
        s.commit()
        with pytest.raises(DemoPurgeBlocked, match="đang chạy|chạy dở"):
            save(s, {"app_mode": "production"}, [], "owner")
        s.rollback()
        assert s.get(AppSetting, PREFIX + "app_mode") is None, "bị chặn thì không được đổi chế độ"
        assert s.get(Story, st.id) is not None
        # lease mồ côi của tiến trình cũ (đã tắt) không chặn
        s.get(Story, st.id).lease_owner = "may-cu:dead00"
        s.commit()
        assert save(s, {"app_mode": "production"}, [], "owner").demo_purged["deleted"]["stories"] == 1
    reset_settings_cache()


def test_demo_data_api_and_config_put(isolated, monkeypatch):
    from fastapi.testclient import TestClient

    from ev_studio.api.main import app
    from ev_studio.demo import seed_demo

    monkeypatch.setenv("EVCS_TRUST_LOCALHOST", "1")  # như run.py: chỉ nghe 127.0.0.1
    reset_settings_cache(base_too=True)
    with SessionLocal() as s:
        seed_demo(s)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:  # chủ kênh mở trên chính máy chạy app
        d = c.get("/api/demo-data").json()
        assert d["channels"] == 1 and d["sources"] == 1 and d["total"] >= 2
        r = c.put("/api/config", json={"values": {"app_mode": "production"}})
        assert r.status_code == 200, r.text
        assert r.json()["demo_purged"]["deleted"]["channels"] == 1
        assert c.get("/api/demo-data").json()["total"] == 0
        # quay lại demo: không xoá gì
        r = c.put("/api/config", json={"values": {"app_mode": "demo"}})
        assert r.json()["demo_purged"] is None
    reset_settings_cache()


def test_startup_in_production_purges_leftover_demo(isolated, monkeypatch):
    from ev_studio.demo import demo_data_summary, purge_demo_if_production, seed_demo

    with SessionLocal() as s:
        seed_demo(s)
    assert purge_demo_if_production() is None  # đang demo: không đụng
    monkeypatch.setenv("APP_MODE", "production")
    reset_settings_cache(base_too=True)
    res = purge_demo_if_production()
    assert res and res["deleted"]["channels"] == 1
    with SessionLocal() as s:
        assert demo_data_summary(s)["total"] == 0
    assert purge_demo_if_production() is None
