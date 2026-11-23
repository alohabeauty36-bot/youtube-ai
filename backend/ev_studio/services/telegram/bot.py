"""Telegram Bot API: thông báo video nháp RIÊNG TƯ + nút duyệt/sửa. 'Đã kiểm duyệt' KHÔNG công khai video."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

import httpx

from ...config import get_settings
from ...errors import BlockedByCredentials, PermanentError, RetryableError
from ...security import redact

log = logging.getLogger(__name__)


class TelegramClient(Protocol):
    is_demo: bool

    def send(self, chat_id: str, text: str, buttons: list[list[dict]], photo: Path | None = None) -> str: ...
    def answer_callback(self, callback_id: str, text: str) -> None: ...
    def get_updates(self, offset: int | None) -> list[dict]: ...


class RealTelegramClient:
    is_demo = False

    def __init__(self, token: str, client: httpx.Client | None = None):
        if not token:
            raise BlockedByCredentials("Thiếu TELEGRAM_BOT_TOKEN")
        self._base = f"https://api.telegram.org/bot{token}"
        self.client = client or httpx.Client(timeout=30)

    def _call(self, method: str, data: dict | None = None, files: dict | None = None) -> dict:
        try:
            if files:
                r = self.client.post(f"{self._base}/{method}", data=data, files=files)
            else:
                r = self.client.post(f"{self._base}/{method}", json=data or {})
        except httpx.HTTPError as e:
            raise RetryableError(f"Telegram mất kết nối: {type(e).__name__}") from None
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"Telegram {r.status_code}")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not body.get("ok"):
            if r.status_code in (401, 404):
                raise BlockedByCredentials("TELEGRAM_BOT_TOKEN không hợp lệ")
            raise PermanentError(f"Telegram lỗi: {redact(body.get('description', r.status_code))}")
        return body["result"]

    def send(self, chat_id, text, buttons, photo=None):
        markup = json.dumps({"inline_keyboard": buttons})
        if photo and photo.exists():
            with open(photo, "rb") as f:
                res = self._call("sendPhoto", data={"chat_id": chat_id, "caption": text[:1024], "reply_markup": markup},
                                 files={"photo": (photo.name, f, "image/jpeg")})
            if len(text) > 1024:
                self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
        else:
            res = self._call("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": json.loads(markup),
                                             "disable_web_page_preview": True})
        return str(res["message_id"])

    def answer_callback(self, callback_id, text):
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:190]})

    def get_updates(self, offset):
        return self._call("getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["callback_query"]})


class DemoTelegramClient:
    """Không gửi Telegram thật: ghi outbox JSONL. `offline=True` mô phỏng bot mất kết nối."""
    is_demo = True

    def __init__(self, offline: bool = False, outbox: Path | None = None):
        self.offline = offline
        self.outbox = outbox or (get_settings().artifacts_dir / "demo_telegram" / "outbox.jsonl")
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        self.answers: list[str] = []

    def send(self, chat_id, text, buttons, photo=None):
        if self.offline:
            raise RetryableError("DEMO: bot Telegram mất kết nối (giả lập)")
        mid = str(sum(1 for _ in open(self.outbox, encoding="utf-8")) + 1 if self.outbox.exists() else 1)
        with open(self.outbox, "a", encoding="utf-8") as f:
            f.write(json.dumps({"message_id": mid, "chat_id": chat_id, "text": text, "buttons": buttons,
                                "photo": str(photo) if photo else None}, ensure_ascii=False) + "\n")
        return mid

    def answer_callback(self, callback_id, text):
        self.answers.append(text)

    def get_updates(self, offset):
        return []


def build_review_message(*, topic: str, pillar: str, title: str, duration_s: float, source_line: str,
                         qa_summary: str, unverified: int, review_url: str, studio_url: str, video_id: str,
                         is_demo: bool) -> str:
    mm, ss = divmod(int(round(duration_s)), 60)
    lines = []
    if is_demo:
        lines.append("[DEMO — không có upload thật]")
    lines += [
        "🎬 VIDEO NHÁP SẴN SÀNG KIỂM DUYỆT",
        f"Chủ đề: {topic} — {pillar}",
        f"Tiêu đề: {title}",
        f"Độ dài: {mm:02d}:{ss:02d}",
        f"Tin gốc: {source_line}",
        "Trạng thái YouTube: RIÊNG TƯ",
        f"YouTube video ID: {video_id}",
        f"Kiểm tra: {qa_summary} — {unverified} claim chưa xác minh",
        f"Xem bản xem trước: {review_url}",
        f"Mở YouTube Studio: {studio_url}",
        "Lưu ý: bấm 'Đã kiểm duyệt' KHÔNG công khai video. Hãy tự đổi trạng thái trên YouTube Studio.",
    ]
    return "\n".join(lines)


def build_buttons(approve_token: str, revise_token: str, studio_url: str, review_url: str) -> list[list[dict]]:
    rows = [[{"text": "✏️ Yêu cầu sửa", "callback_data": f"rv|{revise_token}"},
             {"text": "✅ Đã kiểm duyệt", "callback_data": f"rv|{approve_token}"}]]
    links = []
    if studio_url.startswith("https://"):
        links.append({"text": "Mở Studio", "url": studio_url})
    if review_url.startswith("https://"):  # Telegram chỉ chấp nhận URL công khai hợp lệ
        links.append({"text": "Xem preview", "url": review_url})
    if links:
        rows.append(links)
    return rows
