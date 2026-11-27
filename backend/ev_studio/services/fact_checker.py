"""Bộ xác minh 2 lớp: (1) claim 1-ý có nguồn + trích dẫn khớp; (2) claim nhạy cảm cần nguồn cấp 1
hoặc ≥2 nguồn uy tín độc lập. Xung đột → contested.

Hai chế độ (Thiết lập → Biên tập & thời lượng → "Cách xử lý tin"):
- `analysis` (mặc định, chủ kênh chọn 27/11/2026): video là BÀI PHÂN TÍCH tin đã đăng, không khẳng định đó là sự thật.
  Claim có nguồn + trích dẫn khớp bài gốc nhưng chưa đủ nguồn đối chiếu → `reported` ("đã đưa tin"): được dùng khi
  kịch bản ghi rõ "theo <báo>". Tin chưa xác minh/tranh cãi KHÔNG còn chặn sản xuất.
- `strict`: như trước — claim trọng yếu chưa verified hoặc đang tranh cãi → khoá dựng/upload.
Ở cả hai chế độ, claim KHÔNG có nguồn hoặc trích dẫn không khớp văn bản nguồn vẫn bị loại (không bịa)."""
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
ATTRIBUTION_WORDS = ("theo ", "cho biet", "dua tin", "thong tin tu", "dan nguon", "trich", "bao cao cua", "cong bo",
                     "thong bao", "neu ra", "cho hay")


def analysis_mode(settings=None) -> bool:
    return (settings or get_settings()).fact_mode == "analysis"


def fact_statuses(settings=None) -> tuple[str, ...]:
    """Trạng thái claim được dùng làm dữ kiện trong kịch bản."""
    return ("verified", "reported") if analysis_mode(settings) else ("verified",)


def is_fact(status: str, settings=None) -> bool:
    return status in fact_statuses(settings)


ANALYSIS_RESEARCH_NOTE = """
CHẾ ĐỘ PHÂN TÍCH (chủ kênh bật — ưu tiên hơn quy tắc "chỉ dựa trên claim verified" ở trên)
- Video là bài PHÂN TÍCH/BÌNH LUẬN tin đã được báo chí đăng, không khẳng định tin đó là sự thật.
- Claim status `reported` = mới có báo đưa tin, chưa có nguồn thứ hai xác nhận. ĐƯỢC dùng làm dữ kiện, nhưng mọi chỗ dùng
  phải ghi nguồn ("theo <tên báo>", "<tên báo> đưa tin…"). Ghi chú điều này trong `risk_notes`.
- Claim `contested` (các nguồn đưa số khác nhau) không dùng làm dữ kiện; có thể nêu trong `risk_notes` là "các nguồn chưa thống nhất".
- KHÔNG đưa `blocking_issues` chỉ vì claim chưa được nguồn thứ hai xác nhận. Chỉ dùng `blocking_issues` khi tin gốc không có
  dữ kiện nào để phân tích.
- Vẫn tuyệt đối không bịa: không thêm sự kiện, con số, trích dẫn, tên người ngoài những gì nguồn đã đưa."""

ANALYSIS_SCRIPT_NOTE = """
CHẾ ĐỘ PHÂN TÍCH (chủ kênh bật — ưu tiên hơn các câu "chỉ dùng claim đã xác minh" ở trên)
- Đây là bài PHÂN TÍCH tin đã được báo chí đăng, không phải bản tin khẳng định sự thật. Nói rõ ở mở bài rằng video phân tích
  thông tin do báo chí đưa ra.
- Claim status `reported` (mới có một báo đưa) ĐƯỢC dùng, nhưng câu chứa nó PHẢI ghi nguồn ngay trong câu: "Theo <tên báo>, …",
  "<tên báo> đưa tin…", "<tên báo> cho biết…". `relevant_script_excerpt` trong `editorial_claim_map` phải là chính câu có ghi nguồn đó.
- Claim `verified` dùng như bình thường (vẫn nên ghi nguồn).
- Phần nhận định, dự đoán, kịch bản phải nói rõ là nhận định ("theo tôi", "có thể", "nếu… thì…"); không biến nhận định thành dữ kiện.
- Tuyệt đối không bịa: con số, trích dẫn, tên người, ngày tháng chỉ lấy từ RESEARCH_PACK. Không có thì không nói hoặc nói "chưa có số liệu".
- Claim trong PROHIBITED_OR_UNVERIFIED_CLAIMS (không có nguồn hoặc các nguồn mâu thuẫn) không dùng làm dữ kiện."""


def prompt_note(kind: str, settings=None) -> str:
    """Đoạn thêm vào prompt Nghiên cứu/Biên kịch khi bật chế độ phân tích ("" khi chế độ chặt)."""
    if not analysis_mode(settings):
        return ""
    return ANALYSIS_RESEARCH_NOTE if kind == "research" else ANALYSIS_SCRIPT_NOTE


def has_attribution(excerpt: str, publishers: list[str]) -> bool:
    """Câu có ghi nguồn: nêu tên báo/nguồn, hoặc dùng cụm dẫn nguồn ("theo…", "… cho biết", "… đưa tin")."""
    t = _norm(excerpt or "").lower()
    if any(w in t for w in ATTRIBUTION_WORDS):
        return True
    return any(p and _norm(p).lower() in t for p in publishers)


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


def verify_claim(claim: dict, sources: dict[str, SourceRef], analysis: bool | None = None) -> tuple[str, str]:
    """Trả (status, lý do). Không bao giờ nâng 'inference' thành 'verified'. Chế độ phân tích: claim có nguồn và trích dẫn
    khớp nhưng chưa đủ nguồn đối chiếu → 'reported' (dùng được khi ghi rõ nguồn)."""
    status, reason = _verify_strict(claim, sources)
    if analysis is None:
        analysis = analysis_mode()
    if analysis and status == "unverified" and reason.startswith(("Claim nhạy cảm cần", "Chỉ có nguồn cộng đồng")):
        refs = [sources[x] for x in claim.get("source_ids", []) if x in sources]
        pub = ", ".join(dict.fromkeys(r.publisher or r.domain for r in refs)) or "nguồn"
        return "reported", f"Mới có {pub} đưa tin, chưa có nguồn thứ hai xác nhận — dùng được khi ghi rõ \"theo {pub}\""
    return status, reason


def _verify_strict(claim: dict, sources: dict[str, SourceRef]) -> tuple[str, str]:
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


def blocking_claims(claims, analysis: bool | None = None) -> list[str]:
    """Lý do chặn sản xuất: claim trọng yếu chưa verified (contested/unverified). Chế độ phân tích: không chặn — claim
    chưa xác minh/tranh cãi chỉ bị loại khỏi phần dữ kiện (kịch bản không được dùng), không làm dừng video."""
    if analysis is None:
        analysis = analysis_mode()
    if analysis:
        return []
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
