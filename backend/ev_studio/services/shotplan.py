"""Phòng Đạo diễn hình ảnh: lên danh sách cảnh quay cho từng cảnh của video.

Mỗi cảnh (3–9 giây) nhận: loại cảnh, từ khoá tiếng Anh để tìm video/ảnh trong kho tư liệu (Pexels, Wikimedia, thư viện
chủ kênh), một câu chữ nhấn mạnh ngắn hiện trên màn hình, và (nếu có) số liệu lấy từ claim ĐÃ XÁC MINH.
- Có Claude (qua bộ não phòng ban, model rẻ): một lần gọi cho cả video, kết quả được kiểm tra lại từng cảnh.
- Không có Claude / DEMO / kết quả sai: dùng từ điển chủ đề xe điện (miễn phí).
Số liệu trên màn hình KHÔNG BAO GIỜ do AI tự viết: chỉ lấy từ trường value/unit của claim đã xác minh.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from .ranker import strip_accents

log = logging.getLogger(__name__)
KINDS = ("broll", "keypoint", "question", "stat")

# (mẫu trong lời đọc đã bỏ dấu, từ khoá tiếng Anh cho kho tư liệu)
TOPICS: list[tuple[str, str]] = [
    (r"tram sac|sac nhanh|cong sac|tru sac|cay sac|sac dien|\bsac\b", "electric car charging station"),
    (r"\bpin\b|battery|kwh|ki lo oat|dung luong", "electric vehicle battery"),
    (r"xe may dien|xe ga dien|xe hai banh", "electric scooter city street"),
    (r"chung cu|bai do|ham xe|gara", "underground parking garage cars"),
    (r"nha may|san xuat|day chuyen|lap rap", "car factory assembly line"),
    (r"chinh sach|quy dinh|nghi dinh|thong tu|chinh phu|bo cong thuong|le phi|thue", "government policy documents meeting"),
    (r"thi truong|doanh so|canh tranh|hang xe|dai ly|showroom", "car dealership showroom"),
    (r"gia ban|chi phi|tien|tiet kiem|hoa don|uu dai|giam gia|tra gop|khuyen mai", "calculator money saving"),
    (r"xang|dau|cay xang|nhien lieu", "gas station fuel pump"),
    (r"khi thai|o nhiem|moi truong|carbon|xanh", "city air pollution traffic"),
    (r"bao duong|sua chua|bao hanh|ky thuat vien", "car mechanic workshop"),
    (r"luoi dien|dien nang|nang luong|mat troi|dien luc", "power grid electricity lines"),
    (r"ung dung|app|dien thoai|phan mem|cap nhat", "smartphone app car"),
    (r"duong dai|cao toc|hanh trinh|di tinh|quang duong|\bkm\b|ki lo met", "car driving highway"),
    (r"thanh pho|noi do|giao thong|ket xe|un tac|pho", "city traffic motorbikes"),
    (r"gia dinh|tre em|nguoi dung|chu xe|khach hang|nguoi mua", "family car ownership"),
    (r"tuong lai|xu huong|cong nghe|tu lai|thong minh", "futuristic electric car technology"),
    (r"an toan|tai nan|chay no|phong chay", "car safety test"),
]
GENERIC = ["electric car driving road", "electric car charging cable", "modern car interior dashboard", "aerial highway traffic",
           "city street cars night", "electric suv exterior", "hand plugging ev charger", "car driving countryside road"]


class ShotLLM(BaseModel):
    idx: int
    kind: str = "broll"
    query_en: str = ""
    overlay_vi: str = ""
    claim_id: str | None = None


class ShotPlanLLM(BaseModel):
    shots: list[ShotLLM] = Field(default_factory=list)


SYSTEM = """Bạn là ĐẠO DIỄN HÌNH ẢNH cho video YouTube bình luận tin xe điện tiếng Việt (dài, giọng đọc AI).
Với mỗi cảnh (đoạn lời đọc 3–9 giây), chọn hình ảnh minh họa có thật trong kho video/ảnh miễn phí (Pexels, Wikimedia).

