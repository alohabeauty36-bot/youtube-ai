"""Quality gates cho kịch bản (trước TTS) — mục D của SRS."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ranker import strip_accents

BANNED_UNSUPPORTED = ["pha san", "cam xe xang", "mien phi vinh vien", "mien phi mai", "mien phi tron doi"]
STAGE_DIRECTION = re.compile(r"(\[(?:CẢNH|CANH|SFX|NHẠC|NHAC|B-ROLL|HÌNH|HINH)[^\]]*\]|\((?:cảnh|nhạc|sfx|b-roll)[^)]*\)|^#{1,6}\s|\*\*|^\s*[-*•]\s|\|)", re.I | re.M)
TIMESTAMP = re.compile(r"\b\d{1,2}:\d{2}\b")
URL = re.compile(r"https?://|www\.", re.I)


@dataclass
class QAResult:
    passed: bool
    checks: list[dict] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", blocking: bool = True):
        self.checks.append({"name": name, "ok": ok, "detail": detail, "blocking": blocking})
        if blocking and not ok:
            self.passed = False

    def as_dict(self):
        return {"passed": self.passed, "checks": self.checks}


def shingles(text: str, n: int = 8) -> set[tuple]:
    w = re.findall(r"\w+", strip_accents(text))
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def sentence_count(p: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?…])\s+", p.strip()) if s])


def run_script_qa(out: dict, *, pack_claims: list[dict], source_texts: list[str], recent_scripts: list[str],
                  supported_phrases_text: str = "", is_demo: bool = False, analysis: bool | None = None,
                  publishers: list[str] | None = None) -> QAResult:
    from .fact_checker import analysis_mode, has_attribution

    if analysis is None:
        analysis = analysis_mode()
    facts = ("verified", "reported") if analysis else ("verified",)
    r = QAResult(passed=True)
    spoken: str = out.get("spoken_script", "")
    r.add("spoken_script không rỗng", bool(spoken.strip()), "Claude trả unresolved_questions" if not spoken.strip() else "")
    if not spoken.strip():
        r.add("Câu hỏi chưa giải quyết", False, "; ".join(out.get("unresolved_questions_for_editor", [])))
        return r
    if out.get("unresolved_questions_for_editor"):
        r.add("Không còn câu hỏi chưa giải quyết", False, "; ".join(out["unresolved_questions_for_editor"]))

    # sạch để TTS
    sd = STAGE_DIRECTION.findall(spoken)
    r.add("Không chỉ dẫn dựng/markdown trong lời đọc", not sd, f"Tìm thấy: {sd[:3]}" if sd else "")
    r.add("Không timestamp trong lời đọc", not TIMESTAMP.search(spoken))
    r.add("Không URL trong lời đọc", not URL.search(spoken))

    # claim traceability
    by_key = {c["claim_key"]: c for c in pack_claims}
    bad = []
    for entry in out.get("editorial_claim_map", []):
        c = by_key.get(entry["claim_id"])
        if c is None:
            bad.append(f"{entry['claim_id']} không có trong ResearchPack")
        elif c["status"] not in (*facts, "inference"):
            bad.append(f"{entry['claim_id']} có status {c['status']}")
        elif not set(entry.get("source_ids", [])) & set(c.get("source_ids", [])) and c["status"] in facts:
            bad.append(f"{entry['claim_id']} gắn source_id sai")
    r.add("Claim map chỉ dùng claim đã xác minh" if not analysis else "Claim map chỉ dùng claim có nguồn", not bad, "; ".join(bad))
    verified_keys = {c["claim_key"] for c in pack_claims if c["status"] in facts and c.get("is_key", True)}
    mapped = {e["claim_id"] for e in out.get("editorial_claim_map", [])}
    r.add("Có ít nhất một claim trọng yếu được dẫn", bool(verified_keys & mapped), f"mapped={sorted(mapped)}")
    if analysis:  # tin mới có một báo đưa: câu dùng nó phải ghi nguồn ("theo <báo>")
        no_attr = [e["claim_id"] for e in out.get("editorial_claim_map", [])
                   if by_key.get(e["claim_id"], {}).get("status") == "reported"
                   and not has_attribution(e.get("relevant_script_excerpt", ""), publishers or [])]
        r.add("Tin chưa xác minh được ghi rõ nguồn (\"theo <báo>\")", not no_attr, ",".join(no_attr))
    for e in out.get("production_notes", {}).get("key_on_screen_facts", []):
        if e["claim_id"] not in by_key or by_key[e["claim_id"]]["status"] not in facts:
            r.add("Dữ kiện trên màn hình có claim verified", False, e["claim_id"])
            break
    # claim bị cấm (unverified/contested) không được nhắc tới
    norm_spoken = strip_accents(spoken)
    leaked = [c["claim_key"] for c in pack_claims if c["status"] in ("unverified", "contested")
              and c.get("value") and len(str(c["value"])) >= 2 and strip_accents(c["text"]) in norm_spoken]
    r.add("Không dùng claim chưa xác minh/tranh cãi", not leaked, ",".join(leaked))

    # tiêu đề
    titles = out.get("title_options", [])
    r.add("Chính xác 3 tiêu đề", len(titles) == 3)
    supported = strip_accents(supported_phrases_text)
    over = [t["title"] for t in titles if any(b in strip_accents(t["title"]) and b not in supported for b in BANNED_UNSUPPORTED)]
    over += [p for p in BANNED_UNSUPPORTED if p in norm_spoken and p not in supported]
    r.add("Không khẳng định quá mức thiếu nguồn", not over, "; ".join(over))

    # closing teaser cuối bài
    teaser = out.get("closing_teaser", "").strip()
    r.add("closing_teaser nằm cuối spoken_script", bool(teaser) and spoken.rstrip().endswith(teaser.rstrip()))

    # độ dài & đoạn
    words = len(spoken.split())
    r.add("Số từ trong khoảng tham khảo 1.100–1.900", 1100 <= words <= 1900, f"{words} từ", blocking=False)
    paras = paragraphs(spoken)
    long_paras = [i for i, p in enumerate(paras) if sentence_count(p) > 6]
    r.add("Đoạn 2–5 câu (cảnh báo)", not long_paras, f"đoạn dài: {long_paras[:5]}", blocking=False)

    # originality: không chép dài từ nguồn. Câu dữ kiện ngắn trùng nguồn là bình thường;
    # chặn khi tỉ lệ chuỗi 12 từ trùng > 6% hoặc có đoạn chép liền ≥ 25 từ.
    src_sh = set().union(*[shingles(t, 12) for t in source_texts]) if source_texts else set()
    sp12 = shingles(spoken, 12)
    copied = sp12 & src_sh
    ratio = len(copied) / max(1, len(sp12))
    long_run = bool(sp12 and (shingles(spoken, 25) & set().union(*[shingles(t, 25) for t in source_texts])))
    r.add("originality_check: không chép dài nguồn", ratio <= 0.06 and not long_run,
          f"{len(copied)} chuỗi 12 từ trùng ({ratio:.1%}), chép liền ≥25 từ: {long_run}")

    # repetition: không trùng video gần đây + không lặp đoạn nội bộ
    sp_sh = shingles(spoken)
    worst = 0.0
    for old in recent_scripts:
        o = shingles(old)
        if o and sp_sh:
            worst = max(worst, len(o & sp_sh) / len(sp_sh))
    limit = 0.95 if is_demo else 0.35  # demo writer dùng template chung → chỉ cảnh báo
    r.add("repetition_check: khác video gần đây", worst <= limit, f"trùng {worst:.0%}", blocking=not is_demo)
    seen, dup = set(), 0
    for p in paras:
        k = strip_accents(p)[:120]
        dup += k in seen
        seen.add(k)
    r.add("Không lặp đoạn trong bài", dup == 0, f"{dup} đoạn lặp")
    return r


def duration_ok(seconds: float, lo: float, hi: float) -> bool:
    return lo <= seconds <= hi
