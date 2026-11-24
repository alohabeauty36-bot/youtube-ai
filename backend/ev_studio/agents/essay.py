"""Phòng Bài luận: research web miễn phí → viết bài luận theo chủ đề, có trích nguồn [S1], [S2]…

Quy trình một bài:
1. LẬP KẾ HOẠCH: tách chủ đề thành 4–6 truy vấn (model trên máy gợi ý nếu có, không thì dùng khuôn) — $0.
2. RESEARCH: Wikipedia / SearXNG / Brave → tải trang (tôn trọng robots.txt) → chọn đoạn liên quan → HỒ SƠ TƯ LIỆU — $0.
3. VIẾT qua bộ não phòng ban: bộ nhớ → trò (nếu đã tốt nghiệp) → thầy Claude hoặc ChatGPT (luân phiên, có trần ngân sách).
   Thầy và trò nhận CÙNG hồ sơ tư liệu. Mỗi bài của thầy, trò viết thử cùng đề và được chấm → học dần.
4. QA (không dùng AI): cấu trúc, độ dài, mọi trích dẫn trỏ đúng nguồn, đoạn văn bám sát nguồn, mọi con số đều có trong tư liệu.

Cấp Bậc thầy: phòng tự viết từ đầu đến cuối bằng model trên máy, không gọi Claude/ChatGPT
(trừ khi bài của trò không qua QA và phòng không đặt chế độ "chỉ trò tự làm").
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from ..config import Settings, get_settings
from ..errors import BlockedByCredentials, RetryableError
from ..services.research import Dossier, PageFetcher, build_dossier, build_searchers, plan_queries
from . import memory
from .brain import Brain, Task

log = logging.getLogger(__name__)

STYLES = {
    "nghi_luan": ("Nghị luận", "Nêu luận điểm rõ ở mở bài; mỗi phần thân bài là một luận cứ có dẫn chứng từ tư liệu; "
                               "BẮT BUỘC có một phần trình bày ý kiến trái chiều và phản bác/cân nhắc nó; kết luận khẳng định lại luận điểm."),
    "phan_tich": ("Phân tích", "Chia vấn đề thành các yếu tố (nguyên nhân, cơ chế, tác động…); mỗi phần phân tích một yếu tố có dẫn chứng; "
                               "chỉ ra mối liên hệ giữa các yếu tố; kết luận tổng hợp."),
    "thuyet_minh": ("Thuyết minh", "Giải thích khách quan: khái niệm, cách hoạt động, phân loại, ví dụ; giọng trung lập, không bày tỏ quan điểm cá nhân."),
    "so_sanh": ("So sánh", "Xác định tiêu chí so sánh; mỗi phần một tiêu chí, nêu điểm giống và khác có dẫn chứng; kết luận ai phù hợp với lựa chọn nào."),
}

ESSAY_SYSTEM = """Bạn là cây bút bài luận hàng đầu, viết cho độc giả phổ thông thông minh.
Nhiệm vụ: viết MỘT bài luận hoàn chỉnh theo CHỦ ĐỀ và YÊU CẦU, CHỈ dựa trên HỒ SƠ TƯ LIỆU được cung cấp.

QUY TẮC BẮT BUỘC
1. Mọi dữ kiện, số liệu, ví dụ đều phải lấy từ HỒ SƠ TƯ LIỆU. Không dùng kiến thức ngoài hồ sơ, không bịa số, không bịa nguồn.
   Con số chép NGUYÊN VĂN như trong tư liệu. Tư liệu không có thì không nêu, hoặc nói rõ là chưa có số liệu.
2. Trích nguồn bằng mã trong ngoặc vuông ngay sau câu chứa dữ kiện: [S1] hoặc [S2][S4]. Chỉ dùng mã có trong hồ sơ.
   Mỗi đoạn thân bài phải có ít nhất một trích dẫn. Dùng ít nhất 3 nguồn khác nhau nếu hồ sơ có từ 3 nguồn trở lên.
