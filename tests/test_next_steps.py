"""Video "Cần người xử lý": gợi ý việc cần làm bằng lời thường + nút Chạy lại / mở đúng tab Thiết lập / Bỏ video."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ev_studio.services.next_steps import next_steps


def actions(steps):
    return [(s["action"], s.get("tab")) for s in steps]


def test_no_steps_when_not_stopped():
    assert next_steps("EDITOR_REVIEW", ["x"], None) == []


def test_credentials_point_to_right_settings_tab():
    st = next_steps("NEEDS_REVIEW", ["BLOCKED_BY_CREDENTIALS: Thiếu Pixabay API key"], None)
    assert ("settings", "media") in actions(st) and ("run", None) in actions(st) and actions(st)[-1] == ("dismiss", None)
    st = next_steps("NEEDS_REVIEW", ["BLOCKED_BY_CREDENTIALS: thiếu ANTHROPIC_API_KEY (Claude)"], None)
    assert ("settings", "anthropic") in actions(st)


def test_old_fact_rules_and_budget_and_script():
    st = next_steps("NEEDS_REVIEW", ["Claim trọng yếu c1 chưa xác minh"], None)
    assert st[0]["action"] == "run" and "Chế độ phân tích" in st[0]["text"]
    st = next_steps("NEEDS_REVIEW", ["BLOCKED_BY_BUDGET: chạm trần ngày"], None)
    assert ("settings", "budget") in actions(st)
    st = next_steps("NEEDS_REVIEW", ["Script QA: Tin chưa xác minh được ghi rõ nguồn"], None)
    assert ("editor", None) in actions(st)
    st = next_steps("FAILED_PERMANENT", [], "lỗi lạ chưa gặp")
    assert actions(st) == [("run", None), ("dismiss", None)]


@pytest.fixture(scope="module")
def client(seeded):
    from ev_studio.api.main import app

    with TestClient(app) as c:
        yield c


def test_dismiss_stopped_story_and_list_shows_steps(client):
    from ev_studio.db import SessionLocal
    from ev_studio.models import NewsItem, Story

    with SessionLocal() as s:
        n = s.scalar(select(NewsItem).where(NewsItem.status == "shortlist"))
        st = Story(news_item_id=n.id, pillar="P1", topic="Video bị chặn để thử", mode="MANUAL", state="NEEDS_REVIEW",
                   resume_state="FACT_CHECK_PENDING", blockers=["Claim trọng yếu c1 chưa xác minh"], is_demo=True)
        s.add(st)
        s.commit()
        sid, nid = st.id, n.id
    row = next(x for x in client.get("/api/stories").json() if x["id"] == sid)
    assert row["next_steps"] and row["next_steps"][0]["action"] == "run"
    r = client.post(f"/api/stories/{sid}/dismiss", json={"note": "không cần"})
    assert r.status_code == 200 and r.json()["state"] == "REJECTED"
    with SessionLocal() as s:
        assert s.get(NewsItem, nid).status == "dismissed"
        s.get(NewsItem, nid).status = "shortlist"  # trả lại cho các test khác
        s.commit()
    assert client.post(f"/api/stories/{sid}/dismiss").status_code == 409  # đã bỏ rồi
    from sqlalchemy import delete

    from ev_studio.models import ReviewEvent

    with SessionLocal() as s:  # dọn: không chiếm hạn mức video/ngày của các test Autopilot sau
        s.execute(delete(ReviewEvent).where(ReviewEvent.story_id == sid))
        s.execute(delete(Story).where(Story.id == sid))
        s.commit()
