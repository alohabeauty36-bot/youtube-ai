"""Hồ sơ tư liệu: gom kết quả tìm kiếm, chia đoạn, chọn những đoạn liên quan nhất (BM25), đánh số nguồn S1, S2…

Cả thầy (Claude/ChatGPT) lẫn trò (model trên máy) nhận CÙNG một hồ sơ → so sánh công bằng, và trò học đúng kỹ năng
"tổng hợp tư liệu thành bài luận" chứ không học thuộc dữ kiện của thầy.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from ...agents.memory import BM25, tokens

log = logging.getLogger(__name__)
CHUNK_CHARS = 750
MAX_PASSAGES_PER_SOURCE = 4
MAX_PER_DOMAIN = 3


@dataclass
class Source:
    id: str
    title: str
    url: str
    publisher: str = ""
    provider: str = ""
    license: str | None = None
    passages: list[str] = field(default_factory=list)
    demo: bool = False


@dataclass
class Dossier:
    topic: str
    queries: list[str]
    sources: list[Source]
    stats: dict = field(default_factory=dict)

    @property
    def is_demo(self) -> bool:
        return any(s.demo for s in self.sources)

    def ids(self) -> set[str]:
        return {s.id for s in self.sources}

    def source_text(self, sid: str) -> str:
        for s in self.sources:
            if s.id == sid:
                return " ".join([s.title] + s.passages)
        return ""

    def all_text(self) -> str:
        return " ".join(self.source_text(s.id) for s in self.sources)

    def to_prompt(self) -> str:
        blocks = []
        for s in self.sources:
            head = f"[{s.id}] {s.title} — {s.publisher or urlparse(s.url).hostname} ({s.url})"
            blocks.append(head + "\n" + "\n".join(f"- {p}" for p in s.passages))
        return "\n\n".join(blocks)

    def to_dict(self) -> dict:
        return {"topic": self.topic, "queries": self.queries, "sources": [asdict(s) for s in self.sources], "stats": self.stats}

    @classmethod
    def from_dict(cls, d: dict) -> "Dossier":
        return cls(d.get("topic", ""), list(d.get("queries", [])), [Source(**s) for s in d.get("sources", [])], d.get("stats", {}))


# ------------------------------------------------------------------ lập kế hoạch tìm kiếm


_ANGLES = {
    "vi": ["{t}", "{t} tổng quan", "{t} số liệu thống kê", "{t} ưu điểm nhược điểm", "{t} tranh luận phản biện",
           "{t} tác động xu hướng"],
    "en": ["{t}", "{t} overview", "{t} statistics data", "{t} advantages disadvantages", "{t} criticism debate",
           "{t} impact trends"],
}

PLAN_SYSTEM = """Bạn lập kế hoạch tìm kiếm cho một bài luận. Đọc CHỦ ĐỀ và YÊU CẦU, đề xuất 4–6 truy vấn tìm kiếm ngắn
(2–8 từ), mỗi truy vấn nhắm một khía cạnh khác nhau: định nghĩa/bối cảnh, số liệu, lập luận ủng hộ, phản biện, ví dụ thực tế.
Chỉ trả JSON: {"queries": ["...", "..."]}"""


_FRAMING_VI = re.compile(
    r"^(hãy\s+)?(viết\s+)?(một\s+)?(bài\s+(nghị\s+luận|luận|viết)\s+)?((nghị\s+luận|bàn|suy\s+nghĩ|trình\s+bày|phân\s+tích)\s+)?(về\s+)?"
    r"(liệu\s+)?(có\s+nên\s+|tại\s+sao\s+|vì\s+sao\s+)?", re.I)
_FRAMING_EN = re.compile(r"^(write\s+an?\s+essay\s+(on|about)\s+)?(should\s+(we|you|people)\s+|why\s+(do|does|is|are)\s+)?", re.I)
_TAIL = re.compile(r"\s*(hay\s+không|như\s+thế\s+nào|thế\s+nào|không|or\s+not)?\s*[?.!]*\s*$", re.I)


def core_phrase(topic: str) -> str:
    """'Có nên mua xe điện ở Việt Nam không?' → 'mua xe điện ở Việt Nam' (truy vấn tìm kiếm gọn hơn)."""
    t = re.sub(r"\s+", " ", topic).strip()
    c = _TAIL.sub("", _FRAMING_EN.sub("", _FRAMING_VI.sub("", t, count=1), count=1)).strip(" ,:;-")
    return c if len(c.split()) >= 2 else t.rstrip("?.! ")


def plan_queries(topic: str, requirements: str = "", language: str = "vi", planner: Any = None, n: int = 6) -> list[str]:
    """Truy vấn tìm kiếm. Có model trên máy (miễn phí) thì nhờ nó gợi ý thêm; không có thì dùng khuôn cố định."""
    topic = re.sub(r"\s+", " ", topic).strip()
    lang = "en" if language.startswith("en") else "vi"
    core = core_phrase(topic)
    base = [a.format(t=core) for a in _ANGLES[lang]]
    extra: list[str] = []
    if planner is not None:
        try:
            raw, _ = planner.complete_json(PLAN_SYSTEM, f"CHỦ ĐỀ: {topic}\nYÊU CẦU: {requirements or '(không)'}", 400)
            extra = [str(q).strip() for q in (raw or {}).get("queries", []) if 2 <= len(str(q).split()) <= 12]
        except Exception:  # noqa: BLE001 - lập kế hoạch hỏng thì dùng khuôn cố định
            log.info("Model trên máy không lập được kế hoạch tìm kiếm, dùng khuôn mặc định")
    out, seen = [], set()
    for q in [base[0]] + extra + base[1:]:
        k = " ".join(tokens(q))
        if k and k not in seen:
            seen.add(k)
            out.append(q)
    return out[:n]


# ------------------------------------------------------------------ chia đoạn + chọn lọc


def chunk(text: str, size: int = CHUNK_CHARS) -> list[str]:
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n+", text or "")]
    paras = [p for p in paras if len(p.split()) >= 8 and not re.fullmatch(r"=+.*=+", p)]
    out: list[str] = []
    cur = ""
    for p in paras:
        if len(p) > size:  # đoạn dài: cắt ở ranh giới câu
            if cur:
                out.append(cur)
                cur = ""
            sent = re.split(r"(?<=[.!?…])\s+", p)
            buf = ""
            for s_ in sent:
                if len(buf) + len(s_) > size and buf:
                    out.append(buf.strip())
                    buf = ""
                buf += s_ + " "
            if buf.strip():
                out.append(buf.strip()[: size * 2])
        elif len(cur) + len(p) + 1 <= size:
            cur = f"{cur} {p}".strip()
        else:
            out.append(cur)
            cur = p
    if cur:
        out.append(cur)
    return out


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def build_dossier(topic: str, requirements: str, queries: list[str], searchers: list, fetcher: Any = None,
                  max_sources: int = 8, max_chars: int = 14_000, max_fetch: int = 10, per_query: int = 4) -> Dossier:
    hits, seen_url = [], set()
    for q in queries:
        for s in searchers:
            try:
                for h in s.search(q, per_query):
                    u = h.url.split("#")[0].rstrip("/")
                    if u and u not in seen_url:
                        seen_url.add(u)
                        hits.append(h)
            except Exception:  # noqa: BLE001 - một nguồn tìm kiếm hỏng không làm hỏng cả bài
                log.warning("Tìm kiếm %s lỗi với '%s'", getattr(s, "name", "?"), q, exc_info=True)
    fetched = failed = 0
    docs: list[tuple[Any, str]] = []
    for h in hits:
        text = h.text
        if text is None:
            if fetcher is None or fetched >= max_fetch:
                text = h.snippet
            else:
                fetched += 1
                got = fetcher.fetch(h.url)
                if got is None:
                    failed += 1
                    text = h.snippet
                else:
                    title, text = got
                    h.title = h.title or title
        if text and len(text.split()) >= 30:
            docs.append((h, text))

    # xếp hạng mọi đoạn theo chủ đề (nhân đôi trọng số) + yêu cầu + truy vấn
    chunks: list[tuple[int, int, str]] = []  # (chỉ số tài liệu, vị trí, đoạn)
    for di, (_, text) in enumerate(docs):
        for pi, c in enumerate(chunk(text)):
            chunks.append((di, pi, c))
    stats = {"hits": len(hits), "fetched": fetched, "fetch_failed": failed, "documents": len(docs), "chunks": len(chunks)}
    if not chunks:
        return Dossier(topic, queries, [], stats)
    q_tokens = tokens(topic) * 2 + tokens(requirements) + [t for q in queries for t in tokens(q)]
    ctoks = [tokens(c) for _, _, c in chunks]
    scores = BM25(ctoks).scores(q_tokens)
    order = sorted(range(len(chunks)), key=lambda i: -scores[i])
    picked: dict[int, list[tuple[int, str]]] = {}
    picked_tok: list[set] = []
    per_domain: dict[str, int] = {}
    total = 0
    for i in order:
        if scores[i] <= 0 or total >= max_chars:
            break
        di, pi, c = chunks[i]
        if di not in picked:
            if len(picked) >= max_sources:
                continue
            dom = urlparse(docs[di][0].url).hostname or ""
            if per_domain.get(dom, 0) >= MAX_PER_DOMAIN and docs[di][0].provider != "fixture":
                continue
        elif len(picked[di]) >= MAX_PASSAGES_PER_SOURCE:
            continue
        ts = set(ctoks[i])
        if any(_jaccard(ts, o) > 0.6 for o in picked_tok):
            continue  # gần như trùng đoạn đã chọn (nhiều báo đăng lại cùng thông cáo)
        if di not in picked:
            dom = urlparse(docs[di][0].url).hostname or ""
            per_domain[dom] = per_domain.get(dom, 0) + 1
            picked[di] = []
        picked[di].append((pi, c))
        picked_tok.append(ts)
        total += len(c)
    sources = []
    for n, (di, items) in enumerate(picked.items(), start=1):
        h = docs[di][0]
        sources.append(Source(id=f"S{n}", title=h.title or h.url, url=h.url, publisher=h.publisher, provider=h.provider,
                              license=h.license, passages=[c for _, c in sorted(items)], demo=bool(h.meta.get("demo"))))
    stats["chars"] = total
    return Dossier(topic, queries, sources, stats)
