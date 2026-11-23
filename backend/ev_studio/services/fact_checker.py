"""Bộ xác minh 2 lớp: (1) claim 1-ý có nguồn + trích dẫn khớp; (2) claim nhạy cảm cần nguồn cấp 1
hoặc ≥2 nguồn uy tín độc lập. Xung đột → contested → khoá dựng/upload."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from .ranker import strip_accents

log = logging.getLogger(__name__)

SENSITIVE_TYPES = {"price", "charging_offer", "warranty", "spec", "safety", "sales_volume", "station_count", "law",
                   "deadline"}
VOLATILE_TYPES = {"price", "charging_offer", "station_count", "deadline", "law", "sales_volume"}
NEEDS_REVIEW_MARK = "[CẦN XÁC MINH]"


@dataclass
class SourceRef:
    source_id: str
    url: str
    publisher: str
    trust_tier: int
    published_at: str | None = None
    text: str | None = None  # nội dung đã lấy được (để kiểm tra trích dẫn)

    @property
    def domain(self) -> str:
        h = (urlparse(self.url).hostname or "").lower()
        return h[4:] if h.startswith("www.") else h


def _norm(s: str) -> str:
    return " ".join(strip_accents(s).split())


def excerpt_in_text(excerpt: str | None, text: str | None) -> bool:
    if not excerpt or not text:
        return False
    return _norm(excerpt) in _norm(text)


def verify_claim(claim: dict, sources: dict[str, SourceRef]) -> tuple[str, str]:
    """Trả (status, lý do). Không bao giờ nâng 'inference' thành 'verified'."""
    if claim.get("status") == "inference":
        return "inference", "Nhận định/kịch bản — phải gắn [PHÂN TÍCH/KỊCH BẢN] khi dùng"
    refs = [sources[s] for s in claim.get("source_ids", []) if s in sources]
    if not claim.get("source_url") or not refs:
        return "unverified", "Thiếu nguồn gốc (URL/source_id)"
    # trích dẫn phải xuất hiện trong ít nhất một nguồn có văn bản
    with_text = [r for r in refs if r.text]
    if with_text and not any(excerpt_in_text(claim.get("evidence_excerpt"), r.text) for r in with_text):
        return "unverified", "Trích dẫn bằng chứng không khớp văn bản nguồn"
    sensitive = claim.get("claim_type") in SENSITIVE_TYPES
    tier1 = [r for r in refs if r.trust_tier == 1]
    good_domains = {r.publisher.lower() or r.domain for r in refs if r.trust_tier <= 2}
    if sensitive:
        if tier1:
            return "verified", f"Claim nhạy cảm có nguồn cấp 1 ({tier1[0].publisher})"
        if len(good_domains) >= 2:
            return "verified", f"Claim nhạy cảm có {len(good_domains)} nguồn uy tín độc lập"
        return "unverified", "Claim nhạy cảm cần nguồn cấp 1 hoặc ≥2 nguồn uy tín độc lập"
    if any(r.trust_tier <= 2 for r in refs):
        return "verified", "Có nguồn uy tín"
    return "unverified", "Chỉ có nguồn cộng đồng (cấp 3) — chỉ dùng làm đầu mối"


def detect_conflicts(claims: list[dict]) -> set[str]:
    """Claim cùng (claim_type, subject) nhưng khác value → tranh cãi."""
    groups: dict[tuple, list[dict]] = {}
    contested: set[str] = set()
    for c in claims:
        if c.get("subject") and c.get("value") not in (None, ""):
            groups.setdefault((c.get("claim_type"), _norm(c["subject"])), []).append(c)
        for other in c.get("conflicts_with", []) or []:
            contested.add(c["claim_key"])
            contested.add(other)
    for items in groups.values():
        vals = {_norm(str(i["value"])) for i in items}
        if len(vals) > 1:
            contested.update(i["claim_key"] for i in items)
    return contested


def check_claims(claims: list[dict], sources: dict[str, SourceRef]) -> list[dict]:
    contested = detect_conflicts(claims)
    out = []
    for c in claims:
        c = dict(c)
        if c["claim_key"] in contested:
            c["status"], c["verification_reason"] = "contested", f"{NEEDS_REVIEW_MARK} Các nguồn đưa số liệu khác nhau"
        else:
            c["status"], c["verification_reason"] = verify_claim(c, sources)
        c["sensitive"] = c.get("claim_type") in SENSITIVE_TYPES
        c["volatile"] = c.get("claim_type") in VOLATILE_TYPES
        out.append(c)
    return out


def blocking_claims(claims) -> list[str]:
    """Lý do chặn sản xuất: claim trọng yếu chưa verified (contested/unverified)."""
    reasons = []
    for c in claims:
        status, key, is_key = (c.status, c.claim_key, c.is_key) if hasattr(c, "status") else (c["status"], c["claim_key"], c.get("is_key", True))
        if status == "removed" or not is_key:
            continue
        if status == "contested":
            reasons.append(f"{NEEDS_REVIEW_MARK} claim {key} đang tranh cãi")
        elif status == "unverified":
            reasons.append(f"Claim trọng yếu {key} chưa xác minh")
    return reasons


def freshness_check(claims, client: httpx.Client | None = None, is_demo: bool = False) -> list[str]:
    """Kiểm tra lại claim dễ thay đổi trước render/upload: nguồn còn truy cập được và vẫn chứa bằng chứng."""
    problems = []
    now = datetime.now(timezone.utc)
    for c in claims:
        if c.status not in ("verified",) or not c.volatile:
            continue
        if is_demo:
            c.freshness_checked_at = now
            continue
        cl = client or httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "EVCreatorStudioBot/1.0"})
        try:
            r = cl.get(c.source_url)
            if r.status_code != 200:
                problems.append(f"Nguồn của {c.claim_key} trả HTTP {r.status_code}")
                continue
            if c.evidence_excerpt and not excerpt_in_text(c.evidence_excerpt, r.text):
                problems.append(f"Nguồn của {c.claim_key} không còn chứa bằng chứng — thông tin có thể đã thay đổi")
                continue
            c.freshness_checked_at = now
        except httpx.HTTPError as e:
            problems.append(f"Không kiểm tra được nguồn {c.claim_key}: {type(e).__name__}")
    return problems


def freshness_ok(story_checked_at: datetime | None) -> bool:
    if story_checked_at is None:
        return False
    if story_checked_at.tzinfo is None:
        story_checked_at = story_checked_at.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - story_checked_at).total_seconds() / 3600
    return age_h <= get_settings().freshness_max_age_hours
