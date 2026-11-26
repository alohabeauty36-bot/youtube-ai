"""Bất biến PRIVATE, không có đường công khai, che secrets, state machine, Telegram/YouTube client (mock)."""
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from ev_studio.errors import PrivacyViolation
from ev_studio.services.youtube.privacy import assert_private_body, build_insert_body
from ev_studio.state_machine import InvalidTransition, StoryState as S, assert_transition

SRC = Path(__file__).resolve().parents[1] / "backend" / "ev_studio"


def body(**status):
    return {"snippet": {"title": "t"}, "status": {"privacyStatus": "private", **status}}


def test_insert_body_is_private():
    b = build_insert_body(title="T" * 150, description="d", tags=["a"], category_id="2", made_for_kids_confirmed_false=False,
                          contains_synthetic_media=True)
    assert b["status"]["privacyStatus"] == "private" and "publishAt" not in b["status"]
    assert "selfDeclaredMadeForKids" not in b["status"]  # chỉ khai báo khi chủ kênh đã xác nhận
    assert len(b["snippet"]["title"]) == 100 and b["snippet"]["defaultLanguage"] == "vi"


@pytest.mark.parametrize("bad", [{"privacyStatus": "public"}, {"privacyStatus": "unlisted"}, {"privacyStatus": None},
                                 {"publishAt": "2026-12-01T00:00:00Z"}])
def test_any_non_private_is_rejected(bad):
    with pytest.raises(PrivacyViolation):
        assert_private_body(body(**bad))


def test_source_code_never_sets_non_private():
    """Quét mã nguồn: không có chỗ nào gán privacyStatus khác private, ngoại trừ hàm mô phỏng CHỦ KÊNH tự công khai trong demo."""
    pat = re.compile(r"privacyStatus[\"']?\]?\s*[:=]\s*[\"'](public|unlisted)", re.I)
    hits = []
    for f in SRC.rglob("*.py"):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                hits.append((f.name, i))
    demo_fn = (SRC / "services/youtube/client.py").read_text(encoding="utf-8")
    allowed_line = next(i for i, l in enumerate(demo_fn.splitlines(), 1) if '["privacyStatus"] = "public"' in l)
    assert hits == [("client.py", allowed_line)]
    assert "def simulate_owner_published" in demo_fn


def test_only_one_code_path_can_make_a_video_public():
    """Tự công khai có hẹn giờ (Tự động hoàn toàn) là đường DUY NHẤT: publish.py tạo status public, chỉ autoops.py gọi nó."""
    users = []
    for f in SRC.rglob("*.py"):
        txt = f.read_text(encoding="utf-8")
        if "publish_video(" in txt or "publish_status(" in txt:
            users.append(f.relative_to(SRC).as_posix())
    assert sorted(users) == ["services/youtube/publish.py", "workers/autoops.py"]
    auto = (SRC / "workers/autoops.py").read_text(encoding="utf-8")
    assert "if not auto_publish_active():" in auto and "Story.publish_hold.is_(False)" in auto
    assert "_publish_gates(db, st)" in auto and "Chưa báo chủ kênh qua Telegram" in auto


def test_api_has_no_publish_route_or_privacy_param():
    from ev_studio.api.main import app

    paths = [r.path for r in app.routes]
    assert not [p for p in paths if "publish" in p.lower()]
    up = next(r for r in app.routes if getattr(r, "path", "") == "/api/stories/{sid}/upload-private")
    params = {p.name for p in up.dependant.body_params + up.dependant.query_params}
    assert params == {"confirm_private"}


def test_no_transition_to_public_state():
    with pytest.raises(InvalidTransition):
        assert_transition(S.EDITOR_REVIEW, S.UPLOADING)
    with pytest.raises(InvalidTransition):
        assert_transition(S.DISCOVERED, S.READY_TO_UPLOAD)
    assert_transition(S.EDITOR_REVIEW, S.APPROVED_PRIVATE)
    assert not any(s.value == "PUBLISHED" for s in S)
    # trạng thái công khai do app tạo chỉ có PUBLISHED_AUTO: mặc định bị chặn; chỉ mở khi bật Tự động hoàn toàn + thời gian chờ
    with pytest.raises(InvalidTransition, match="Tự động hoàn toàn"):
        assert_transition(S.EDITOR_REVIEW, S.PUBLISHED_AUTO)
    import ev_studio.config as cfg

    on = cfg.get_settings().model_copy(update={"full_auto": True, "auto_publish_delay_minutes": 120})
    orig = cfg.get_settings
    cfg.get_settings = lambda: on
    try:
        assert_transition(S.EDITOR_REVIEW, S.PUBLISHED_AUTO)
        assert_transition(S.APPROVED_PRIVATE, S.PUBLISHED_AUTO)
        for src in (S.READY_TO_UPLOAD, S.UPLOADING, S.UPLOADED_PRIVATE, S.TELEGRAM_NOTIFIED, S.REVISION_REQUESTED, S.REJECTED):
            with pytest.raises(InvalidTransition):
                assert_transition(src, S.PUBLISHED_AUTO)
    finally:
        cfg.get_settings = orig
    for src in (S.READY_TO_UPLOAD, S.UPLOADING, S.UPLOADED_PRIVATE, S.TELEGRAM_NOTIFIED, S.REVISION_REQUESTED, S.REJECTED):
        with pytest.raises(InvalidTransition):
            assert_transition(src, S.PUBLISHED_AUTO)