Trả về DUY NHẤT JSON: {"shots": [{"idx": 0, "kind": "broll|keypoint|question|stat", "query_en": "...", "overlay_vi": "...",
"claim_id": null}]}, đủ MỌI idx được cho.
- query_en: 2–5 từ tiếng Anh, mô tả cảnh quay CỤ THỂ, quay được (vd "ev charging station night", "hand holding smartphone
  app", "car dealership showroom"). Không tên thương hiệu, không người nổi tiếng, không chữ. Cảnh liền nhau không trùng query.
- kind: broll (mặc định, chỉ hình + phụ đề) | keypoint (hiện câu nhấn mạnh) | question (câu hỏi mở cho người xem) |
  stat (chỉ khi cảnh nhắc đúng một claim có số liệu trong CLAIMS; ghi claim_id). Khoảng 1/3 số cảnh là keypoint.
- overlay_vi: 3–9 từ tiếng Việt có dấu, tóm ý chính của cảnh, KHÔNG thêm số liệu hay dữ kiện không có trong lời đọc.
Không bịa số. Không chèn lời bình ngoài nội dung lời đọc."""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def overlay_phrase(text: str, max_words: int = 8) -> str:
    sents = [x.strip() for x in re.split(r"(?<=[.!?…])\s+", text) if x.strip()]
    best = next((x for x in sents if re.search(r"\d", x)), sents[0] if sents else text)
    clause = re.split(r"[,;:—–]\s+", best)[0]
    w = clause.split()
    out = " ".join(w[:max_words]).rstrip(" ,.;:!?…")
    return out + ("…" if len(w) > max_words else "")


def heuristic_query(text: str, recent: list[str], k: int) -> str:
    t = strip_accents(text.lower())
    scored = []
    for pat, q in TOPICS:
        n = len(re.findall(pat, t))
        if n:
            scored.append((n, q))
    for _, q in sorted(scored, key=lambda x: -x[0]):
        if q not in recent[-3:]:
            return q
    for i in range(len(GENERIC)):
        q = GENERIC[(k + i) % len(GENERIC)]
        if q not in recent[-3:]:
            return q
    return GENERIC[k % len(GENERIC)]


def heuristic_plan(scenes: list[dict], stat_claims: dict[str, dict]) -> list[dict]:
    plan, recent = [], []
    for k, sc in enumerate(scenes):
        q = heuristic_query(sc["text"], recent, k)
        recent.append(q)
        t = strip_accents(sc["text"])
        claim = next((cid for cid, c in stat_claims.items() if c.get("_excerpt") and c["_excerpt"] in t), None)
        kind = "stat" if claim else "question" if sc["text"].strip().endswith("?") else "keypoint" if k % 3 == 1 else "broll"
        plan.append({"idx": sc["idx"], "kind": kind, "query_en": q, "overlay_vi": overlay_phrase(sc["text"]),
                     "claim_id": claim, "source": "heuristic"})
    return plan


def _valid_query(q: str) -> bool:
    return bool(q) and q.isascii() and 1 <= len(q.split()) <= 6 and not re.search(r"\d{3,}", q)


def validate_llm_plan(raw: dict, scenes: list[dict], stat_claims: dict[str, dict]) -> list[dict]:
    """Giữ cảnh hợp lệ từ Claude; cảnh thiếu/sai → dùng từ điển. claim_id phải là claim có số liệu đã xác minh."""
    fallback = {p["idx"]: p for p in heuristic_plan(scenes, stat_claims)}
    got = {s.idx: s for s in ShotPlanLLM.model_validate(raw).shots}
    out, recent = [], []
    for sc in scenes:
        s = got.get(sc["idx"])
        fb = fallback[sc["idx"]]
        if s is None:
            out.append(fb)
            recent.append(fb["query_en"])
            continue
        q = _clean(s.query_en).lower()
        q = q if _valid_query(q) and q not in recent[-2:] else fb["query_en"]
        kind = s.kind if s.kind in KINDS else "broll"
        claim = s.claim_id if s.claim_id in stat_claims else None
        if kind == "stat" and not claim:
            kind = "keypoint"
        ov = _clean(s.overlay_vi)
        if not ov or len(ov.split()) > 12 or re.search(r"\d", ov) and not re.search(r"\d", sc["text"]):
            ov = fb["overlay_vi"]  # không cho chữ trên màn hình có số mà lời đọc không có
        out.append({"idx": sc["idx"], "kind": kind, "query_en": q, "overlay_vi": ov, "claim_id": claim, "source": "llm"})
        recent.append(q)
    return out


def plan_shots(scenes: list[dict], *, claims: list[dict], claim_map: list[dict], title: str, pillar: str,
               brain: Any = None, story_id: str | None = None) -> list[dict]:
    """scenes: [{idx, text, start, end}]. claims: claim đã xác minh [{claim_key, text, value, unit}]."""
    excerpts = {e["claim_id"]: strip_accents(e.get("relevant_script_excerpt") or "")[:50] for e in claim_map}
    stat_claims = {c["claim_key"]: {**c, "_excerpt": excerpts.get(c["claim_key"], "")} for c in claims
                   if c.get("value") not in (None, "") and re.search(r"\d", str(c.get("value")))}
    if brain is None:
        return heuristic_plan(scenes, stat_claims)
    from ..agents.brain import Task

    user = json.dumps({"title": title, "pillar": pillar,
                       "claims": [{"claim_id": k, "text": v["text"], "value": v.get("value"), "unit": v.get("unit")}
                                  for k, v in stat_claims.items()],
                       "scenes": [{"idx": s["idx"], "seconds": round(s["end"] - s["start"], 1), "narration": s["text"]}
                                  for s in scenes]}, ensure_ascii=False)
    try:
        res, _usage = brain.ask(Task("dao_dien", SYSTEM, user, lambda raw: validate_llm_plan(raw, scenes, stat_claims),
                                     None, query=title, story_id=story_id, max_tokens=9000))
        return res
    except Exception as e:  # noqa: BLE001 — hết ngân sách / lỗi mạng / sai schema: vẫn dựng được bằng từ điển
        log.info("Phòng Đạo diễn dùng từ điển thay Claude: %s", e)
        return heuristic_plan(scenes, stat_claims)
