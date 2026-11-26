"""Tự công khai có hẹn giờ — điểm DUY NHẤT trong ứng dụng đặt một video thành công khai.

Chỉ chạy khi TẤT CẢ đều đúng (kiểm tra ở workers/autoops.py trước khi gọi `publish_video`):
- chủ kênh bật "Tự động hoàn toàn" và thời gian chờ tự công khai > 0;
- video đã tải lên RIÊNG TƯ, YouTube đọc lại là private, Media QA đạt;
- Telegram đã báo chủ kênh kèm nút "Dừng tự công khai", đã hết thời gian chờ và chủ kênh không bấm Dừng;
- kiểm chứng lại claim ngay trước khi công khai không phát hiện claim trọng yếu bị tranh cãi.
Upload vẫn luôn là private (privacy.py). Hàm này không đặt publishAt và không đổi các trường status khác.
"""
from __future__ import annotations

from typing import Any

from ...errors import PrivacyViolation
from .privacy import PRIVATE

PUBLIC = "public"


class LockedPrivate(Exception):
    """YouTube giữ video ở chế độ riêng tư dù app yêu cầu công khai — thường do project API chưa qua audit."""


def publish_status(current: dict[str, Any]) -> dict[str, Any]:
    """Giữ nguyên mọi trường status hiện có (khai báo nội dung tổng hợp, dành cho trẻ em, license…), chỉ đổi quyền xem."""
    if not current:
        raise PrivacyViolation("Không đọc được trạng thái video — không công khai")
    if current.get("privacyStatus") != PRIVATE:
        raise PrivacyViolation(f"Chỉ công khai video đang riêng tư (hiện: {current.get('privacyStatus')!r})")
    read_only = {"uploadStatus", "failureReason", "rejectionReason", "publishAt"}
    st = {k: v for k, v in current.items() if k not in read_only}
    st["privacyStatus"] = PUBLIC
    return st


def publish_video(client, video_id: str) -> str:
    """Đổi video sang công khai rồi ĐỌC LẠI. Trả về trạng thái đọc được; YouTube giữ private → LockedPrivate."""
    cur = client.get_status(video_id)
    client.update_status(video_id, publish_status(cur or {}))
    after = client.get_privacy(video_id)
    if after != PUBLIC:
        raise LockedPrivate(after or "không rõ")
    return after