def test_db_rejects_non_private_upload_row(db):
    from sqlalchemy.exc import IntegrityError

    from ev_studio.models import YouTubeUpload

    db.add(YouTubeUpload(story_id="x", render_id="y", channel_id="z", idempotency_key="k-public", privacy_status_requested="public"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_redaction_patterns_and_logging(caplog):
    from ev_studio.security import RedactingFilter, redact

    raw = ("key sk-ant-api03-ABCDEFGHIJKLMNOP token 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ "
           "ya29.a0AfH6SMBxyz refresh_token=1//0gabcdefghijklmnopqrstuvwxyz client_secret: GOCSPX-abc")
    out = redact(raw)
    for s in ("sk-ant-api03", "AAHdqTcv", "ya29.a0", "1//0gabc", "GOCSPX-abc"):
        assert s not in out
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "lỗi với %s", (raw,), None)
    RedactingFilter().filter(rec)
    assert "sk-ant" not in rec.getMessage()


def test_token_encryption_roundtrip():
    from ev_studio.security import decrypt_str, encrypt_str

    enc = encrypt_str('{"refresh_token":"1//secret"}')
    assert "secret" not in enc and decrypt_str(enc).endswith('"1//secret"}')


def test_signed_links():
    from ev_studio.security import sign_link, verify_link

    t = sign_link("review", "story-1", 60)
    assert verify_link("review", "story-1", t)
    assert not verify_link("review", "story-2", t)
    assert not verify_link("media", "story-1", t)
    assert not verify_link("review", "story-1", "1.bad")


def test_telegram_real_client_payload_and_no_token_leak():
    from ev_studio.errors import RetryableError
    from ev_studio.services.telegram.bot import RealTelegramClient, build_buttons

    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"
    sent = {}

    def ok(req):
        sent["url"], sent["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    c = RealTelegramClient(token, client=httpx.Client(transport=httpx.MockTransport(ok)))
    btn = build_buttons("A" * 24, "B" * 24, "https://studio.youtube.com/video/x/edit", "http://localhost/review")
    assert c.send("42", "xin chào", btn) == "77"
    assert sent["body"]["chat_id"] == "42" and sent["url"].endswith("/sendMessage")
    assert all(len(b.get("callback_data", "")) <= 64 for row in btn for b in row)
    assert not any("localhost" in b.get("url", "") for row in btn for b in row)  # Telegram cần URL https công khai

    def down(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")

    c2 = RealTelegramClient(token, client=httpx.Client(transport=httpx.MockTransport(down)))
    with pytest.raises(RetryableError) as ei:
        c2.send("42", "x", [])
    assert token not in str(ei.value) and ei.value.__cause__ is None


class _Req:
    def __init__(self, result=None, chunks=None):
        self.result, self.chunks = result, chunks or []

    def execute(self):
        return self.result

    def next_chunk(self):
        item = self.chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_google_client_upload_private_and_resume_on_network_error(tmp_path):
    from ev_studio.services.youtube.client import GoogleYouTubeClient

    f = tmp_path / "v.mp4"
    f.write_bytes(b"0" * 100)
    calls = {}

    class Videos:
        def insert(self, part, body, media_body, notifySubscribers):
            calls["body"] = body
            return _Req(chunks=[ConnectionError("drop"), (SimpleNamespace(progress=lambda: 0.5), None), (None, {"id": "VID1"})])

        def list(self, part, id):
            return _Req({"items": [{"status": {"privacyStatus": "private"}}]})

    svc = SimpleNamespace(videos=lambda: Videos())
    c = GoogleYouTubeClient(SimpleNamespace(expired=False), service=svc)
    import ev_studio.services.youtube.client as mod

    mod.time.sleep = lambda s: None
    vid = c.insert_video(build_insert_body(title="t" * 20, description="d", tags=[], category_id="2",
                                           made_for_kids_confirmed_false=True, contains_synthetic_media=True), f)
    assert vid == "VID1" and calls["body"]["status"]["privacyStatus"] == "private"
    assert c.get_privacy("VID1") == "private"
    with pytest.raises(PrivacyViolation):
        c.insert_video({"snippet": {}, "status": {"privacyStatus": "public"}}, f)
