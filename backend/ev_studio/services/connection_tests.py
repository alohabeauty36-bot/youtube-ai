"""Nút 'Kiểm tra' trong trang Cấu hình: gọi nhẹ từng dịch vụ để xác nhận key/kết nối. Không làm lộ secret."""
from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import Settings
from ..security import redact


def _ok(msg: str, **extra) -> dict:
    return {"ok": True, "message": msg, **extra}


def _fail(msg: str, **extra) -> dict:
    return {"ok": False, "message": redact(msg), **extra}


def test_anthropic(s: Settings, client: Any = None) -> dict:
    if not s.anthropic_api_key:
        return _fail("Chưa nhập Anthropic API key")
    try:
        import anthropic

        c = client or anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value(), timeout=20, max_retries=1)
        models = [m.id for m in c.models.list(limit=50).data]
    except Exception as e:
        name = type(e).__name__
        if "Authentication" in name:
            return _fail("API key không hợp lệ")
        return _fail(f"Không gọi được Anthropic: {name}: {e}")
    if s.anthropic_model in models:
        return _ok(f"Kết nối được. Model '{s.anthropic_model}' khả dụng.", models=models)
    return _fail(f"Key hợp lệ nhưng tài khoản không có model '{s.anthropic_model}'. Chọn một model trong danh sách.", models=models)


def test_telegram(s: Settings, client: httpx.Client | None = None, send_test: bool = False) -> dict:
    if not s.telegram_bot_token:
        return _fail("Chưa nhập bot token")
    base = f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}"
    c = client or httpx.Client(timeout=15)
    try:
        me = c.get(f"{base}/getMe").json()
        if not me.get("ok"):
            return _fail("Bot token không hợp lệ")
        chats: dict[str, str] = {}
        if not s.telegram_webhook_secret:
            upd = c.get(f"{base}/getUpdates", params={"timeout": 0, "limit": 50}).json()
            for u in upd.get("result", []):
                m = u.get("message") or u.get("my_chat_member") or (u.get("callback_query") or {}).get("message") or {}
                ch = m.get("chat") or {}
                if ch.get("id"):
                    chats[str(ch["id"])] = ch.get("title") or " ".join(x for x in (ch.get("first_name"), ch.get("last_name")) if x) or ch.get("username", "")
        sent = []
        if send_test:
            for cid in sorted(s.allowed_chat_ids):
                r = c.post(f"{base}/sendMessage", json={"chat_id": cid, "text": "✅ Tin nhắn thử từ EV Creator Studio. Bot đã kết nối."}).json()
                sent.append({"chat_id": cid, "ok": bool(r.get("ok")), "error": r.get("description")})
    except httpx.HTTPError as e:
        return _fail(f"Không kết nối được Telegram: {type(e).__name__}")
    msg = f"Bot @{me['result'].get('username')} hoạt động."
    if not s.allowed_chat_ids:
        msg += " Chưa có Chat ID được phép — nhắn /start cho bot rồi chọn ID bên dưới."
    return _ok(msg, bot=me["result"].get("username"), recent_chats=[{"chat_id": k, "name": v} for k, v in chats.items()], sent=sent)


def test_pexels(s: Settings, client: httpx.Client | None = None) -> dict:
    if not s.pexels_api_key:
        return _fail("Chưa nhập Pexels API key (tuỳ chọn)")
    c = client or httpx.Client(timeout=15)
    try:
        r = c.get("https://api.pexels.com/v1/search", params={"query": "electric car", "per_page": 1},
                  headers={"Authorization": s.pexels_api_key.get_secret_value()})
    except httpx.HTTPError as e:
        return _fail(f"Không kết nối được Pexels: {type(e).__name__}")
    if r.status_code == 200:
        return _ok(f"Pexels hoạt động ({r.json().get('total_results', 0)} ảnh cho 'electric car').")
    return _fail(f"Pexels trả HTTP {r.status_code} — kiểm tra key")


def test_tts(s: Settings, client: httpx.Client | None = None) -> dict:
    p = s.tts_provider
    if p == "vieneu_http":
        c = client or httpx.Client(timeout=10)
        try:
            r = c.get(s.vieneu_http_url.rstrip("/") + "/health")
            voices = c.get(s.vieneu_http_url.rstrip("/") + "/v1/voices").json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            return _fail(f"Không kết nối được server VieNeu tại {s.vieneu_http_url}: {type(e).__name__}")
        return _ok("Server VieNeu hoạt động.", voices=voices) if r.status_code == 200 else _fail(f"Server VieNeu trả {r.status_code}")
    if p in ("vieneu_local",) or (p == "auto" and not s.is_demo):
        try:
            import importlib.metadata as md

            import vieneu  # noqa: F401
            return _ok(f"Đã cài vieneu {md.version('vieneu')}. Model sẽ tải lần đầu khi đọc (vài trăm MB). "
                       "Dùng 'Nghe thử 30 giây' ở trang Biên tập để kiểm tra giọng.")
        except ImportError:
            return _fail("Chưa cài thư viện VieNeu. Chạy: python run.py --with-tts  (hoặc pip install vieneu==3.8.5)")
    return _ok("Đang dùng TTS DEMO (âm thanh giả lập, không phải giọng nói). Chọn vieneu_local hoặc vieneu_http để dùng giọng thật.")


def test_youtube(s: Settings) -> dict:
    redirect = s.public_base_url.rstrip("/") + "/api/oauth/google/callback"
    raw = s.google_client_secrets_json.get_secret_value() if s.google_client_secrets_json else None
    if not raw and s.google_client_secrets_file and s.google_client_secrets_file.exists():
        raw = s.google_client_secrets_file.read_text(encoding="utf-8")
    if not raw:
        return _fail("Chưa có OAuth client. Dán nội dung client_secret.json.", redirect_uri=redirect)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _fail("client_secret.json không phải JSON hợp lệ", redirect_uri=redirect)
    cfg = data.get("web") or data.get("installed") or {}
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        return _fail("Thiếu client_id/client_secret trong JSON", redirect_uri=redirect)
    if "installed" in data:
        return _fail("Đây là client loại 'Desktop'. Hãy tạo OAuth client loại 'Web application'.", redirect_uri=redirect)
    uris = cfg.get("redirect_uris") or []
    if redirect not in uris:
        return _fail(f"Redirect URI chưa đăng ký. Thêm chính xác '{redirect}' vào OAuth client rồi tải lại JSON.",
                     redirect_uri=redirect, registered=uris)
    return _ok("OAuth client hợp lệ. Bấm 'Kết nối kênh YouTube' để cấp quyền và chọn kênh.", redirect_uri=redirect,
               client_id=cfg["client_id"][:12] + "…")


TESTS = {"anthropic": test_anthropic, "telegram": test_telegram, "pexels": test_pexels, "tts": test_tts, "youtube": test_youtube}
