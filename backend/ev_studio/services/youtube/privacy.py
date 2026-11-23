"""Bất biến an toàn: mọi upload là PRIVATE. Module này là điểm DUY NHẤT tạo body cho videos.insert.

Không có hàm nào trong ứng dụng đặt privacyStatus khác 'private', không có publishAt, không có lịch công khai.
"""
from __future__ import annotations

from typing import Any

from ...errors import PrivacyViolation

PRIVATE = "private"
ALLOWED_PRIVACY = frozenset({PRIVATE})
FORBIDDEN_STATUS_KEYS = {"publishAt"}


def assert_private_body(body: dict[str, Any]) -> None:
    status = body.get("status") or {}
    if status.get("privacyStatus") not in ALLOWED_PRIVACY:
        raise PrivacyViolation(f"Từ chối upload: privacyStatus={status.get('privacyStatus')!r} (chỉ cho phép 'private')")
    bad = FORBIDDEN_STATUS_KEYS & set(status)
    if bad:
        raise PrivacyViolation(f"Từ chối upload: không được đặt {sorted(bad)} (lịch công khai)")


def build_insert_body(*, title: str, description: str, tags: list[str], category_id: str,
                      made_for_kids_confirmed_false: bool, contains_synthetic_media: bool,
                      default_language: str = "vi") -> dict[str, Any]:
    status: dict[str, Any] = {
        "privacyStatus": PRIVATE,
        "embeddable": True,
        "license": "youtube",
        "containsSyntheticMedia": bool(contains_synthetic_media),
    }
    if made_for_kids_confirmed_false:  # chỉ khai báo khi chủ kênh đã xác nhận
        status["selfDeclaredMadeForKids"] = False
    body = {
        "snippet": {
            "title": title[:100], "description": description[:5000], "tags": tags,
            "categoryId": category_id, "defaultLanguage": default_language, "defaultAudioLanguage": default_language,
        },
        "status": status,
    }
    assert_private_body(body)
    return body
