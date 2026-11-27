"""Gợi ý "việc cần làm" bằng lời thường cho video đang dừng (Cần người xử lý / Lỗi), kèm nút bấm được trên giao diện.

Mỗi gợi ý: {"text": lời hướng dẫn, "action": "run" | "settings" | "editor" | "dismiss", "tab": tab Thiết lập (khi action=settings),
"label": chữ trên nút}. Chỉ đọc lý do chặn đã ghi — không gọi mạng, không đổi dữ liệu.
"""
from __future__ import annotations

from .ranker import strip_accents

STOP_STATES = {"NEEDS_REVIEW", "FAILED_RETRYABLE", "FAILED_PERMANENT"}

# (từ khoá không dấu, tab Thiết lập, tên dịch vụ)
_CREDENTIALS = [
    (("anthropic", "claude", "ket noi ai", "writer"), "anthropic", "Claude (Anthropic)"),
    (("pixabay", "pexels", "kho tu lieu"), "media", "Kho tư liệu (Pixabay)"),
    (("cloudflare", "fal.ai", "anh ai"), "ai_images", "Ảnh AI"),
    (("youtube", "oauth", "kenh dich", "client_secret", "token kenh"), "youtube", "YouTube"),
    (("telegram",), "telegram", "Telegram"),
    (("vieneu", "tts", "giong doc"), "tts", "Giọng đọc"),
]


def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def next_steps(state: str, blockers: list[str] | None, last_error: str | None) -> list[dict]:
    if state not in STOP_STATES:
        return []
    raw = " | ".join([*(blockers or []), last_error or ""])
    t = strip_accents(raw).lower()
    steps: list[dict] = []

    def add(text, action, label, tab=None):
        if not any(s["action"] == action and s.get("tab") == tab for s in steps):
            steps.append({"text": text, "action": action, "label": label, **({"tab": tab} if tab else {})})

    if "blocked_by_credentials" in t or _has(t, ("thieu key", "thieu api", "chua nhap", "tu choi key", "tu choi token",
                                                  "khong nhan api key", "chua ket noi", "chua chon kenh")):
        hit = False
        for words, tab, name in _CREDENTIALS:
            if _has(t, words):
                add(f"Thiếu hoặc sai kết nối {name}. Mở Thiết lập → {name}, nhập/kiểm tra rồi bấm \"Chạy lại\".",
                    "settings", f"Mở Thiết lập → {name}", tab)
                hit = True
        if not hit:
            add("Thiếu key hoặc kết nối dịch vụ. Mở Thiết lập, xem chấm vàng/đỏ ở các tab, nhập key rồi bấm \"Chạy lại\".",
                "settings", "Mở Thiết lập", "mode")
    if "blocked_by_budget" in t or _has(t, ("ngan sach", "tran chi", "budget")):
        add("Đã chạm trần ngân sách AI. Tăng trần ở Thiết lập → Ngân sách AI, hoặc chờ sang ngày/tháng mới rồi bấm \"Chạy lại\".",
            "settings", "Mở Thiết lập → Ngân sách AI", "budget")
    if _has(t, ("chua xac minh", "tranh cai", "can xac minh", "claim trong yeu", "freshness")):
        add("Video dừng theo quy tắc xác minh tin cũ. App đã bỏ qua mọi luật xác minh — bấm \"Chạy lại\".", "run", "Chạy lại")
    if _has(t, ("script qa", "kich ban", "ghi ro nguon", "claim map", "so tu", "chep dai", "trung video")):
        add("Kịch bản chưa qua bước kiểm tra. Bấm \"Chạy lại\" để Biên kịch viết lại, hoặc mở trang Biên tập để sửa tay.",
            "editor", "Mở trang Biên tập")
    if _has(t, ("thoi luong", "duoi 10 phut", "qua dai", "audio")):
        add("Thời lượng chưa đạt (phải từ 10 phút). Bấm \"Chạy lại\" để viết lại cho đủ độ dài.", "run", "Chạy lại")
    if _has(t, ("license", "tu lieu", "asset")):
        add("Thiếu hình ảnh có giấy phép. Nhập key Pixabay (Thiết lập → Kho tư liệu) hoặc Cloudflare (Ảnh AI) rồi bấm \"Chạy lại\".",
            "settings", "Mở Thiết lập → Kho tư liệu", "media")
    if _has(t, ("mang", "timeout", "429", "500", "502", "503", "tam thoi", "retryable", "thu lai")):
        add("Lỗi mạng hoặc dịch vụ bận tạm thời. Bấm \"Chạy lại\".", "run", "Chạy lại")
    if not any(s["action"] == "run" for s in steps):
        add("Sau khi xử lý xong, bấm \"Chạy lại\" để video chạy tiếp từ chỗ đang dừng.", "run", "Chạy lại")
    add("Không muốn làm video này nữa? Bấm \"Bỏ video\" (tin sẽ không bị chọn lại).", "dismiss", "Bỏ video")
    return steps
