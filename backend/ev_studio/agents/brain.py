"""Bộ não của một phòng ban: nhớ → (trò tự làm | nhờ thầy) → học.

Thứ tự xử lý mỗi đề bài:
1. BỘ NHỚ: đề y hệt đã từng hỏi thầy và câu trả lời còn tốt → dùng lại, $0.
2. TRÒ TỰ LÀM (model chạy trên máy, $0) khi phòng đã "tốt nghiệp" — trừ một tỷ lệ nhỏ việc vẫn gửi thầy để kiểm tra
   chất lượng (audit). Trò làm hỏng → ghi điểm trượt, chuyển sang thầy.
3. THẦY (Claude hoặc ChatGPT, tốn tiền): luôn qua Ban Tài chính giữ chỗ trước. Chạm trần → thử trò; trò cũng không được
   → dừng (BudgetExceeded), KHÔNG tiêu thêm.
4. HỌC: câu trả lời của thầy được lưu vào bộ nhớ. Trò làm thử cùng đề (chạy nền, miễn phí) và được chấm: đúng schema,
   qua QA nghiệp vụ, và nội dung đủ giống thầy. Đạt ≥ 80% trong 20 lần gần nhất (tối thiểu 10 lần) → tốt nghiệp.

Không fine-tune model từ câu trả lời của Claude/ChatGPT: "học" ở đây là bộ nhớ + truy xuất + bài học + ví dụ mẫu
đưa vào prompt của model chạy máy. Cách này không vi phạm điều khoản cấm dùng output để huấn luyện model cạnh tranh.
"""
from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings, get_settings
from ..errors import BlockedByCredentials, GateBlocked, RetryableError
from . import budget, memory, pricing
from .departments import DEPARTMENTS, MASTER, Department, level_of, teacher_model

log = logging.getLogger(__name__)
_shadow_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-shadow")

ZERO = {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0, "usd": 0.0}


@dataclass
class Task:
    department: str
    system: str
    user: str
    validate: Callable[[dict], Any]  # chuẩn hoá output; raise nếu sai schema
    check: Callable[[Any], tuple[bool, list[str]]] | None = None  # QA nghiệp vụ, không dùng LLM
    query: str = ""  # chuỗi để tra cứu bài học / kiến thức / ví dụ
    story_id: str | None = None
    max_tokens: int | None = None
    use_cache: bool = True
    meta: dict = field(default_factory=dict)
    student_extra: str = ""  # hướng dẫn thêm chỉ dành cho trò (ví dụ mục tiêu cấu trúc rút ra từ bài của thầy)


@dataclass
class Attempt:
    ok: bool
    result: Any = None
    problems: list[str] = field(default_factory=list)
    similarity: float | None = None
    usage: dict = field(default_factory=dict)


