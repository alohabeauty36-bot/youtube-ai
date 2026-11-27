"""API (auth, gate HTTP, signed media) + vòng chỉnh thời lượng theo audio thật + luồng MANUAL dừng chờ người."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ev_studio.db import SessionLocal
from ev_studio.models import NewsItem, ScriptVersion, Story
from ev_studio.providers import Providers
from ev_studio.services.tts.adapters import DemoTTSAdapter
from ev_studio.workers.jobs import run_pipeline


@pytest.fixture(scope="module")
def client(seeded):
    from ev_studio.api.main import app

    with TestClient(app) as c:
        yield c


def test_health_and_integrations(client):
    assert client.get("/api/health").json()["mode"] == "demo"
    integ = client.get("/api/system/integrations").json()
    assert integ["youtube"] == "DEMO" and "private" in integ["privacy_policy"]
    # ảnh AI là tuỳ chọn → "chưa nhập key" chứ không phải lỗi; trang Thiết lập hiện trạng thái này ở tab Ảnh AI
    assert integ["cloudflare"] in ("NOT_SET", "CONFIGURED", "OFF") and integ["fal"] in ("NOT_SET", "CONFIGURED", "OFF")


def test_optional_integration_status():
    from ev_studio.api.main import _optional

    assert _optional(False, True) == "OFF"
    assert _optional(True, False) == "NOT_SET"
    assert _optional(True, True) == "CONFIGURED"


def test_settings_has_ai_images_tab(client):
    cfg = client.get("/api/config").json()
    assert "ai_images" in cfg["groups"]
    keys = {f["key"] for f in cfg["fields"] if f["group"] == "ai_images"}
    assert {"cloudflare_account_id", "cloudflare_api_token", "fal_api_key", "ai_image_provider"} <= keys


def test_news_listing_and_blocked_story_creation(client):
    news = client.get("/api/news").json()
    blocked = next(n for n in news if n["status"] == "blocked")
    r = client.post("/api/stories", json={"news_item_id": blocked["id"]})
    assert r.status_code == 409


def test_manual_flow_stops_for_human_and_upload_needs_confirmation(client):
    news = client.get("/api/news", params={"status": "shortlist"}).json()
    p2 = next(n for n in news if n["pillar"] == "P2" and not n["has_story"])
    st = client.post("/api/stories", json={"news_item_id": p2["id"]}).json()
    res = run_pipeline(st["id"], stop_at="TTS_DONE")
    assert res["state"] == "SCRIPT_DRAFT" and "duyệt" in res["waiting_for"]
    d = client.get(f"/api/stories/{st['id']}").json()
    assert d["script"]["qa_passed"] and d["research_pack"]["content"]["sources"]
    r = client.post(f"/api/stories/{st['id']}/upload-private", json={"confirm_private": False})
    assert r.status_code == 400
    r = client.post(f"/api/stories/{st['id']}/upload-private", json={"confirm_private": True})
    assert r.status_code == 200  # chỉ ghi nhận yêu cầu; gate kiểm tra ở pipeline (chưa READY)
    from ev_studio.config import get_settings

    get_settings().skip_quality_gates = False  # luồng duyệt tay với Script QA đầy đủ (mặc định app bỏ qua kiểm tra)
    try:
        edit = client.patch(f"/api/scripts/{d['script']['id']}", json={"spoken_script": "[CẢNH 1] lời đọc bẩn"})
        assert edit.status_code == 200 and edit.json()["qa_passed"] is False
        sv_id = edit.json()["script_version_id"]
        assert client.post(f"/api/scripts/{sv_id}/approve").status_code == 409  # QA chưa đạt → không được duyệt
    finally:
        get_settings().skip_quality_gates = True
    with SessionLocal() as s:
        versions = s.scalars(select(ScriptVersion).where(ScriptVersion.story_id == st["id"])).all()
        assert len(versions) == 2  # version cũ immutable


def test_claim_manual_verify_requires_source_and_note(client):
    news = client.get("/api/news").json()
    c = next(cl for n in news for cl in n["claims"] if cl["status"] == "contested")
    assert client.patch(f"/api/claims/{c['id']}", json={"status": "verified"}).status_code == 409
    ok = client.patch(f"/api/claims/{c['id']}", json={"status": "removed", "editor_note": "Bỏ số liệu tranh cãi"})
    assert ok.status_code == 200 and ok.json()["status"] == "removed"


def test_signed_media_links(client):
    stories = client.get("/api/stories").json()
    sid = stories[0]["id"]
    assert client.get(f"/api/media/{sid}/video", params={"t": "1.bad"}).status_code == 403


def test_auth_roles(client, monkeypatch):
    from pydantic import SecretStr

    from ev_studio.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "admin_token", SecretStr("owner-token-123"))
    monkeypatch.setattr(s, "editor_token", SecretStr("editor-token-456"))
    assert client.get("/api/stories").status_code == 401
    assert client.get("/api/stories", headers={"Authorization": "Bearer editor-token-456"}).status_code == 200
    r = client.patch("/api/settings", json={"autopilot_enabled": True}, headers={"Authorization": "Bearer editor-token-456"})
    assert r.status_code == 403
    r = client.post("/api/stories/x/upload-private", json={"confirm_private": True}, headers={"Authorization": "Bearer editor-token-456"})
    assert r.status_code == 403


class SlowTTS(DemoTTSAdapter):
    """Giọng đọc chậm hơn ước tính → bản đầu vượt thời lượng → hệ thống viết lại theo ACTUAL_AUDIO_SECONDS."""
    SYLLABLE_SECONDS = 0.335 * 1.45
    model_version = "slow-demo"


def test_duration_loop_rewrites_using_actual_audio(seeded):
    with SessionLocal() as s:
        n = s.scalar(select(NewsItem).where(NewsItem.title.contains("tổng chi phí")))
        st = Story(news_item_id=n.id, pillar="P1", topic="TCO test", mode="MANUAL", script_approved_by="tester", is_demo=True)
        s.add(st)
        s.commit()
        sid = st.id
    res = run_pipeline(sid, Providers(tts=SlowTTS()), stop_at="ASSETS_READY")
    with SessionLocal() as s:
        vs = s.scalars(select(ScriptVersion).where(ScriptVersion.story_id == sid).order_by(ScriptVersion.version)).all()
    assert len(vs) >= 2, res
    assert "ACTUAL_AUDIO_SECONDS" in vs[1].change_note
    assert vs[0].actual_audio_seconds > 200 and 120 <= vs[-1].actual_audio_seconds <= 200
    assert res["state"] == "ASSETS_READY"