3. Diễn đạt lại bằng lời của mình; không chép nguyên câu dài từ tư liệu.
4. Cấu trúc Markdown: đoạn mở bài (không có tiêu đề), sau đó các phần thân bài, mỗi phần mở đầu bằng "## Tiêu đề phần",
   cuối cùng là "## Kết luận". Không dùng gạch đầu dòng trong thân bài, viết thành đoạn văn liền mạch.
5. Độ dài sát số từ yêu cầu (lệch tối đa 15%). Viết đúng ngôn ngữ yêu cầu.
6. Không viết lời dẫn kiểu "Dưới đây là bài luận", không nhắc mình là AI, không chèn đường link vào thân bài.

Trả về DUY NHẤT một JSON object:
{"title": "tiêu đề bài luận", "thesis": "luận điểm chính, 1–2 câu",
 "outline": ["Mở bài: …", "Phần 1: …", "…", "Kết luận: …"],
 "essay_markdown": "toàn văn bài luận theo cấu trúc ở quy tắc 4",
 "sources_used": ["S1", "S3"]}"""

CITE = re.compile(r"\[(S\d+(?:\s*[,;]\s*S?\d+)*)\]")
NUM = re.compile(r"\d+(?:[.,]\d+)*")
META = re.compile(r"(dưới đây là|sau đây là bài|là một (mô hình ngôn ngữ|trí tuệ nhân tạo|AI)|tôi là (AI|trí tuệ nhân tạo)|"
                  r"as an ai|here is (the|an|your) essay|language model)", re.I)
URL = re.compile(r"https?://\S+")
COUNTER = re.compile(r"(tuy nhiên|mặt khác|ngược lại|phản biện|trái chiều|phản đối|người hoài nghi|however|on the other hand|"
                     r"critics|opponents|counterargument)", re.I)


@dataclass
class EssayRequest:
    topic: str
    requirements: str = ""
    words: int = 1200
    style: str = "nghi_luan"
    language: str = "vi"

    def user_message(self, dossier: Dossier, today: str) -> str:
        style_name, style_rule = STYLES.get(self.style, STYLES["nghi_luan"])
        lang = "tiếng Việt" if self.language.startswith("vi") else "English"
        return (f"CHỦ ĐỀ: {self.topic}\nYÊU CẦU CỦA CHỦ KÊNH: {self.requirements or '(không có thêm)'}\n"
                f"KIỂU BÀI: {style_name} — {style_rule}\nĐỘ DÀI: khoảng {self.words} từ\nNGÔN NGỮ: {lang}\nNGÀY HIỆN TẠI: {today}\n\n"
                f"HỒ SƠ TƯ LIỆU ({len(dossier.sources)} nguồn):\n{dossier.to_prompt()}")


def max_tokens_for(words: int) -> int:
    return int(min(12000, max(2500, words * 3.2 + 1200)))


# ------------------------------------------------------------------ chuẩn hoá đầu ra


def validate_essay(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise RetryableError("bài luận phải là JSON object")
    md = str(raw.get("essay_markdown") or raw.get("essay") or raw.get("content") or "").strip()
    title = str(raw.get("title") or "").strip()
    if len(md) < 300:
        raise RetryableError("essay_markdown rỗng hoặc quá ngắn")
    if not title:
        m = re.match(r"#\s+(.+)\n", md)
        if not m:
            raise RetryableError("thiếu title")
        title = m.group(1).strip()
    md = re.sub(r"^#\s+.+\n+", "", md) if md.startswith("# ") else md  # tiêu đề đã có ở trường title
    md = re.sub(r"\[S(\d+)\s*[,;]\s*S?(\d+)\]", r"[S\1][S\2]", md)  # [S1, S2] → [S1][S2]
    outline = raw.get("outline") or []
    used = raw.get("sources_used") or sorted(set(cited_ids(md)), key=lambda x: int(x[1:]))
    return {"title": title[:300], "thesis": str(raw.get("thesis") or "").strip()[:800],
            "outline": [str(x)[:300] for x in outline][:20] if isinstance(outline, list) else [],
            "essay_markdown": md, "sources_used": [str(x) for x in used][:30]}


def cited_ids(text: str) -> list[str]:
    out = []
    for m in CITE.finditer(text):
        for part in re.split(r"\s*[,;]\s*", m.group(1)):
            out.append(part if part.startswith("S") else "S" + part)
    return out


# ------------------------------------------------------------------ QA (không dùng AI)


def _blocks(md: str) -> list[tuple[str, str]]:
    out = []
    for b in re.split(r"\n\s*\n", md.strip()):
        b = b.strip()
        if not b:
            continue
        if re.match(r"#{2,4}\s", b):
            first, _, rest = b.partition("\n")
            out.append(("h", first.lstrip("# ").strip()))
            if rest.strip():
                out.append(("p", rest.strip()))
        else:
            out.append(("p", b))
    return out


def _plain(text: str) -> str:
    return CITE.sub("", text)


def _numbers(text: str) -> set[str]:
    out = set()
    for n in NUM.findall(text):
        d = re.sub(r"[.,]", "", n)
        if len(d) >= 2:  # bỏ số 1 chữ số (thứ tự, "ba lý do"…)
            out.add(d.lstrip("0") or "0")
    return out


def _has_diacritics(w: str) -> bool:
    return any(unicodedata.combining(c) for c in unicodedata.normalize("NFD", w)) or "đ" in w.lower()


def profile(md: str) -> dict:
    """Hồ sơ phong cách của một bài (để so sánh cách viết của Claude, ChatGPT và trò)."""
    blocks = _blocks(md)
    paras = [_plain(t) for k, t in blocks if k == "p"]
    words = sum(len(p.split()) for p in paras)
    sents = [s for p in paras for s in re.split(r"(?<=[.!?…])\s+", p) if s.strip()]
    cites = len(cited_ids(md))
    return {
        "words": words, "sections": sum(1 for k, _ in blocks if k == "h"), "paragraphs": len(paras),
        "words_per_paragraph": round(words / len(paras), 1) if paras else 0,
        "words_per_sentence": round(words / len(sents), 1) if sents else 0,
        "citations_per_100w": round(cites * 100 / words, 2) if words else 0,
        "distinct_sources": len(set(cited_ids(md))), "counter_markers": len(COUNTER.findall(md)),
        "questions": sum(p.count("?") for p in paras),
    }


def analyze(essay: dict, dossier: Dossier, req: EssayRequest) -> dict:
    """Chấm bài luận. Trả {ok, problems (chặn), warnings, metrics}. Dùng chung cho thầy, trò và bản DEMO."""
    md = essay["essay_markdown"]
    blocks = _blocks(md)
    paras = [t for k, t in blocks if k == "p"]
    prof = profile(md)
    problems: list[str] = []
    warnings: list[str] = []
    ids = dossier.ids()

    lo, hi = int(req.words * 0.8), int(req.words * 1.25)
    if not lo <= prof["words"] <= hi:
        problems.append(f"độ dài {prof['words']} từ, cần {lo}–{hi}")
    need_sections = 3 if req.words >= 700 else 2
    if prof["sections"] < need_sections:
        problems.append(f"chỉ có {prof['sections']} phần có tiêu đề '##', cần ít nhất {need_sections} (gồm Kết luận)")

    cited = cited_ids(md)
    unknown = sorted(set(cited) - ids)
    if unknown:
        problems.append(f"trích dẫn nguồn không có trong hồ sơ: {unknown[:5]}")
    need_src = min(3, len(ids))
    if len(set(cited) & ids) < need_src:
        problems.append(f"chỉ dùng {len(set(cited) & ids)} nguồn, cần ít nhất {need_src}")

    body = paras[1:-1] if len(paras) > 2 else paras
    body = [p for p in body if len(p.split()) >= 35]
    uncited = [p for p in body if not CITE.search(p)]
    if body and len(uncited) / len(body) > 0.34:
        problems.append(f"{len(uncited)}/{len(body)} đoạn thân bài không có trích dẫn")

    # bám nguồn: tỷ lệ từ nội dung của đoạn có mặt trong các nguồn được trích
    support = []
    weak = 0
    src_tok = {sid: set(memory.tokens(dossier.source_text(sid))) for sid in ids}
    for p in paras:
        sids = [x for x in cited_ids(p) if x in ids]
        if not sids:
            continue
        pt = set(memory.tokens(_plain(p)))
        if len(pt) < 8:
            continue
        pool = set().union(*(src_tok[x] for x in sids))
        sc = len(pt & pool) / len(pt)
        support.append(sc)
        weak += sc < 0.3
    grounding = round(mean(support), 3) if support else 0.0
    if support and grounding < 0.45:
        problems.append(f"nội dung bám nguồn yếu ({grounding:.0%}, cần ≥ 45%): có thể đã dùng dữ kiện ngoài tư liệu")
    elif weak:
        warnings.append(f"{weak} đoạn bám nguồn yếu (< 30%)")

    allowed = _numbers(dossier.all_text()) | _numbers(req.topic + " " + req.requirements) | {str(datetime.now().year)}
    bad_nums = sorted(_numbers(_plain(md)) - allowed)
    if bad_nums:
        problems.append(f"số liệu không có trong tư liệu: {bad_nums[:6]}")

    if META.search(md):
        problems.append("có lời dẫn/meta (\"dưới đây là…\", nhắc mình là AI)")
    urls = [u.rstrip(").,") for u in URL.findall(md)]
    if urls:
        problems.append("chèn đường link vào thân bài (dùng mã nguồn [S1] thay vì URL)")

    if req.language.startswith("vi"):
        ws = re.findall(r"[^\W\d_]+", _plain(md))
        ratio = sum(_has_diacritics(w) for w in ws) / len(ws) if ws else 0
        if ratio < 0.15:
            problems.append("bài không viết bằng tiếng Việt có dấu")
    if req.style == "nghi_luan" and not prof["counter_markers"]:
        warnings.append("bài nghị luận chưa có phần ý kiến trái chiều")
    if dossier.is_demo:
        warnings.append("hồ sơ tư liệu là dữ liệu GIẢ LẬP (DEMO)")
    metrics = {**prof, "grounding": grounding, "sources_available": len(ids)}
    return {"ok": not problems, "problems": problems, "warnings": warnings, "metrics": metrics}


# ------------------------------------------------------------------ học phong cách của từng thầy


_STYLE_KEYS = ("words", "sections", "paragraphs", "words_per_paragraph", "words_per_sentence", "citations_per_100w",
               "distinct_sources", "counter_markers")


def teacher_styles(db, limit: int = 60) -> dict[str, dict]:
    """Trung bình hồ sơ phong cách theo người viết: anthropic (Claude), openai (ChatGPT), local (trò), owner (chủ kênh sửa)."""
    from sqlalchemy import select

    from ..models import AgentMemory as M

    rows = db.scalars(select(M).where(M.department == "bai_luan", M.kind == "answer", M.quality >= 0.5)
                      .order_by(M.id.desc()).limit(limit))
    acc: dict[str, list[dict]] = {}
    for r in rows:
        md = (r.response or {}).get("essay_markdown") if isinstance(r.response, dict) else None
        if md:
            acc.setdefault(r.source, []).append(profile(md))
    out = {}
    for who, ps in acc.items():
        out[who] = {"essays": len(ps), **{k: round(mean(p[k] for p in ps), 2) for k in _STYLE_KEYS}}
    return out


def style_targets(db) -> str:
    """Mục tiêu cấu trúc cho trò = trung bình các bài của thầy (cả Claude lẫn ChatGPT) và bản chủ kênh đã duyệt."""
    st = teacher_styles(db)
    ref = [v for k, v in st.items() if k in ("anthropic", "openai", "owner")]
    if not ref:
        return ""
    n = sum(v["essays"] for v in ref)
    avg = {k: sum(v[k] * v["essays"] for v in ref) / n for k in _STYLE_KEYS}
    return ("\n\n## MỤC TIÊU CẤU TRÚC (rút ra từ " + str(n) + " bài của thầy)\n"
            f"- Khoảng {avg['sections']:.0f} phần có tiêu đề '##', {avg['paragraphs']:.0f} đoạn, mỗi đoạn ~{avg['words_per_paragraph']:.0f} từ.\n"
            f"- Câu dài trung bình ~{avg['words_per_sentence']:.0f} từ. Khoảng {avg['citations_per_100w']:.1f} trích dẫn [S…] mỗi 100 từ, "
            f"dùng ~{avg['distinct_sources']:.0f} nguồn khác nhau.\n"
            + ("- Có phần phản biện/ý kiến trái chiều.\n" if avg["counter_markers"] >= 1 else ""))


# ------------------------------------------------------------------ bản nháp DEMO (không có AI)


def demo_draft(req: EssayRequest, dossier: Dossier) -> dict:
    """Ghép trích đoạn từ hồ sơ tư liệu thành bản nháp có cấu trúc. Chỉ để chạy thử khi chưa có thầy lẫn trò."""
    parts = [f"Bài viết này tổng hợp {len(dossier.sources)} nguồn tư liệu về chủ đề “{req.topic}”. "
             "Đây là BẢN NHÁP DEMO ghép từ trích đoạn, chưa do AI viết lại."]
    for s in dossier.sources:
        head = re.sub(r"^\[DEMO\]\s*", "", s.title)
        text = " ".join(s.passages)
        sents = re.split(r"(?<=[.!?…])\s+", text)
        parts.append(f"## {head}\n\n" + " ".join(sents[:4]) + f" [{s.id}]")
    parts.append("## Kết luận\n\nCác nguồn trên cho thấy nhiều góc nhìn khác nhau về chủ đề. Cần một người viết tổng hợp lại "
                 "thành lập luận thống nhất.")
    md = "\n\n".join(parts)
    return {"title": f"[DEMO] {req.topic}", "thesis": "", "outline": [], "essay_markdown": md,
            "sources_used": [s.id for s in dossier.sources]}


# ------------------------------------------------------------------ chạy một bài


class EssayWriter:
    def __init__(self, settings: Settings | None = None, brain: Brain | None = None, searchers: list | None = None,
                 fetcher: Any = None):
        self.s = settings or get_settings()
        self._brain = brain
        self.searchers = searchers
        self.fetcher = fetcher

    def brain(self) -> Brain | None:
        if self._brain is None:
            from .writer import build_brain

            try:
                self._brain = build_brain(self.s)
            except BlockedByCredentials:
                return None
        return self._brain

    def research(self, req: EssayRequest) -> Dossier:
        b = self.brain()
        planner = b.student if (b and b.student and b.student.available()) else None
        queries = plan_queries(req.topic, req.requirements, req.language, planner=planner)
        searchers = self.searchers if self.searchers is not None else build_searchers(self.s)
        fetcher = self.fetcher if self.fetcher is not None else PageFetcher()
        return build_dossier(req.topic, req.requirements, queries, searchers, fetcher,
                             max_sources=self.s.research_max_sources, max_fetch=self.s.research_max_fetch, max_chars=12_000)

    def write(self, req: EssayRequest, dossier: Dossier, essay_id: str | None = None) -> tuple[dict, dict, dict]:
        """Trả (bài, usage, kết quả QA)."""
        from ..db import SessionLocal

        b = self.brain()
        if b is None:
            if not self.s.is_demo:
                raise BlockedByCredentials("Phòng Bài luận cần ít nhất một thầy (ANTHROPIC_API_KEY / OPENAI_API_KEY) "
                                           "hoặc model chạy trên máy (Ollama)")
            essay = demo_draft(req, dossier)
            return essay, {"source": "demo", "model": "demo", "usd": 0.0}, analyze(essay, dossier, req)
        with SessionLocal() as db:
            extra = style_targets(db)
        today = datetime.now(timezone.utc).strftime("%d/%m/%Y")
        system = ESSAY_SYSTEM
        task = Task("bai_luan", system, req.user_message(dossier, today), validate_essay,
                    check=lambda e: (lambda r: (r["ok"], r["problems"]))(analyze(e, dossier, req)),
                    query=f"{req.topic} {req.requirements}", max_tokens=max_tokens_for(req.words),
                    student_extra=extra, meta={"note": f"essay {essay_id or ''}".strip()})
        essay, usage = b.ask(task)
        return essay, usage, analyze(essay, dossier, req)


def run_essay(essay_id: str, writer: EssayWriter | None = None) -> None:
    """Chạy nền một bài luận đã tạo trong bảng essays. Ghi trạng thái từng bước để giao diện theo dõi."""
    from ..agents.budget import BudgetExceeded
    from ..db import SessionLocal
    from ..models import Essay

    w = writer or EssayWriter()

    def upd(**kw):
        with SessionLocal() as db:
            e = db.get(Essay, essay_id)
            for k, v in kw.items():
                setattr(e, k, v)
            db.commit()

    with SessionLocal() as db:
        e = db.get(Essay, essay_id)
        if e is None:
            return
        req = EssayRequest(e.topic, e.requirements or "", e.words, e.style, e.language)
    try:
        upd(status="researching", stage_note="Đang tìm và đọc tư liệu trên mạng…", error=None)
        dossier = w.research(req)
        if not dossier.sources:
            upd(status="failed", stage_note="Không tìm được tư liệu", dossier=dossier.to_dict(),
                error="Không tìm được tư liệu phù hợp. Thử diễn đạt chủ đề khác, hoặc cấu hình SearXNG/Brave để tìm rộng hơn.")
            return
        upd(status="writing", stage_note=f"Đang viết từ {len(dossier.sources)} nguồn…", dossier=dossier.to_dict(),
            is_demo=dossier.is_demo)
        for attempt in range(2):  # thầy trả sai định dạng → thử lại một lần (vẫn trong trần ngân sách)
            try:
                essay, usage, qa = w.write(req, dossier, essay_id)
                break
            except RetryableError:
                if attempt:
                    raise
        if not qa["ok"]:
            from . import learning

            with SessionLocal() as db:
                learning.on_essay_qa_failed(db, qa["problems"])
                db.commit()
        upd(status="done" if qa["ok"] else "needs_review", stage_note="Hoàn tất" if qa["ok"] else "Cần xem lại: chưa qua QA",
            title=essay["title"], content=essay, markdown=essay["essay_markdown"], qa=qa,
            source=usage.get("source"), model=usage.get("model"), usd=float(usage.get("usd") or 0.0),
            memory_id=usage.get("memory_id"), is_demo=dossier.is_demo or usage.get("source") == "demo")
    except BudgetExceeded as ex:
        upd(status="blocked", stage_note="Chạm trần ngân sách", error=str(ex))
    except BlockedByCredentials as ex:
        upd(status="blocked", stage_note="Thiếu kết nối AI", error=str(ex))
    except Exception as ex:  # noqa: BLE001 - lỗi bất kỳ: ghi lại để chủ kênh thấy, không làm sập app
        log.exception("Bài luận %s lỗi", essay_id, extra={"evcs_source": "agents"})
        upd(status="failed", stage_note="Lỗi", error=f"{type(ex).__name__}: {str(ex)[:500]}")


def export_markdown(e) -> str:
    """Bài hoàn chỉnh kèm danh mục nguồn, để chủ kênh tải về / dán sang nơi khác."""
    src = (e.dossier or {}).get("sources", [])
    used = set(cited_ids(e.markdown or ""))
    lines = [f"# {e.title or e.topic}", "", e.markdown or "", "", "## Nguồn tham khảo", ""]
    for s in src:
        if s["id"] in used:
            lic = f" — giấy phép {s['license']}" if s.get("license") else ""
            lines.append(f"- [{s['id']}] {s['title']} — {s.get('publisher') or ''} {s['url']}{lic}")
    return "\n".join(lines).strip() + "\n"
