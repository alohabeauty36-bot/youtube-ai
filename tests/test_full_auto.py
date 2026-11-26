"""Tự động hoàn toàn: Autopilot tự chạy, tự công khai sau thời gian chờ (có nút Dừng), phát hiện video bị khoá riêng tư,
tự sửa lỗi / tự bỏ tin hỏng. DEMO, không upload thật."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from ev_studio.config import get_settings, reset_settings_cache
from ev_studio.db import SessionLocal
from ev_studio.models import AppSetting, Claim, NewsItem, Story, TelegramCallbackToken, TelegramMessage, YouTubeUpload, now
from ev_studio.runtime_config import PREFIX, save
from ev_studio.workers import autoops


def demo_videos():
    p = get_settings().artifacts_dir / "demo_youtube" / "videos.json"
    return json.loads(p.read_text()) if p.exists() else {}


@pytest.fixture()
def full_auto_on(db_ready):
    with SessionLocal() as s:
        save(s, {"full_auto": True, "auto_publish_delay_minutes": 120, "auto_heal_max_attempts": 1}, [], "test")
    reset_settings_cache()
    yield
    with SessionLocal() as s:
        s.execute(delete(AppSetting).where(AppSetting.key.in_([PREFIX + k for k in
                                                               ("full_auto", "auto_publish_delay_minutes", "auto_heal_max_attempts")])))
        s.commit()
    reset_settings_cache()


@pytest.fixture()
def auto_story(seeded, full_auto_on, monkeypatch):
    """Một video Autopilot mới chạy tới EDITOR_REVIEW khi đang bật Tự động hoàn toàn."""
    from ev_studio.agents import budget
    from ev_studio.workers.jobs import autopilot_tick

    monkeypatch.setattr(budget, "can_start_story", lambda db: (True, "ok"))
    with SessionLocal() as s:
        src = s.scalar(select(NewsItem).where(NewsItem.status == "shortlist", NewsItem.is_demo.is_(True))
                       .order_by(NewsItem.score.desc()))
        n = NewsItem(**{c.name: getattr(src, c.name) for c in NewsItem.__table__.columns if c.name != "id"})
        n.canonical_url, n.article_hash, n.score, n.status = src.canonical_url + "?full-auto", "fa-" + src.article_hash, 99, "shortlist"
        s.add(n)
        s.flush()
        for c in src.claims:
            s.add(Claim(**{k.name: getattr(c, k.name) for k in Claim.__table__.columns if k.name not in ("id", "news_item_id")},
                        news_item_id=n.id))
        s.commit()
        res = autopilot_tick(s)
    assert res.get("ran") and res["state"] == "EDITOR_REVIEW", res
    yield res["story_id"]
    with SessionLocal() as s:  # không tính vào hạn mức tin/ngày của các test khác
        s.execute(Story.__table__.update().where(Story.id == res["story_id"]).values(created_at=now() - timedelta(days=3)))
        s.commit()


def test_scheduled_publish_hold_lock_and_publish(auto_story):
    from ev_studio.services.youtube.client import DemoYouTubeClient

    sid = auto_story
    with SessionLocal() as s:
        st = s.get(Story, sid)
        assert st.publish_after is not None and st.publish_after.replace(tzinfo=None) > now().replace(tzinfo=None) + timedelta(minutes=100)
        msg = s.scalar(select(TelegramMessage).where(TelegramMessage.story_id == sid))
        assert "TỰ ĐỘNG CÔNG KHAI" in msg.text
        assert s.scalar(select(TelegramCallbackToken).where(TelegramCallbackToken.story_id == sid,
                                                            TelegramCallbackToken.action == "hold")) is not None
        # chưa tới giờ → không làm gì
        assert autoops.publish_due(s) == []
        up = s.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == sid))
        vid = up.youtube_video_id

        # chủ kênh bấm Dừng trên Telegram
        from ev_studio.services.telegram.callbacks import handle_callback

        tok = s.scalar(select(TelegramCallbackToken).where(TelegramCallbackToken.story_id == sid, TelegramCallbackToken.action == "hold"))
        r = handle_callback(s, {"id": "cb1", "data": f"rv|{tok.token}", "message": {"chat": {"id": tok.chat_id}}})
        assert r.get("held") and s.get(Story, sid).publish_hold
        st = s.get(Story, sid)
        st.publish_after = now() - timedelta(minutes=1)
        s.commit()
        assert autoops.publish_due(s) == []  # đã Dừng → giữ riêng tư
        assert demo_videos()[vid]["privacyStatus"] == "private"

        # cho phép lại, nhưng YouTube khoá riêng tư (project chưa audit)
        autoops.hold(s, st, "owner", False)
        DemoYouTubeClient().simulate_locked_private(vid)
        out = autoops.publish_due(s)
        assert out[0]["action"] == "locked_private"
        st = s.get(Story, sid)
        assert st.publish_hold and "audit" in st.blockers[0] and st.state == "EDITOR_REVIEW"

        # gỡ khoá (giả lập audit đạt) → tự công khai
        db = demo_videos()
        db[vid].pop("locked_private")
        (get_settings().artifacts_dir / "demo_youtube" / "videos.json").write_text(json.dumps(db))
        autoops.hold(s, st, "owner", False)
        st.publish_after = now() - timedelta(minutes=1)
        s.commit()
        out = autoops.publish_due(s)
        assert out[0]["action"] == "published"
        st = s.get(Story, sid)
        assert st.state == "PUBLISHED_AUTO" and demo_videos()[vid]["privacyStatus"] == "public"
        assert s.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == sid)).privacy_status_observed == "public"


def test_publish_requires_telegram_and_full_auto(auto_story):
    sid = auto_story
    with SessionLocal() as s:
        st = s.get(Story, sid)
        st.publish_after = now() - timedelta(minutes=1)
        s.execute(delete(TelegramMessage).where(TelegramMessage.story_id == sid))  # chủ kênh chưa được báo
        s.commit()
        out = autoops.publish_due(s)
        assert out[0]["action"] == "held" and any("Telegram" in r for r in out[0]["reasons"])
        assert s.get(Story, sid).state == "EDITOR_REVIEW"
    # tắt Tự động hoàn toàn → không bao giờ tự công khai
    with SessionLocal() as s:
        save(s, {"full_auto": False}, [], "test")
    reset_settings_cache()
    with SessionLocal() as s:
        st = s.get(Story, sid)
        st.publish_hold = False
        s.commit()
        assert autoops.publish_due(s) == []


def _stuck_story(db, state="NEEDS_REVIEW", blockers=None, attempts=0, age_min=30):
    st = Story(pillar="P1", topic="tin hỏng", mode="AUTOPILOT_PRIVATE", autopilot_authorized=True, state=state,
               resume_state="RENDERING", blockers=blockers or ["Media QA: Thời lượng — 90s"], auto_heal_attempts=attempts,
               is_demo=True)
    db.add(st)
    db.flush()
    st.updated_at = now() - timedelta(minutes=age_min)
    db.commit()
    db.execute(Story.__table__.update().where(Story.id == st.id).values(updated_at=now() - timedelta(minutes=age_min)))
    db.commit()
    return st.id


def test_heal_retries_then_abandons_and_skips_budget_and_credentials(full_auto_on, monkeypatch):
    calls = []
    monkeypatch.setattr(autoops, "_resume", lambda sid, p: calls.append(sid) or {"state": "NEEDS_REVIEW"})
    with SessionLocal() as s:
        retry = _stuck_story(s, attempts=0)
        give_up = _stuck_story(s, attempts=1)
        budget = _stuck_story(s, blockers=["BLOCKED_BY_BUDGET (ngày): hết"], age_min=20)
        creds = _stuck_story(s, blockers=["BLOCKED_BY_CREDENTIALS: Thiếu ANTHROPIC_API_KEY"])
        fresh = _stuck_story(s, age_min=1)
        out = {o["story_id"]: o["action"] for o in autoops.heal(s)}
        assert out.get(retry) == "retry" and calls == [retry]
        assert out.get(give_up) == "abandoned" and s.get(Story, give_up).state == "REJECTED"
        assert "Tự động bỏ" in s.get(Story, give_up).last_error
        assert budget not in out and creds not in out and fresh not in out
        from ev_studio.models import ReviewEvent

        ids = [retry, give_up, budget, creds, fresh]
        s.execute(delete(ReviewEvent).where(ReviewEvent.story_id.in_(ids)))
        s.execute(delete(Story).where(Story.id.in_(ids)))
        s.commit()


def test_full_auto_turns_on_autopilot_and_scan(full_auto_on):
    with SessionLocal() as s:
        assert autoops.is_on(s, "autopilot_enabled") and autoops.is_on(s, "scan_schedule_enabled")
        assert autoops.auto_publish_active()
