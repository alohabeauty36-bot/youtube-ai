"""Công tắc "Bỏ qua mọi kiểm tra" (Thiết lập → Biên tập & thời lượng): lỗi Script QA / Media QA thành cảnh báo.

Một số mục KHÔNG bỏ qua được dù công tắc bật, vì hậu quả không sửa được sau khi đăng:
- video dưới 10 phút (yêu cầu cứng của chủ kênh),
- hình/clip chưa qua kiểm tra giấy phép (gậy bản quyền),
- file video đổi sau khi QA (checksum), kịch bản rỗng (không có gì để đọc).
"""
from __future__ import annotations

from ..config import get_settings

NON_SKIPPABLE_MEDIA = ("Không dưới", "Mọi asset đã qua license gate", "Checksum khớp file render", "SHA-256 file upload")
NON_SKIPPABLE_SCRIPT = ("spoken_script không rỗng",)


def skip_on(settings=None) -> bool:
    return bool((settings or get_settings()).skip_quality_gates)


def hard_failures(checks: list[dict], kind: str) -> list[dict]:
    keep = NON_SKIPPABLE_MEDIA if kind == "media" else NON_SKIPPABLE_SCRIPT
    return [c for c in checks if not c.get("ok") and c.get("blocking", True) and str(c.get("name", "")).startswith(keep)]


def override(qa: dict, kind: str, by: str) -> dict | None:
    """Trả bản QA đã đánh dấu bỏ qua (passed=True) nếu mọi lỗi đều bỏ qua được; None nếu còn lỗi không bỏ được."""
    checks = qa.get("checks") or []
    if hard_failures(checks, kind):
        return None
    warnings = [f"{c['name']} — {c.get('detail', '')}".strip(" —") for c in checks if not c.get("ok")]
    return {**qa, "passed": True, "overridden_by": by, "warnings": warnings}
