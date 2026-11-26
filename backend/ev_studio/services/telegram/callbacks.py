"""Xử lý nút Telegram: token 1 lần, có hạn, đúng chat_id allowlist. Không có nút nào công khai video;
nút "Dừng tự công khai" chỉ chặn việc tự công khai có hẹn giờ."""
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
    story = session.get(Story, tok.story_id)
    if tok.action == "hold":  # chỉ chặn tự công khai; các nút duyệt/sửa vẫn dùng được
        from ...workers.autoops import hold

        if story.state not in ("EDITOR_REVIEW", "APPROVED_PRIVATE", "UPLOADED_PRIVATE", "TELEGRAM_NOTIFIED"):
            session.commit()
            return answer(f"⛔ Video đang ở trạng thái {story.state}, không còn hẹn công khai")
        hold(session, story, f"telegram:{chat_id}", True)
        return answer("✅ Đã dừng tự công khai. Video giữ RIÊNG TƯ.") | {"held": True}
    # vô hiệu hoá token còn lại của cùng tin nhắn
    for other in session.query(TelegramCallbackToken).filter_by(story_id=tok.story_id, chat_id=chat_id, used_at=None):
        other.used_at = now()
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


POLL_WARN_AFTER = 300  # giây mất kết nối liên tục trước khi ghi cảnh báo vào Nhật ký lỗi


def poll_once(tg, offset, db_factory) -> int | None:
    for u in tg.get_updates(offset):
        offset = u["update_id"] + 1
        if "callback_query" in u:
            with db_factory() as db:
                handle_callback(db, u["callback_query"], tg)
    return offset


class PollHealth:
    """Theo dõi lỗi polling. Mạng chập chờn vài lần (tự hết) chỉ ghi INFO; mất kết nối liên tục ≥ 5 phút hoặc lỗi cấu hình
    (sai token, 409 Conflict…) mới ghi WARNING kèm nội dung lỗi để chủ kênh biết phải làm gì."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.fail_since, self.fails, self.warned = clock, None, 0, False

    def ok(self) -> None:
        if self.warned:
            log.info("Telegram polling đã kết nối lại sau %s lần lỗi", self.fails)
        self.fail_since, self.fails, self.warned = None, 0, False

    def failed(self, e: Exception) -> float:
        """Ghi log phù hợp, trả về số giây nên chờ trước lần thử tiếp."""
        from ...errors import RetryableError

        self.fails += 1
        now_ = self.clock()
        self.fail_since = self.fail_since or now_
        msg = str(e) or type(e).__name__
        if not isinstance(e, RetryableError):
            if not self.warned or self.fails % 30 == 1:
                log.warning("Telegram polling lỗi: %s", msg, extra={"evcs_source": "telegram"})
                self.warned = True
            return 60.0
        if not self.warned and now_ - self.fail_since >= POLL_WARN_AFTER:
            log.warning("Telegram polling mất kết nối liên tục %d phút (%s lần): %s. Kiểm tra Internet của máy chạy app; "
                        "nút duyệt trên Telegram sẽ hoạt động lại khi có mạng.", (now_ - self.fail_since) // 60, self.fails, msg,
                        extra={"evcs_source": "telegram"})
            self.warned = True
        else:
            log.info("Telegram polling tạm lỗi (sẽ thử lại): %s", msg)
        return min(60.0, 5.0 * 2 ** min(self.fails - 1, 4))


def start_polling() -> None:
    """Chạy local không có webhook: long-poll getUpdates trong thread nền."""
    from ...db import SessionLocal
    from ...providers import get_telegram

    def loop():
        from ...config import get_settings

        offset = None
        health = PollHealth()
        while True:
            s = get_settings()  # cấu hình có thể đổi trong trang Cấu hình → kiểm tra lại mỗi vòng
            if s.is_demo or not s.telegram_bot_token or s.telegram_webhook_secret:
                time.sleep(15)
                continue
            try:
                offset = poll_once(get_telegram(), offset, SessionLocal)
                health.ok()
            except Exception as e:  # noqa: BLE001 — thread nền không được chết
                time.sleep(health.failed(e))

    threading.Thread(target=loop, daemon=True, name="telegram-poll").start()
