"""Ban Tăng trưởng: đọc YouTube Analytics, tính KPI doanh thu tháng, tự chỉnh kế hoạch trong giới hạn, không tự công khai."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from ev_studio.agents import growth
from ev_studio.config import get_settings


@pytest.fixture()
def clean_growth(db_ready):
    from ev_studio.db import SessionLocal
    from ev_studio.models import AppSetting, ChannelMetricDay, GrowthPlan, VideoMetric

    def wipe():
        with SessionLocal() as s:
            for m in (ChannelMetricDay, VideoMetric, GrowthPlan):
                s.execute(delete(m))
            s.execute(delete(AppSetting).where(AppSetting.key.in_([growth.S_TARGET, growth.S_WEIGHTS, growth.S_GUIDANCE])))
            s.commit()

    wipe()
    yield
    wipe()


def with_settings(monkeypatch, **kw):
    prod = get_settings().model_copy(update=kw)
    for mod in ("ev_studio.agents.growth", "ev_studio.agents.budget"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda prod=prod: prod)
    return prod


def add_days(db, cid, today, n, views, rev_per_1k):
    from ev_studio.models import ChannelMetricDay

    for i in range(n):
        d = today - timedelta(days=i)
        db.add(ChannelMetricDay(channel_id=cid, day=d.isoformat(), views=views, watch_minutes=views * 4.0,
                                ad_revenue_usd=None if rev_per_1k is None else views * rev_per_1k / 1000))
    db.flush()


def test_kpi_math_and_never_cut_output_when_behind(clean_growth, db, monkeypatch):
    with_settings(monkeypatch, kpi_monthly_revenue_usd=100.0, growth_auto_apply=True, growth_max_stories_per_day=6,
                  max_videos_per_week=42, stories_per_day=3, stories_per_day_min=2)
    today = date(2026, 11, 20)
    add_days(db, "UC_T1", today, 40, views=1000, rev_per_1k=1.0)  # $1/ngày
    st = growth.kpi_status(db, "UC_T1", today=today)
    assert st["mtd_usd"] == 20.0 and st["days_covered"] == 20 and st["days_remaining"] == 10
    assert st["projected_usd"] == 30.0 and st["gap_usd"] == 70.0 and st["rpm_usd"] == 1.0
    assert st["need_per_day_usd"] == 8.0 and st["required_views_per_day"] == 8000
    decisions, owner, limits = growth.decide(db, st)
    d = next(x for x in decisions if x["key"] == growth.S_TARGET)
    assert d["to"] >= d["from"]  # đang thiếu KPI → không giảm
    assert d["to"] <= 6
    assert any("video/ngày" in o["title"] for o in owner)  # cần nhiều hơn trần → báo chủ kênh


def test_ahead_of_kpi_may_reduce_to_save_budget(clean_growth, db, monkeypatch):
    with_settings(monkeypatch, kpi_monthly_revenue_usd=10.0, growth_auto_apply=True, stories_per_day=5, stories_per_day_min=1,
                  max_videos_per_week=42)
    today = date(2026, 11, 20)
    add_days(db, "UC_T2", today, 40, views=2000, rev_per_1k=1.0)  # $2/ngày → dự báo $60 ≫ $10
    st = growth.kpi_status(db, "UC_T2", today=today)
    decisions, _, _ = growth.decide(db, st)
    d = next(x for x in decisions if x["key"] == growth.S_TARGET)
    assert d["to"] < d["from"] and d["to"] >= 1


def test_not_monetized_pushes_for_ypp(clean_growth, db, monkeypatch):
    with_settings(monkeypatch, kpi_monthly_revenue_usd=50.0, growth_max_stories_per_day=4, stories_per_day=2,
                  stories_per_day_min=1, max_videos_per_week=42)
    today = date(2026, 11, 20)
    add_days(db, "UC_T3", today, 30, views=300, rev_per_1k=None)
    st = growth.kpi_status(db, "UC_T3", today=today, sync_info={"subscribers": 420, "watch_hours_365": 900})
    assert st["monetized"] is False and st["rpm_usd"] is None
    decisions, owner, limits = growth.decide(db, st)
    assert next(x for x in decisions if x["key"] == growth.S_TARGET)["to"] == limits["hi"]
    ypp = next(o for o in owner if "doanh thu" in o["title"])
    assert "1,000" in ypp["detail"] and "420" in ypp["detail"]


def test_ypp_threshold_doubles_in_2027(clean_growth, db):
    assert growth.kpi_status(db, "UC_X", today=date(2027, 2, 1))["ypp"]["watch_hours_needed"] == 8000
    assert growth.kpi_status(db, "UC_X", today=date(2026, 12, 1))["ypp"]["watch_hours_needed"] == 4000


def test_demo_tick_applies_plan_within_limits_and_budget_uses_it(clean_growth, db, seeded, monkeypatch):
    from ev_studio.agents import budget
    from ev_studio.models import GrowthPlan

    with_settings(monkeypatch, kpi_monthly_revenue_usd=30.0, growth_auto_apply=True, growth_max_stories_per_day=5)
    res = growth.growth_tick(db, actor="test")
    assert res["status"]["kpi_set"] and res["status"]["sync"]["is_demo"] and res["memo"]["summary"].startswith("[DEMO]")
    plan = db.scalar(select(GrowthPlan).order_by(GrowthPlan.id.desc()))
    assert plan.is_demo and plan.applied
    t = growth.effective_stories_per_day(db)
    assert 1 <= t <= 5
    assert budget.status(db)["stories_per_day"] == t
    # tắt tự áp dụng → quay về cấu hình của chủ kênh
    with_settings(monkeypatch, growth_auto_apply=False, stories_per_day=2)
    assert growth.effective_stories_per_day(db) == 2


def test_pillar_priority_reorders_but_keeps_score():
    w = {"P1": 0.6, "P2": 0.25, "P3": 0.15}
    assert growth.autopilot_priority(85, "P1", w) > growth.autopilot_priority(90, "P3", w)
    assert growth.autopilot_priority(85, "P1", {p: 1 / 3 for p in w}) == pytest.approx(85)


def test_channel_without_analytics_scope_must_reconnect(db, monkeypatch):
    from ev_studio.errors import BlockedByCredentials
    from ev_studio.models import Channel

    monkeypatch.setattr("ev_studio.providers.youtube_mode", lambda *a: "google")
    ch = Channel(youtube_channel_id="UC_OLD", title="cũ", oauth_token_encrypted="x",
                 scopes=["https://www.googleapis.com/auth/youtube.upload"])
    with pytest.raises(BlockedByCredentials, match="Kết nối kênh YouTube"):
        growth.analytics_client_for(db, ch)


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


def test_google_client_reads_days_and_survives_revenue_403():
    import httplib2
    from googleapiclient.errors import HttpError

    from ev_studio.services.youtube.analytics import GoogleAnalyticsClient

    calls = []

    def query(**kw):
        calls.append(kw)
        if "estimatedRevenue" in kw["metrics"]:
            def deny():
                raise HttpError(httplib2.Response({"status": 403}), b'{"error": "forbidden"}')
            return _Req(deny)
        return _Req(lambda: {"columnHeaders": [{"name": "day"}, {"name": "views"}, {"name": "estimatedMinutesWatched"},
                                                {"name": "averageViewDuration"}, {"name": "subscribersGained"},
                                                {"name": "subscribersLost"}],
                             "rows": [["2026-11-01", 120, 480.0, 240, 3, 1], ["2026-11-02", 150, 600.0, 250, 4, 0]]})

    ya = SimpleNamespace(reports=lambda: SimpleNamespace(query=query))
    c = GoogleAnalyticsClient(None, analytics=ya, data=SimpleNamespace())
    rows, err = c.daily(date(2026, 11, 1), date(2026, 11, 2))
    assert [r["views"] for r in rows] == [120, 150] and "kiếm tiền" in err
    assert all(k["ids"] == "channel==MINE" and k["currency"] == "USD" for k in calls)
    vids, verr = c.top_videos(date(2026, 11, 1), date(2026, 11, 2))
    assert verr and calls[-1]["sort"] == "-views" and calls[-1]["maxResults"] == 200


def test_growth_api(clean_growth, db_ready, seeded):
    from fastapi.testclient import TestClient

    from ev_studio.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/growth/run", json={"memo": True})
        assert r.status_code == 200 and r.json()["decisions"]
        g = c.get("/api/growth").json()
        assert g["latest"]["id"] == r.json()["id"] and g["memo"] and g["channel"]["analytics_scope"]
