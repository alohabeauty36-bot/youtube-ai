"""Nút 'Kiểm tra' trong trang Cấu hình: gọi nhẹ từng dịch vụ để xác nhận key/kết nối. Không làm lộ secret."""
from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import Settings
from ..security import redact, sign_link


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
    """Đọc thử MỘT câu bằng đúng bộ đọc mà pipeline sẽ dùng → lộ ngay lỗi thật (chưa cài, thiếu model, sai tên giọng…)."""
    import time

    import numpy as np

    from ..errors import BlockedByCredentials
    from ..providers import get_tts
    from .tts.adapters import DemoTTSAdapter, VieNeuHttpAdapter
    from .tts.pipeline import segment_qc, write_wav

    try:
        tts = get_tts(s)
    except BlockedByCredentials as e:
        return _fail(f"{e}. Cách cài: đóng ứng dụng, chạy `python run.py --with-tts` "
                     "(Windows: bấm đúp start-giong-that.bat) rồi mở lại.")
    if isinstance(tts, VieNeuHttpAdapter) and client is not None:
        tts.client = client
    if isinstance(tts, DemoTTSAdapter):
        return _fail(DemoTTSAdapter.NOTICE, demo=True)
    text = "Xin chào, đây là giọng đọc thử của kênh xe điện. Nếu bạn nghe rõ câu này, giọng đọc đã sẵn sàng."
    t0 = time.time()
    try:
        audio = np.asarray(tts.synthesize_one(text, s.vieneu_voice), dtype=np.float32).reshape(-1)
    except Exception as e:  # noqa: BLE001 - báo nguyên nhân thật cho chủ kênh
        hint = ""
        msg = f"{type(e).__name__}: {e}"
        if "not found" in msg.lower() and "voice" in msg.lower():
            hint = " Tên giọng không có trong model — chọn lại ở ô 'Giọng mặc định'."
        elif isinstance(tts, VieNeuHttpAdapter):
            hint = f" Kiểm tra server VieNeu tại {s.vieneu_http_url} đang chạy."
        else:
            hint = (" Lần đầu VieNeu cần Internet để tải model từ HuggingFace (vài trăm MB); "
                    "kiểm tra mạng/tường lửa rồi bấm Kiểm tra lại.")
        return _fail(f"Không đọc được câu thử với giọng '{s.vieneu_voice}': {msg[:400]}.{hint}")
    sr = tts.sample_rate
    qc = segment_qc(audio, sr, text)
    out = s.artifacts_dir / "_tts_test" / "test.wav"
    write_wav(out, audio, sr)
    secs = round(time.time() - t0, 1)
    base = {"engine": tts.name, "voice": s.vieneu_voice, "qc": qc, "seconds_to_generate": secs, "audio_url": "/api/voices/test-audio?t=" + sign_link("media", "tts-test", 3600) + f"&v={int(t0)}"}
    if not qc["ok"]:
        return _fail(f"Có tạo audio nhưng chất lượng bất thường: {', '.join(qc['issues'])}. Nghe thử bên dưới.", **base)
    return _ok(f"Giọng '{s.vieneu_voice}' ({tts.name}) đọc tốt: {qc['duration']}s audio, mất {secs}s để tạo. Nghe thử bên dưới.", **base)


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


def test_ai_images(s: Settings, generators: dict | None = None) -> dict:
    """Tạo thử 1 ảnh qua từng nguồn đã cấu hình (fal.ai: tốn ~0,003 USD)."""
    import base64
    import io

    from PIL import Image

    from .media.ai_images import build_prompt, generator_for, providers_in_order

    order = list(generators) if generators else providers_in_order(s)
    if not order:
        return _fail("Chưa cấu hình nguồn ảnh AI: nhập Cloudflare Account ID + API token, hoặc fal.ai API key.")
    results = []
    for p in order:
        try:
            g = (generators or {}).get(p) or generator_for(s, p)
            raw = g.generate(build_prompt("electric car charging station at dusk"), seed=7)
            im = Image.open(io.BytesIO(raw))
            results.append({"provider": p, "ok": True, "size": f"{im.width}×{im.height}"})
        except Exception as e:  # noqa: BLE001
            results.append({"provider": p, "ok": False, "error": redact(str(e))[:200]})
    good = [r for r in results if r["ok"]]
    msg = "; ".join(f"{r['provider']}: " + (f"tạo được ảnh {r['size']}" if r["ok"] else r["error"]) for r in results)
    return (_ok if good else _fail)(msg, providers=results)


TESTS = {"ai_images": test_ai_images, "anthropic": test_anthropic, "telegram": test_telegram, "pexels": test_pexels, "tts": test_tts, "youtube": test_youtube}