class Brain:
    def __init__(self, settings: Settings | None = None, teachers: dict[str, Any] | None = None, student: Any = None,
                 rng: random.Random | None = None):
        self.s = settings or get_settings()
        self.teachers = teachers or {}
        self.student = student
        self.rng = rng or random.Random()

    # ------------------------------------------------------------------ public

    def ask(self, task: Task) -> tuple[Any, dict]:
        from ..db import SessionLocal

        dept = DEPARTMENTS[task.department]
        key = memory.key_hash(dept.code, task.system, task.user)
        with SessionLocal() as db:
            skill = get_skill(db, dept.code)
            mode, graduated = skill.mode, skill.graduated
            level = level_of(list(skill.recent or []), skill.student_passes, graduated, self.s)
            if task.use_cache:
                hit = memory.recall_exact(db, dept.code, key)
                if hit is not None:
                    try:
                        result = task.validate(hit.response)
                    except Exception:  # noqa: BLE001 - ký ức cũ không còn hợp schema → bỏ qua
                        result = None
                    if result is not None:
                        hit.uses += 1
                        skill.cache_hits += 1
                        skill.saved_usd += self._teacher_price_guess(dept, task)
                        db.commit()
                        return result, {**ZERO, "source": "memory", "memory_id": hit.id, "model": hit.model or "memory"}
            context = build_context(db, dept, task.query)
            examples = build_examples(db, dept, task.query)
        student_ok = bool(self.student) and dept.learns and self.student.available()
        if mode == "student_only" and not student_ok:
            raise BlockedByCredentials(f"{dept.name} đặt 'chỉ trò tự làm' nhưng chưa kết nối model chạy trên máy (Ollama)")

        # 2. trò tự làm. Bậc thầy: không gửi thầy kiểm tra ngẫu nhiên nữa (agent_master_audit_rate, mặc định 0)
        audit_rate = self.s.agent_master_audit_rate if level >= MASTER else self.s.agent_audit_rate
        if student_ok and (mode == "student_only" or (mode == "auto" and graduated and self.rng.random() >= audit_rate)):
            att = self._student(dept, task, context, examples)
            self._score(dept.code, att, live=True, saved=self._teacher_price_guess(dept, task) if att.ok else 0.0)
            if att.ok:
                mid = self._remember(dept, task, key, att.result, "local", self.student.model, quality=0.8)
                return att.result, {**ZERO, "source": "local", "model": self.student.model, "memory_id": mid, "level": level}
            if mode == "student_only":
                raise GateBlocked([f"{dept.name} (trò tự làm) chưa đạt: {p}" for p in att.problems[:3]])

        # 3. thầy
        try:
            result, usage = self._teacher(dept, task, context)
        except budget.BudgetExceeded:
            if student_ok and mode != "teacher_only":
                att = self._student(dept, task, context, examples)
                self._score(dept.code, att, live=True)
                if att.ok:
                    mid = self._remember(dept, task, key, att.result, "local", self.student.model, quality=0.6)
                    return att.result, {**ZERO, "source": "local", "model": self.student.model, "memory_id": mid,
                                        "note": "Hết ngân sách → trò tự làm"}
            raise
        ok, problems = task.check(result) if task.check else (True, [])
        mid = self._remember(dept, task, key, result, usage["provider"], usage["model"], quality=1.0 if ok else 0.3)
        usage["memory_id"] = mid
        usage["source"] = usage["provider"]

        # 4. học: trò làm thử cùng đề, chấm so với thầy
        if student_ok and mode != "teacher_only" and self.s.agent_shadow_learning:
            fn = lambda: self._shadow(dept, task, context, examples, result)  # noqa: E731
            if self.s.agent_shadow_async:
                _shadow_pool.submit(fn)
            else:
                fn()
        return result, usage

    # ------------------------------------------------------------------ thầy

    def pick_teacher(self, dept: Department) -> tuple[str, Any]:
        strat = self.s.teacher_strategy
        if dept.strategy_setting:  # phòng có thầy riêng (Phòng Bài luận mặc định luân phiên Claude/ChatGPT)
            own = getattr(self.s, dept.strategy_setting, "inherit") or "inherit"
            strat = strat if own == "inherit" else own
        order = {"anthropic": ["anthropic", "openai"], "openai": ["openai", "anthropic"]}.get(strat)
        if strat == "alternate":
            from ..db import SessionLocal

            with SessionLocal() as db:
                n = get_skill(db, dept.code).teacher_calls
            order = ["anthropic", "openai"] if n % 2 == 0 else ["openai", "anthropic"]
        for name in order:
            t = self.teachers.get(name)
            if t is not None:
                if hasattr(t, "for_dept"):  # mỗi phòng một model riêng
                    t = t.for_dept(dept.code)
                return name, t
        raise BlockedByCredentials("Chưa có thầy nào: cần ANTHROPIC_API_KEY hoặc OPENAI_API_KEY")

    def _teacher(self, dept: Department, task: Task, context: str) -> tuple[Any, dict]:
        name, t = self.pick_teacher(dept)
        system = task.system + context
        max_out = task.max_tokens or dept.max_tokens
        est_in = pricing.estimate_tokens(system + task.user)
        rid = budget.reserve(dept.code, name, t.model, est_in, max_out, story_id=task.story_id, note=task.meta.get("note", ""))
        try:
            raw, usage = t.complete_json(system, task.user, max_out)
        except Exception:
            budget.release(rid)  # API lỗi không tính tiền
            raise
        usage["usd"] = budget.settle(rid, usage)
        usage.setdefault("provider", name)
        from ..db import SessionLocal

        with SessionLocal() as db:
            get_skill(db, dept.code).teacher_calls += 1
            db.commit()
        try:
            return task.validate(raw), usage
        except Exception as e:  # tiền đã tiêu nhưng output sai schema → orchestrator sẽ retry (vẫn trong trần)
            raise RetryableError(f"{dept.name}: thầy {name} trả sai schema: {e}") from e

    def _teacher_price_guess(self, dept: Department, task: Task) -> float:
        try:
            model = teacher_model(self.s, dept)
            return pricing.cost_usd(model, pricing.estimate_tokens(task.system + task.user) // 2,
                                    (task.max_tokens or dept.max_tokens) // 3, overrides_json=self.s.model_prices_json)
        except GateBlocked:
            return 0.0

    # ------------------------------------------------------------------ trò

    def _student(self, dept: Department, task: Task, context: str, examples: str, teacher_result: Any = None) -> Attempt:
        try:
            raw, usage = self.student.complete_json(task.system + context + examples + task.student_extra, task.user,
                                                    task.max_tokens or dept.max_tokens)
            result = task.validate(raw)
        except Exception as e:  # noqa: BLE001
            return Attempt(False, problems=[f"{type(e).__name__}: {str(e)[:200]}"])
        ok, problems = task.check(result) if task.check else (True, [])
        sim = None
        if teacher_result is not None:
            sim = memory.overlap_f1(memory.flatten_text(result), memory.flatten_text(teacher_result))
            if sim < dept.similarity_threshold:
                ok = False
                problems = problems + [f"nội dung khác thầy quá xa (giống {sim:.0%}, cần ≥ {dept.similarity_threshold:.0%})"]
        return Attempt(ok, result, problems, sim, usage)

    def _shadow(self, dept: Department, task: Task, context: str, examples: str, teacher_result: Any) -> None:
        try:
            att = self._student(dept, task, context, examples, teacher_result)
            self._score(dept.code, att, live=False)
        except Exception:  # noqa: BLE001 - học thử không được làm hỏng pipeline
            log.warning("%s: học thử lỗi", dept.name, exc_info=True, extra={"evcs_source": "agents"})

    def _score(self, code: str, att: Attempt, live: bool, saved: float = 0.0) -> None:
        from ..db import SessionLocal

        with SessionLocal() as db:
            sk = get_skill(db, code)
            sk.student_attempts += 1
            sk.student_passes += 1 if att.ok else 0
            if live and att.ok:
                sk.student_live += 1
                sk.saved_usd += saved
            win = list(sk.recent or []) + [1 if att.ok else 0]
            sk.recent = win[-self.s.agent_graduation_window:]
            if att.similarity is not None:
                sk.similarity_avg = round(att.similarity if not sk.similarity_avg else 0.8 * sk.similarity_avg + 0.2 * att.similarity, 4)
            rate = sum(sk.recent) / len(sk.recent)
            was = sk.graduated
            sk.graduated = len(sk.recent) >= self.s.agent_graduation_min and rate >= self.s.agent_graduation_rate
            if was != sk.graduated:
                log.info("%s %s (tỷ lệ đạt %.0f%%)", code, "TỐT NGHIỆP" if sk.graduated else "bị hạ về học việc", rate * 100)
            if not att.ok and att.problems:
                memory.add_lesson(db, code, "Trò từng làm hỏng: " + "; ".join(att.problems[:2]), source="system")
            db.commit()

    # ------------------------------------------------------------------ ghi nhớ

    def _remember(self, dept: Department, task: Task, key: str, result: Any, source: str, model: str, quality: float) -> int:
        from ..db import SessionLocal

        with SessionLocal() as db:
            m = memory.remember(db, dept.code, "answer", key=key, text=memory.flatten_text(result), response=result,
                                request=task.user[:20000], source=source, model=model, quality=quality, story_id=task.story_id)
            db.commit()
            return m.id


# ---------------------------------------------------------------------- helpers


def get_skill(db, code: str):
    from ..models import AgentSkill

    sk = db.get(AgentSkill, code)
    if sk is None:
        sk = AgentSkill(department=code, recent=[])
        db.add(sk)
        db.flush()
    return sk


def build_context(db, dept: Department, query: str) -> str:
    """Kinh nghiệm của phòng: sổ tay văn phong + bài học. Giữ ngắn (≤ ~2.500 ký tự) để không đội tiền token của thầy."""
    parts = []
    guide = memory.style_guide(db, dept.code)
    if guide:
        parts.append("## SỔ TAY VĂN PHONG CỦA " + dept.name.upper() + "\n" + guide[:1500])
    ls = memory.lessons(db, dept.code, 8)
    if ls:
        parts.append("## BÀI HỌC ĐÃ RÚT RA (tuân thủ nếu không mâu thuẫn quy tắc phía trên)\n" + "\n".join(f"- {x[:220]}" for x in ls))
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def build_examples(db, dept: Department, query: str) -> str:
    """Chỉ dành cho trò: 1–2 câu trả lời tốt nhất của thầy (ưu tiên bản chủ kênh đã duyệt) cho đề gần giống."""
    if dept.code == "bai_luan":
        return _essay_examples(db, query)
    rows = memory.search(db, dept.code, query, kinds=["answer"], k=2)
    rows = [r for r in rows if r.source in ("anthropic", "openai") or r.approved]
    if not rows:
        return ""
    budget_chars = 5000 if dept.code == "bien_kich" else 3000
    blocks = [memory.dumps(r.response)[:budget_chars] for r in rows]
    know = memory.search(db, ["san_tin", "nghien_cuu"], query, kinds=["knowledge"], k=6)
    kb = ("\n## KIẾN THỨC ĐÃ XÁC MINH LIÊN QUAN\n" + "\n".join(f"- {k.text[:200]}" for k in know)) if know else ""
    return ("\n\n## VÍ DỤ ĐẦU RA TỐT (chỉ học CẤU TRÚC và VĂN PHONG; KHÔNG chép dữ kiện, chỉ dùng dữ kiện trong đề bài)\n"
            + "\n---\n".join(blocks) + kb)


def _essay_examples(db, query: str) -> str:
    """Phòng Bài luận: một bài mẫu của MỖI thầy (Claude, ChatGPT; bản chủ kênh duyệt được ưu tiên) cho chủ đề gần nhất,
    để trò thấy hai cách viết khác nhau. Không kèm kho kiến thức: bài luận chỉ được dùng dữ kiện trong hồ sơ tư liệu."""
    rows = memory.search(db, "bai_luan", query, kinds=["answer"], k=8)
    if not rows:  # chủ đề mới hoàn toàn → lấy bài tốt gần đây nhất
        rows = memory.search(db, "bai_luan", "", kinds=["answer"], k=8)
    picked: dict[str, Any] = {}
    for r in sorted(rows, key=lambda r: (not r.approved, -(r.quality or 0))):
        who = "owner" if r.approved else r.source
        if who in ("anthropic", "openai", "owner") and (r.quality or 0) >= 0.9 and who not in picked:
            picked[who] = r
    if not picked:
        return ""
    label = {"anthropic": "Claude", "openai": "ChatGPT", "owner": "bản chủ kênh đã duyệt"}
    blocks = []
    for who, r in list(picked.items())[:2]:
        resp = r.response or {}
        body = f"# {resp.get('title', '')}\n{resp.get('essay_markdown', '')}" if isinstance(resp, dict) else memory.dumps(resp)
        blocks.append(f"### Bài mẫu ({label.get(who, who)})\n{body[:3500]}")
    return ("\n\n## BÀI LUẬN MẪU CỦA THẦY (chỉ học CẤU TRÚC, CÁCH LẬP LUẬN, CÁCH TRÍCH NGUỒN; KHÔNG chép câu chữ hay dữ kiện)\n"
            + "\n\n".join(blocks))
