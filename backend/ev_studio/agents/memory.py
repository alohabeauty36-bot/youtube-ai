"""Bộ nhớ dài hạn của các phòng ban + truy xuất BM25 (thuần Python, không cần thư viện ngoài).

Bốn loại ký ức:
- answer      : câu hỏi đã gửi thầy (Claude/ChatGPT) và câu trả lời. Hỏi lại y hệt → lấy từ bộ nhớ, không tốn tiền.
- lesson      : bài học rút ra từ QA không đạt, chủ kênh sửa/yêu cầu sửa, thời lượng audio thật.
- knowledge   : dữ kiện đã xác minh có nguồn (claim verified) để các phòng tra cứu.
- style_guide : sổ tay văn phong do thầy đúc kết định kỳ từ các bài học.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..services.ranker import strip_accents

_TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("va cua la co cho cac nhung mot duoc trong khi thi de voi nay do nhu tu ve the se da dang ra vao lai cung".split())


def key_hash(*parts: str) -> str:
    return hashlib.sha256("␞".join(parts).encode("utf-8")).hexdigest()


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(strip_accents(text or "").lower()) if t not in STOP and len(t) > 1]


def flatten_text(obj: Any, limit: int = 20000) -> str:
    """Rút phần chữ trong JSON trả lời để truy xuất / so sánh."""
    out: list[str] = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(obj)
    return " ".join(out)[:limit]


def overlap_f1(a: str, b: str) -> float:
    """ROUGE-1 F1 (bỏ dấu): đo mức trò viết giống thầy về nội dung."""
    ca, cb = Counter(tokens(a)), Counter(tokens(b))
    if not ca or not cb:
        return 0.0
    common = sum((ca & cb).values())
    if not common:
        return 0.0
    p, r = common / sum(ca.values()), common / sum(cb.values())
    return round(2 * p * r / (p + r), 4)


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.n = len(docs)
        self.avg = (sum(len(d) for d in docs) / self.n) if self.n else 0
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i]) or 1
            sc = 0.0
            for q in query:
                f = tf.get(q)
                if f:
                    sc += self.idf.get(q, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
            out.append(sc)
        return out


# ------------------------------------------------------------------ ghi / đọc


def remember(db: Session, department: str, kind: str, *, key: str, text: str, response: Any = None, request: str | None = None,
             source: str = "system", model: str | None = None, quality: float = 0.0, approved: bool = False,
             story_id: str | None = None, dedupe: bool = False):
    from ..models import AgentMemory

    if dedupe:
        old = db.scalar(select(AgentMemory).where(AgentMemory.department == department, AgentMemory.kind == kind,
                                                  AgentMemory.key_hash == key).limit(1))
        if old is not None:
            old.uses += 1
            return old
    m = AgentMemory(department=department, kind=kind, key_hash=key, source=source, model=model, request=request,
                    response=response if response is not None else {}, text=text[:20000], quality=quality,
                    approved=approved, story_id=story_id)
    db.add(m)
    db.flush()
    return m


def recall_exact(db: Session, department: str, key: str):
    from ..models import AgentMemory

    return db.scalar(select(AgentMemory).where(AgentMemory.department == department, AgentMemory.kind == "answer",
                                               AgentMemory.key_hash == key, AgentMemory.quality >= 0)
                     .order_by(AgentMemory.approved.desc(), AgentMemory.id.desc()).limit(1))


def search(db: Session, department: str | Iterable[str], query: str, kinds: Iterable[str], k: int = 3,
           approved_only: bool = False, pool: int = 1500) -> list:
    from ..models import AgentMemory

    depts = [department] if isinstance(department, str) else list(department)
    q = select(AgentMemory).where(AgentMemory.department.in_(depts), AgentMemory.kind.in_(list(kinds)),
                                  AgentMemory.quality >= 0)
    if approved_only:
        q = q.where(AgentMemory.approved.is_(True))
    rows = list(db.scalars(q.order_by(AgentMemory.id.desc()).limit(pool)))
    if not rows:
        return []
    qt = tokens(query)
    if not qt:
        return rows[:k]
    bm = BM25([tokens(r.text) for r in rows])
    sc = bm.scores(qt)
    ranked = sorted(zip(sc, rows), key=lambda x: (x[0] + (1.0 if x[1].approved else 0) + 0.3 * x[1].quality), reverse=True)
    return [r for s, r in ranked[:k] if s > 0]


def lessons(db: Session, department: str, limit: int = 8) -> list[str]:
    from ..models import AgentMemory

    rows = db.scalars(select(AgentMemory.text).where(AgentMemory.department == department, AgentMemory.kind == "lesson")
                      .order_by(AgentMemory.uses.desc(), AgentMemory.id.desc()).limit(limit))
    return list(rows)


def style_guide(db: Session, department: str) -> str | None:
    from ..models import AgentMemory

    return db.scalar(select(AgentMemory.text).where(AgentMemory.department == department, AgentMemory.kind == "style_guide")
                     .order_by(AgentMemory.id.desc()).limit(1))


def add_lesson(db: Session, department: str, text: str, source: str = "system", story_id: str | None = None):
    text = re.sub(r"\s+", " ", text).strip()[:600]
    if not text:
        return None
    return remember(db, department, "lesson", key=key_hash(department, "lesson", strip_accents(text).lower()), text=text,
                    source=source, story_id=story_id, dedupe=True)


def dumps(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)
