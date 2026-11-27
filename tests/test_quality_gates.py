"""Công tắc "Bỏ qua mọi kiểm tra" (mặc định bật) + nút "Bỏ qua kiểm tra & duyệt" cho từng kịch bản."""
from ev_studio.config import get_settings
from ev_studio.services import quality_gates as qg
from ev_studio.services.next_steps import next_steps


def test_default_on_and_override_turns_failures_into_warnings():
    assert get_settings().skip_quality_gates is True
    qa = {"passed": False, "checks": [{"name": "Chính xác 3 tiêu đề", "ok": False, "detail": "2", "blocking": True},
                                      {"name": "Đoạn 2–5 câu (cảnh báo)", "ok": False, "detail": "", "blocking": False}]}
    ov = qg.override(qa, "script", "test")
    assert ov["passed"] and ov["overridden_by"] == "test" and len(ov["warnings"]) == 2


def test_non_skippable_rules_stay():
    empty = {"checks": [{"name": "spoken_script không rỗng", "ok": False, "blocking": True}]}
    assert qg.override(empty, "script", "x") is None
    for name in ("Không dưới 10 phút", "Mọi asset đã qua license gate", "Checksum khớp file render"):
        assert qg.override({"checks": [{"name": name, "ok": False}]}, "media", "x") is None
    ok = qg.override({"passed": False, "checks": [{"name": "30 fps", "ok": False}, {"name": "Thumbnail 1280×720 < 2MB", "ok": False}]},
                     "media", "x")
    assert ok and ok["passed"]


def test_next_steps_offer_skip_and_approve():
    st = next_steps("NEEDS_REVIEW", ["Script QA chưa đạt (v4)"], None)
    assert st[0]["action"] == "approve" and "Bỏ qua kiểm tra" in st[0]["label"]
    st = next_steps("SCRIPT_DRAFT", [], None, awaiting_script_approval=True)
    assert [x["action"] for x in st] == ["approve", "editor", "dismiss"]
    assert next_steps("SCRIPT_DRAFT", [], None) == []


def test_force_approve_failed_script_resumes_story(seeded, monkeypatch):
    """Kịch bản trượt QA + story Cần người xử lý → bấm Bỏ qua kiểm tra & duyệt → kịch bản qua, story chạy tiếp."""
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, select

    from ev_studio.api.main import app
    from ev_studio.db import SessionLocal
    from ev_studio.models import AuditLog, NewsItem, ReviewEvent, ScriptVersion, Story

    with SessionLocal() as s:
        n = s.scalar(select(NewsItem).where(NewsItem.status == "shortlist"))
        st = Story(news_item_id=n.id, pillar="P1", topic="Kịch bản trượt QA", mode="MANUAL", state="NEEDS_REVIEW",
                   resume_state="SCRIPT_DRAFT", blockers=["Script QA chưa đạt (v1)"], is_demo=True)
        s.add(st)
        s.flush()
        sv = ScriptVersion(story_id=st.id, version=1, content={"title_options": []}, spoken_script="Xin chào các bạn.",
                           word_count=4, qa={"passed": False, "checks": [{"name": "Chính xác 3 tiêu đề", "ok": False,
                                                                           "detail": "", "blocking": True}]},
                           qa_passed=False, created_by="test", is_demo=True)
        s.add(sv)
        s.flush()
        st.current_script_version_id = sv.id
        s.commit()
        sid, svid = st.id, sv.id
    import ev_studio.workers.executor as ex

    monkeypatch.setattr(ex, "submit_pipeline", lambda *a, **k: False)  # không chạy pipeline nền với kịch bản giả
    get_settings().skip_quality_gates = False  # nút vẫn dùng được khi công tắc tắt
    try:
        with TestClient(app) as c:
            assert c.post(f"/api/scripts/{svid}/approve").status_code == 409  # không force → vẫn chặn
            r = c.post(f"/api/scripts/{svid}/approve?force=true")
            assert r.status_code == 200, r.text
    finally:
        get_settings().skip_quality_gates = True
    with SessionLocal() as s:
        sv = s.get(ScriptVersion, svid)
        assert sv.qa_passed and "Bỏ qua kiểm tra" in sv.qa["overridden_by"]
        st = s.get(Story, sid)
        assert st.state != "NEEDS_REVIEW" and st.script_approved_by
        assert s.scalar(select(AuditLog).where(AuditLog.action == "script.qa_override", AuditLog.entity_id == svid))
    with SessionLocal() as s:
        s.execute(delete(ReviewEvent).where(ReviewEvent.story_id == sid))
        st = s.get(Story, sid)
        st.current_script_version_id = None
        s.flush()
        s.execute(delete(ScriptVersion).where(ScriptVersion.story_id == sid))
        s.delete(st)
        s.commit()


def test_clean_for_tts_strips_what_voice_would_read_aloud():
    from ev_studio.services.script_qa import clean_for_tts

    t = clean_for_tts("[CẢNH 1] **Xin chào** các bạn.\n- Ý một https://vnexpress.net/x\nLúc 8:30 sáng (nhạc nền lên)")
    assert "[" not in t and "**" not in t and "http" not in t and "8 giờ 30" in t and "nhạc nền" not in t
    assert t.startswith("Xin chào các bạn.")
