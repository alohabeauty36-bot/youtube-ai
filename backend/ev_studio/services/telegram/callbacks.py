"""Xử lý nút Telegram: token 1 lần, có hạn, đúng chat_id allowlist. Không có hành động công khai."""
from __future__ import annotations

import logging
import threading
import time

from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Story, TelegramCallbackToken, now
from ...security import audit

log = logging.getLogger(__name__)


def handle_callback(session: Session, callback: dict, tg=None) -> dict:
    from ...workers.jobs import apply_review

    s = get_settings()
    cb_id = callback.get("id", "")
    chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id") or (callback.get("from") or {}).get("id") or "")
    data = callback.get("data") or ""

    def answer(text: str) -> dict:
        if tg is not None and cb_id:
            try:
                tg.answer_callback(cb_id, text)
            except Exception:  # pragma: no cover
                pass
        return {"ok": text.startswith("✅") or text.startswith("✏️"), "message": text}

    allowed = s.allowed_chat_ids or ({"DEMO_CHAT"} if s.is_demo else set())
    if chat_id not in allowed:
        audit(session, f"telegram:{chat_id}", "telegram.callback_rejected", "telegram", None, reason="chat không trong allowlist")
        session.commit()
        return answer("⛔ Không có quyền")
    if not data.startswith("rv|"):
        return answer("⛔ Dữ liệu không hợp lệ")
    tok = session.get(TelegramCallbackToken, data[3:])
    if tok is None or tok.chat_id != chat_id:
        return answer("⛔ Token không hợp lệ")
    exp = tok.expires_at if tok.expires_at.tzinfo else tok.expires_at.replace(tzinfo=now().tzinfo)
    if tok.used_at is not None:
        return answer("⛔ Nút này đã được dùng (chống replay)")
    if exp < now():
        return answer("⛔ Nút đã hết hạn")
    tok.used_at = now()
    # vô hiệu hoá token còn lại của cùng tin nhắn
    for other in session.query(TelegramCallbackToken).filter_by(story_id=tok.story_id, chat_id=chat_id, used_at=None):
        other.used_at = now()
    story = session.get(Story, tok.story_id)
    try:
        res = apply_review(session, story, tok.action, actor=f"telegram:{chat_id}", via="telegram",
                           note="Yêu cầu sửa từ Telegram" if tok.action == "request_revision" else None)
        session.commit()
    except Exception as e:
        session.rollback()
        log.warning("Nút Telegram '%s' lỗi cho story %s: %s", tok.action, tok.story_id, e, exc_info=e,
                    extra={"evcs_story_id": tok.story_id})
        return answer(f"⛔ {e}")
    if tok.action == "approve":
        return answer("✅ Đã ghi nhận kiểm duyệt. Video VẪN RIÊNG TƯ — hãy tự công khai trên YouTube Studio.") | res
    return answer("✏️ Đã tạo phiên bản sửa mới; bản private cũ giữ nguyên.") | res


def start_polling() -> None:
    """Chạy local không có webhook: long-poll getUpdates trong thread nền."""
    from ...db import SessionLocal
    from ...providers import get_telegram

    def loop():
        from ...config import get_settings

        offset = None
        while True:
            s = get_settings()  # cấu hình có thể đổi trong trang Cấu hình → kiểm tra lại mỗi vòng
            if s.is_demo or not s.telegram_bot_token or s.telegram_webhook_secret:
                time.sleep(15)
                continue
            try:
                tg = get_telegram()
                for u in tg.get_updates(offset):
                    offset = u["update_id"] + 1
                    if "callback_query" in u:
                        with SessionLocal() as db:
                            handle_callback(db, u["callback_query"], tg)
            except Exception as e:
                log.warning("Telegram polling lỗi: %s", type(e).__name__)
                time.sleep(10)

    threading.Thread(target=loop, daemon=True, name="telegram-poll").start()
