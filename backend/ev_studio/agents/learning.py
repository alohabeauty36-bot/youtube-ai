"""Vòng học tập: biến mọi phản hồi (QA, chủ kênh, audio thật, claim đã xác minh) thành ký ức — không tốn tiền.
Riêng `reflect()` (Ban Đào tạo đúc kết sổ tay) gọi thầy model rẻ, tối đa 1 lần/tuần/phòng, có trần ngân sách riêng."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from . import memory
from .brain import Brain, Task, get_skill
from .departments import DEPARTMENTS

log = logging.getLogger(__name__)


def _mark_story_answers(db, story_id: str, departments: tuple[str, ...], **values) -> int:
    from ..models import AgentMemory as M

    r = db.execute(update(M).where(M.story_id == story_id, M.kind == "answer", M.department.in_(departments)).values(**values))
    return r.rowcount or 0


def on_script_qa_failed(db, story_id: str, problems: list[str]) -> None:
    """Kịch bản trượt QA: không cho bộ nhớ trả lại đúng bản này nữa, và ghi bài học."""
    _mark_story_answers(db, story_id, ("bien_kich",), quality=-1.0)
    for p in problems[:4]:
        memory.add_lesson(db, "bien_kich", f"Kịch bản từng trượt QA vì: {p}", source="system", story_id=story_id)


def on_script_approved(db, story, script_version) -> None:
    """Chủ kênh duyệt kịch bản: bản đã duyệt thành ví dụ mẫu tốt nhất cho cả thầy lẫn trò."""
    _mark_story_answers(db, story.id, ("bien_kich", "nghien_cuu"), approved=True, quality=1.0)
    content = script_version.content
    memory.remember(db, "bien_kich", "answer", key=memory.key_hash("owner-approved", script_version.id),
                    text=memory.flatten_text(content), response=content, request=story.core_question or story.topic,
                    source="owner", model="owner-approved", quality=1.0, approved=True, story_id=story.id, dedupe=True)


def on_revision_requested(db, story, note: str) -> None:
    if note and note.strip():
        memory.add_lesson(db, "bien_kich", f"Chủ kênh yêu cầu sửa: {note.strip()}", source="owner", story_id=story.id)


def on_rejected(db, story, note: str | None) -> None:
    _mark_story_answers(db, story.id, ("bien_kich",), quality=0.0)
    if note:
        memory.add_lesson(db, "bien_kich", f"Chủ kênh loại video vì: {note.strip()}", source="owner", story_id=story.id)


def on_audio_measured(db, words: int, seconds: float) -> None:
    if words > 50 and seconds > 30:
        wps = words / seconds
        memory.add_lesson(db, "bien_kich", f"Giọng đọc thực đo khoảng {wps:.1f} từ/giây: 600 giây lời đọc cần khoảng "
                                           f"{int(round(600 * wps, -1))} từ.", source="system")


def remember_claims(db, news_title: str, claims: list[dict], story_id: str | None = None) -> int:
    n = 0
    for c in claims:
        if c.get("status") != "verified":
            continue
        txt = f"{c['text']} (nguồn: {c.get('source_url') or '—'}; phạm vi: {c.get('date_scope') or '—'})"
        memory.remember(db, "san_tin", "knowledge", key=memory.key_hash("claim", " ".join(memory.tokens(c["text"]))), text=txt,
                        response={"claim": c, "news": news_title}, source="system", quality=1.0, story_id=story_id, dedupe=True)
        n += 1
    return n


REFLECT_SYSTEM = """Bạn là trưởng Ban Đào tạo của một tòa soạn video YouTube tiếng Việt về xe điện.
Nhiệm vụ: đọc các bài học rời rạc của MỘT phòng ban và viết lại thành SỔ TAY VĂN PHONG ngắn gọn, cụ thể, có thể làm theo ngay.
Quy tắc: không bịa thêm dữ kiện về xe hay hãng; gộp bài học trùng; bỏ bài học mâu thuẫn với nguyên tắc kiểm chứng nguồn;
tối đa 12 gạch đầu dòng, mỗi dòng dưới 30 từ. Trả về JSON: {"style_guide": "- ...\\n- ...", "dropped": ["..."]}"""


def reflect(db, department: str, brain: Brain, force: bool = False) -> dict:
    dept = DEPARTMENTS[department]
    sk = get_skill(db, department)
    last = sk.last_reflect_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if not force and last and datetime.now(timezone.utc) - last < timedelta(days=7):
        return {"ran": False, "reason": "Đã đúc kết trong 7 ngày qua"}
    ls = memory.lessons(db, department, 60)
    if len(ls) < 3:
        return {"ran": False, "reason": f"Mới có {len(ls)} bài học, cần ít nhất 3"}
    old = memory.style_guide(db, department) or "(chưa có)"
    user = f"PHÒNG: {dept.name} — {dept.role}\n\nSỔ TAY HIỆN TẠI:\n{old}\n\nBÀI HỌC:\n" + "\n".join(f"- {x}" for x in ls)
    db.commit()  # không giữ khoá ghi khi chờ thầy

    def validate(raw):
        g = str((raw or {}).get("style_guide", "")).strip()
        if not g:
            raise ValueError("style_guide rỗng")
        return {"style_guide": g[:2500], "dropped": list((raw or {}).get("dropped", []))[:20]}

    res, usage = brain.ask(Task("hoc_tap", REFLECT_SYSTEM, user, validate, use_cache=False, max_tokens=3000,
                                meta={"note": f"reflect {department}"}))
    memory.remember(db, department, "style_guide", key=memory.key_hash("guide", department, res["style_guide"]),
                    text=res["style_guide"], response=res, source=usage.get("source", "?"), model=usage.get("model"), quality=1.0)
    sk = get_skill(db, department)
    sk.last_reflect_at = datetime.now(timezone.utc)
    db.commit()
    return {"ran": True, "usd": usage.get("usd", 0), "style_guide": res["style_guide"]}


def reflect_all(brain: Brain) -> list[dict]:
    from ..db import SessionLocal

    out = []
    for code, d in DEPARTMENTS.items():
        if code == "hoc_tap":
            continue
        with SessionLocal() as db:
            try:
                out.append({"department": code, **reflect(db, code, brain)})
            except Exception as e:  # noqa: BLE001 - chạm trần ngân sách… → bỏ qua phòng này
                db.rollback()
                out.append({"department": code, "ran": False, "reason": str(e)[:200]})
    return out


def memory_stats(db) -> dict[str, dict[str, int]]:
    from sqlalchemy import func

    from ..models import AgentMemory as M

    stats: dict[str, dict[str, int]] = {}
    for d, k, n in db.execute(select(M.department, M.kind, func.count(M.id)).group_by(M.department, M.kind)):
        stats.setdefault(d, {})[k] = int(n)
    return stats


# ------------------------------------------------------------------ Phòng Bài luận


def on_essay_approved(db, essay, edited_markdown: str | None = None) -> None:
    """Chủ kênh duyệt bài luận: bản này (hoặc bản chủ kênh đã sửa) thành bài mẫu tốt nhất cho cả thầy lẫn trò."""
    from ..models import AgentMemory

    if essay.memory_id:
        m = db.get(AgentMemory, essay.memory_id)
        if m is not None:
            m.approved, m.quality = True, 1.0
    if edited_markdown and edited_markdown.strip() and edited_markdown.strip() != (essay.markdown or "").strip():
        content = {**(essay.content or {}), "essay_markdown": edited_markdown.strip()}
        memory.remember(db, "bai_luan", "answer", key=memory.key_hash("owner-essay", essay.id), text=memory.flatten_text(content),
                        response=content, request=essay.topic, source="owner", model="owner-edited", quality=1.0, approved=True,
                        dedupe=False)
        memory.add_lesson(db, "bai_luan", f"Chủ kênh đã tự sửa bài “{(essay.title or essay.topic)[:80]}” trước khi duyệt: "
                                          "học theo bản đã sửa.", source="owner")


def on_essay_feedback(db, essay, note: str) -> None:
    if note and note.strip():
        memory.add_lesson(db, "bai_luan", f"Chủ kênh góp ý bài luận: {note.strip()}", source="owner")


def on_essay_rejected(db, essay, note: str | None) -> None:
    from ..models import AgentMemory

    if essay.memory_id:
        m = db.get(AgentMemory, essay.memory_id)
        if m is not None:
            m.quality = -1.0  # không trả lại từ bộ nhớ, không dùng làm bài mẫu
    if note:
        memory.add_lesson(db, "bai_luan", f"Chủ kênh loại bài luận vì: {note.strip()}", source="owner")


def on_essay_qa_failed(db, problems: list[str]) -> None:
    for p in problems[:3]:
        memory.add_lesson(db, "bai_luan", f"Bài luận từng trượt QA vì: {p}", source="system")
