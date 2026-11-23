"""Chống trùng, phân loại trụ cột P1/P2/P3 và chấm điểm /100 có giải thích."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {"fbclid", "gclid", "ref", "source", "zarsrc", "mc_cid", "mc_eid"}


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/+$", "", p.path) or "/"
    return urlunparse(("https", host, path, "", urlencode(sorted(query)), ""))


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower()).replace("đ", "d")
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", strip_accents(s)) if len(t) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def article_hash(title: str, excerpt: str) -> str:
    norm = " ".join(sorted(tokens(title + " " + excerpt[:800])))
    return hashlib.sha256(norm.encode()).hexdigest()


PILLAR_KEYWORDS = {
    "P1": ["mua", "lan dau", "tong chi phi", "chung cu", "dat coc", "xe cu", "ban giao", "lua chon", "nen mua",
           "gia ban", "tra gop", "so huu", "quang duong cong bo", "di xa", "ve que"],
    "P2": ["meo", "tiet kiem", "sac day", "bao duong", "pin", "dieu hoa", "toi uu", "lo trinh", "tieu thu",
           "chu xe", "kiem tra", "su co", "huong dan", "dich vu", "chay dich vu"],
    "P3": ["tram sac", "chinh sach", "quy dinh", "thong tu", "nghi dinh", "uu dai", "mo rong", "thi truong",
           "hop tac", "mou", "doanh so", "chien luoc", "doi thu", "ha tang", "hieu luc", "v-green", "vgreen",
           "thoi han", "don gia", "mang sac", "mang luoi", "cong bo"],
}

RUMOR_MARKERS = ["tin don", "nghe noi", "ro ri", "chua xac nhan", "nghi van", "soc", "khong the tin"]


def classify_pillar(title: str, excerpt: str) -> tuple[str, dict[str, int]]:
    text = strip_accents(title + " " + excerpt)
    hits = {p: sum(1 for k in kws if k in text) for p, kws in PILLAR_KEYWORDS.items()}
    best = max(hits, key=lambda p: (hits[p], p == "P1"))
    return (best if hits[best] > 0 else "P1"), hits


@dataclass
class ScoreResult:
    total: float
    breakdown: dict[str, float]
    reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)


def score_news(*, title: str, excerpt: str, published_at: datetime | None, trust_tier: int,
               n_supporting: int, n_verified_claims: int, n_key_claims: int, n_contested: int,
               has_conditions: bool, asset_feasibility: float, novelty_similarity: float,
               flags: list[str], pillar_hits: dict[str, int], now: datetime | None = None) -> ScoreResult:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    penalties: list[str] = []
    b: dict[str, float] = {}

    # 1. Độ mới (0–20)
    if published_at:
        age_h = max(0.0, (now - published_at).total_seconds() / 3600)
        b["freshness"] = 20 if age_h <= 24 else 15 if age_h <= 72 else 10 if age_h <= 168 else 4 if age_h <= 720 else 1
        reasons.append(f"Đăng cách đây {age_h/24:.1f} ngày")
    else:
        b["freshness"] = 0
        penalties.append("Không rõ ngày đăng")

    # 2. Tác động tới 3 nhóm khán giả (0–25)
    total_hits = sum(pillar_hits.values())
    groups = sum(1 for v in pillar_hits.values() if v > 0)
    b["impact"] = min(25, 6 * groups + 2 * min(total_hits, 5))
    reasons.append(f"Liên quan {groups}/3 nhóm khán giả")

    # 3. Xác thực & chất lượng nguồn (0–25)
    tier_pts = {1: 15, 2: 10, 3: 3}.get(trust_tier, 0)
    corroboration = min(5, 2.5 * n_supporting)
    claim_pts = 5 * (n_verified_claims / n_key_claims) if n_key_claims else 0
    b["credibility"] = min(25, tier_pts + corroboration + claim_pts)
    reasons.append(f"Nguồn cấp {trust_tier}, {n_supporting} nguồn đối chiếu, {n_verified_claims}/{n_key_claims} claim trọng yếu đã xác minh")

    # 4. Khả năng giải thích khác tin gốc (0–15)
    b["explainability"] = min(15, 6 + (5 if has_conditions else 0) + (4 if len(excerpt) > 300 else 1))

    # 5. Tư liệu hợp quyền + khả thi 10 phút (0–10)
    b["asset_feasibility"] = max(0, min(10, asset_feasibility))

    # 6. Tính mới so với video đã làm (0–5)
    b["novelty"] = round(5 * (1 - min(1.0, novelty_similarity)), 2)
    if novelty_similarity > 0.6:
        penalties.append("Trùng góc với video đã làm")

    total = sum(b.values())
    text = strip_accents(title + " " + excerpt)
    if "rumor" in flags or any(m in text for m in RUMOR_MARKERS):
        total -= 30
        penalties.append("Dựa trên đồn đoán/chưa xác nhận (-30)")
    if "clickbait" in flags:
        total -= 15
        penalties.append("Tiêu đề câu kéo không có cơ sở (-15)")
    if n_contested:
        total -= 10
        penalties.append(f"{n_contested} claim đang tranh cãi [CẦN XÁC MINH] (-10)")
    return ScoreResult(total=round(max(0, min(100, total)), 1), breakdown=b, reasons=reasons, penalties=penalties)


def bucket(score: float, shortlist: int = 75, research: int = 60) -> str:
    if score >= shortlist:
        return "shortlist"
    if score >= research:
        return "research"
    return "reference"
