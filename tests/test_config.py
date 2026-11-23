"""Cấu hình API trong trang Thiết lập: lưu mã hoá, che giá trị, ưu tiên, kiểm tra kết nối, quyền truy cập."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from ev_studio.config import get_settings, reset_settings_cache
from ev_studio.db import SessionLocal
from ev_studio.models import AppSetting
from ev_studio.runtime_config import PREFIX, describe, save

KEY = "sk-ant-api03-SUPERSECRETVALUE-1234abcd"


@pytest.fixture()
def clean_config(db_ready):
    yield
    with SessionLocal() as s:
        s.execute(delete(AppSetting).where(AppSetting.key.like(PREFIX + "%")))
        s.commit()
    reset_settings_cache()


@pytest.fixture()
def client(db_ready):
    from ev_studio.api.main import app

    with TestClient(app) as c:
        yield c


def test_secret_saved_encrypted_masked_and_applied(clean_config):
    with SessionLocal() as s:
        save(s, {"anthropic_api_key": KEY, "anthropic_model": "claude-opus-5-5"}, [], "test")
        raw = s.get(AppSetting, PREFIX + "anthropic_api_key").value
        assert KEY not in json.dumps(raw) and "enc" in raw
        d = describe(s)
    f = {x["key"]: x for x in d["fields"]}
    assert f["anthropic_api_key"]["is_set"] and f["anthropic_api_key"]["masked"] == "••••abcd"
    assert f["anthropic_api_key"]["value"] is None and f["anthropic_api_key"]["source"] == "ui"
    assert f["anthropic_model"]["value"] == "claude-opus-5-5"
    assert get_settings().anthropic_api_key.get_secret_value() == KEY
    assert KEY not in json.dumps(d, default=str)


def test_empty_secret_keeps_value_and_clear_reverts(clean_config):
    with SessionLocal() as s:
        save(s, {"telegram_bot_token": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}, [], "t")
        save(s, {"telegram_bot_token": "", "telegram_allowed_chat_ids": "42"}, [], "t")
        assert get_settings().telegram_bot_token.get_secret_value().startswith("123456789:")
        assert get_settings().allowed_chat_ids == {"42"}
        save(s, {}, ["telegram_bot_token", "telegram_allowed_chat_ids"], "t")
    assert get_settings().telegram_bot_token is None and get_settings().allowed_chat_ids == set()


@pytest.mark.parametrize("values,msg", [
    ({"app_mode": "public"}, "một trong"),
    ({"google_client_secrets_json": "{\"foo\": 1}"}, "không hợp lệ"),
    ({"google_client_secrets_json": "không phải json"}, "OAuth"),
    ({"duration_min_seconds": 700}, "nhỏ hơn"),
    ({"privacy_status": "public"}, "Không hỗ trợ"),
])
def test_invalid_values_rejected(clean_config, values, msg):
    with SessionLocal() as s, pytest.raises(ValueError, match=msg):
        save(s, values, [], "t")


def test_config_api_masks_and_requires_owner(clean_config, client, monkeypatch):
    r = client.put("/api/config", json={"values": {"pexels_api_key": "pexels-secret-key-9999"}})
    assert r.status_code == 200 and "pexels-secret-key" not in r.text
    got = client.get("/api/config").json()
    assert got["oauth_redirect_uri"].endswith("/api/oauth/google/callback")
    assert next(f for f in got["fields"] if f["key"] == "pexels_api_key")["masked"] == "••••9999"
    assert client.put("/api/config", json={"values": {"app_mode": "xyz"}}).status_code == 400
    from pydantic import SecretStr

    s = get_settings()
    monkeypatch.setattr(s, "admin_token", SecretStr("owner-tok-123"))
    monkeypatch.setattr(s, "editor_token", SecretStr("editor-tok-456"))
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/config", headers={"Authorization": "Bearer editor-tok-456"}).status_code == 403
    assert client.get("/api/config", headers={"Authorization": "Bearer owner-tok-123"}).status_code == 200


def test_trusted_localhost_only_without_proxy_headers(monkeypatch):
    from ev_studio.api.deps import is_trusted_local

    s = get_settings()
    monkeypatch.setattr(s, "trust_localhost", True)
    req = lambda host, headers=None: SimpleNamespace(client=SimpleNamespace(host=host), headers=headers or {})  # noqa: E731
    assert is_trusted_local(req("127.0.0.1"))
    assert is_trusted_local(req("::1"))
    assert not is_trusted_local(req("192.168.1.5"))
    assert not is_trusted_local(req("127.0.0.1", {"x-forwarded-for": "8.8.8.8"}))
    monkeypatch.setattr(s, "trust_localhost", False)
    assert not is_trusted_local(req("127.0.0.1"))


def test_connection_test_telegram_lists_chats_and_hides_token():
    from ev_studio.services.connection_tests import test_telegram

    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ"

    def h(req):
        if req.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "ev_bot"}})
        if req.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 1, "message": {"chat": {"id": 5551, "first_name": "Chủ", "last_name": "Kênh"}}}]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    from pydantic import SecretStr

    s = get_settings().model_copy(update={"telegram_bot_token": SecretStr(token), "telegram_allowed_chat_ids": "5551"})
    r = test_telegram(s, client=httpx.Client(transport=httpx.MockTransport(h)), send_test=True)
    assert r["ok"] and r["bot"] == "ev_bot" and r["recent_chats"] == [{"chat_id": "5551", "name": "Chủ Kênh"}]
    assert r["sent"][0]["ok"] and token not in json.dumps(r)


def test_connection_test_youtube_checks_redirect_uri():
    from pydantic import SecretStr

    from ev_studio.services.connection_tests import test_youtube

    base = get_settings()
    redirect = base.public_base_url.rstrip("/") + "/api/oauth/google/callback"
    web = {"web": {"client_id": "123.apps.googleusercontent.com", "client_secret": "x", "redirect_uris": ["http://other/cb"]}}
    r = test_youtube(base.model_copy(update={"google_client_secrets_json": SecretStr(json.dumps(web))}))
    assert not r["ok"] and redirect in r["message"]
    web["web"]["redirect_uris"] = [redirect]
    assert test_youtube(base.model_copy(update={"google_client_secrets_json": SecretStr(json.dumps(web))}))["ok"]
    desktop = {"installed": web["web"]}
    assert "Web application" in test_youtube(base.model_copy(update={"google_client_secrets_json": SecretStr(json.dumps(desktop))}))["message"]


def test_connection_test_anthropic_model_listing():
    from pydantic import SecretStr

    from ev_studio.services.connection_tests import test_anthropic

    fake = SimpleNamespace(models=SimpleNamespace(list=lambda limit: SimpleNamespace(data=[SimpleNamespace(id="claude-sonnet-5-5")])))
    s = get_settings().model_copy(update={"anthropic_api_key": SecretStr(KEY)})
    assert test_anthropic(s.model_copy(update={"anthropic_model": "claude-sonnet-5-5"}), client=fake)["ok"]
    bad = test_anthropic(s.model_copy(update={"anthropic_model": "claude-x"}), client=fake)
    assert not bad["ok"] and bad["models"] == ["claude-sonnet-5-5"]


def test_oauth_flow_from_pasted_json():
    from pydantic import SecretStr

    from ev_studio.services.youtube.client import make_oauth_flow

    base = get_settings()
    redirect = base.public_base_url.rstrip("/") + "/api/oauth/google/callback"
    web = {"web": {"client_id": "123.apps.googleusercontent.com", "client_secret": "x", "redirect_uris": [redirect],
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}
    flow = make_oauth_flow(base.model_copy(update={"google_client_secrets_json": SecretStr(json.dumps(web))}), "state1")
    url, _ = flow.authorization_url(access_type="offline")
    assert "youtube.upload" in url and "state=state1" in url and "client_id=123.apps" in url


def test_web_ui_served_by_backend(client):
    r = client.get("/")
    assert r.status_code == 200 and "EV Creator Studio" in r.text
    assert client.get("/settings/").status_code == 200
    assert client.get("/story/?id=abc").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
