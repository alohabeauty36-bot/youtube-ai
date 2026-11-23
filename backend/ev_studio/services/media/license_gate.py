"""Media licensing gate: asset chưa chứng minh quyền sử dụng → bị loại. Không có ngoại lệ "fair use" tự động."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

ALLOWED_LICENSES = {
    "cc0", "cc0 1.0", "public domain", "pd", "pdm", "pexels license",
    "cc by 2.0", "cc by 2.5", "cc by 3.0", "cc by 4.0",
    "cc by-sa 2.0", "cc by-sa 2.5", "cc by-sa 3.0", "cc by-sa 4.0",
    "owner-provided", "licensed", "internal graphic",
}
NEEDS_PROOF = {"owner-provided", "licensed"}
FORBIDDEN_MARKERS = ("nc", "nd", "noncommercial", "no derivatives", "all rights reserved", "editorial", "fair use", "unknown")


@dataclass
class GateDecision:
    status: str  # approved | rejected
    reason: str
    is_illustration: bool = False


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def evaluate(asset: dict, story_models: list[str] | None = None) -> GateDecision:
    provider = asset.get("provider")
    if provider == "web_reference":
        return GateDecision("rejected", "Chỉ là URL tham chiếu từ tìm kiếm web — không được tải/sử dụng")
    lic = (asset.get("license_name") or "").strip().lower()
    if not lic:
        return GateDecision("rejected", "Thiếu thông tin giấy phép")
    tokens = lic.replace("-", " ").split()
    if any(m in lic for m in FORBIDDEN_MARKERS[2:]) or "nc" in tokens or "nd" in tokens:
        return GateDecision("rejected", f"Giấy phép '{asset.get('license_name')}' không cho phép dùng thương mại/chỉnh sửa")
    if lic not in ALLOWED_LICENSES:
        return GateDecision("rejected", f"Giấy phép '{asset.get('license_name')}' không nằm trong danh sách cho phép")
    if asset.get("allowed_commercial") is False:
        return GateDecision("rejected", "Không cho phép dùng thương mại (kênh có thể kiếm tiền)")
    if lic in NEEDS_PROOF and not (asset.get("permission_proof") or "").strip():
        return GateDecision("rejected", "Thiếu bằng chứng quyền sử dụng (permission_proof)")
    attribution_needed = asset.get("attribution_required") or lic.startswith("cc by")
    if attribution_needed and not (asset.get("attribution_text") or "").strip():
        return GateDecision("rejected", "Giấy phép yêu cầu ghi công nhưng thiếu attribution_text")
    if provider != "internal_graphic" and not asset.get("source_url") and lic not in NEEDS_PROOF:
        return GateDecision("rejected", "Thiếu source_url gốc")
    illustration = bool(asset.get("is_ai_generated") or asset.get("is_illustration"))
    model = (asset.get("model_name") or "").strip()
    if model and story_models and model not in story_models:
        return GateDecision("approved", f"Mẫu xe {model} khác chủ đề → chỉ dùng như cảnh chung, dán nhãn 'minh họa'", True)
    if not model and story_models and provider not in ("internal_graphic",):
        illustration = True
    return GateDecision("approved", "Giấy phép hợp lệ" + (" (dán nhãn minh họa)" if illustration else ""), illustration)
