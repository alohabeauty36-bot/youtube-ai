"""AgentWriter — WriterProvider chạy qua các phòng ban (bộ nhớ + thầy + trò + Ban Tài chính).

Dùng cùng prompt với AnthropicWriter (đọc từ prompts/), nên chất lượng khi gọi thầy giữ nguyên;
khác biệt là mọi lần gọi đều qua trần ngân sách, được ghi nhớ, và dần được trò chạy trên máy làm thay.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from ..config import Settings, get_settings
from ..errors import BlockedByCredentials, RetryableError
from ..schemas import ExtractedClaims, ResearchPackLLM, ScriptOutput
from ..services.claude_writer import load_prompts, render_developer_message, render_user_task
from .brain import Brain, Task
from .departments import DEPARTMENTS, teacher_model
from .teachers import AnthropicTeacher, LocalStudent, OpenAITeacher

WORDS_PER_SECOND = 2.42


def _schema(model):
    def v(raw):
        try:
            return model.model_validate(raw).model_dump()
        except ValidationError as e:
            raise RetryableError(f"sai schema {model.__name__}: {e.errors()[:2]}") from e
    return v


def build_brain(settings: Settings | None = None, anthropic_client: Any = None, openai_http: Any = None,
                student: Any = None) -> Brain:
    s = settings or get_settings()
    teachers: dict[str, Any] = {}
    akey = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else ""
    if akey or anthropic_client is not None:
        teachers["anthropic"] = PerDeptAnthropic(s, akey or "sk-ant-injected", anthropic_client)
    okey = s.openai_api_key.get_secret_value() if s.openai_api_key else ""
    if (okey and s.openai_model) or openai_http is not None:
        teachers["openai"] = OpenAITeacher(okey or "injected", s.openai_model or "gpt-test", s.openai_base_url, http=openai_http)
    if student is None and s.local_llm_url:
        student = LocalStudent(s.local_llm_url, s.local_llm_model, s.local_llm_timeout_seconds)
    if not teachers and student is None:
        raise BlockedByCredentials("Chưa có ANTHROPIC_API_KEY / OPENAI_API_KEY và chưa kết nối model chạy trên máy")
    return Brain(s, teachers, student)


class PerDeptAnthropic:
    """Thầy Claude: mỗi phòng dùng model riêng (Săn tin dùng Haiku rẻ, Biên kịch dùng Sonnet…)."""

    def __init__(self, settings: Settings, key: str, client: Any = None):
        self.s, self.key, self.client, self._cache = settings, key, client, {}

    @property
    def model(self) -> str:
        return self.s.anthropic_model

    def for_dept(self, code: str) -> AnthropicTeacher:
        model = teacher_model(self.s, DEPARTMENTS[code])
        if model not in self._cache:
            self._cache[model] = AnthropicTeacher(self.key, model, client=self.client)
        return self._cache[model]


class AgentWriter:
    name = "agents"
    is_demo = False

    def __init__(self, settings: Settings | None = None, brain: Brain | None = None, **kw):
        self.s = settings or get_settings()
        self.brain = brain or build_brain(self.s, **kw)
        self.bundle = load_prompts()
        self.model = teacher_model(self.s, DEPARTMENTS["bien_kich"])

    def _ask(self, task: Task):
        return self.brain.ask(task)

    # ---------------------------------------------------------------- Phòng Săn tin

    def extract_claims(self, title: str, text: str):
        def validate(raw):
            if isinstance(raw, list):  # bản lưu trong bộ nhớ
                raw = {"claims": raw}
            try:
                return [c.model_dump() for c in ExtractedClaims.model_validate(raw).claims]
            except ValidationError as e:
                raise RetryableError(f"Claim JSON sai schema: {e.errors()[:2]}") from e

        def check(claims):
            bad = [c["claim_key"] for c in claims if c.get("evidence_excerpt") and c["evidence_excerpt"][:40] not in text]
            return (not bad, [f"trích dẫn không có nguyên văn trong bài: {bad[:3]}"] if bad else [])

        res, usage = self._ask(Task("san_tin", self.bundle.claim_system, f"TIÊU ĐỀ: {title}\n\nVĂN BẢN:\n{text}",
                                    validate, check, query=title, max_tokens=4000))
        return res, usage

    # ---------------------------------------------------------------- Phòng Nghiên cứu

    def research_pack(self, ctx: dict):
        user = json.dumps({k: ctx[k] for k in ("news", "sources", "claims", "recent_topics", "pillar")}, ensure_ascii=False)
        known = {x["source_id"] for x in ctx.get("sources", [])}

        def check(pack):
            unknown = set(pack.get("source_ids", [])) - known
            return (not unknown, [f"nhắc source_id không tồn tại: {sorted(unknown)}"] if unknown else [])

        from ..services.fact_checker import prompt_note

        return self._ask(Task("nghien_cuu", self.bundle.research_system + prompt_note("research"), user, _schema(ResearchPackLLM), check,
                              query=ctx["news"].get("title", ""), story_id=ctx.get("story_id"), max_tokens=6000))

    # ---------------------------------------------------------------- Phòng Biên kịch

    def write_script(self, ctx: dict):
        from ..services.fact_checker import prompt_note
        from ..services.script_qa import run_script_qa

        system = self.bundle.system + prompt_note("script") + "\n\n" + self.bundle.pillar_profiles[ctx["pillar"]]
        user = render_developer_message(self.bundle, ctx) + "\n\n" + render_user_task(self.bundle, ctx["core_question"])
        pack = ctx.get("research_pack") or {}
        pack_claims = list(pack.get("claims", [])) + list(pack.get("claims_to_avoid", []))
        target_s = float(ctx.get("target_duration_seconds") or 660)
        if ctx.get("actual_audio_seconds") and ctx.get("previous_spoken_script"):
            target_words = len(ctx["previous_spoken_script"].split()) * target_s / float(ctx["actual_audio_seconds"])
        else:
            target_words = target_s * WORDS_PER_SECOND

        def check(out):
            pubs = [x.get("publisher") or "" for x in pack.get("sources", [])] + [(pack.get("news") or {}).get("publisher") or ""]
            qa = run_script_qa(out, pack_claims=pack_claims, source_texts=[], recent_scripts=[], publishers=pubs)
            problems = [f"{c['name']}: {c['detail']}" for c in qa.checks if c["blocking"] and not c["ok"]]
            words = len(out.get("spoken_script", "").split())
            if not 0.8 * target_words <= words <= 1.2 * target_words:
                problems.append(f"độ dài {words} từ, cần khoảng {int(target_words)}")
            return (not problems, problems)

        q = " ".join([ctx.get("core_question", ""), pack.get("topic", ""), pack.get("editorial_angle", "")])
        return self._ask(Task("bien_kich", system, user, _schema(ScriptOutput), check, query=q,
                              story_id=ctx.get("story_id"), max_tokens=self.s.anthropic_max_tokens))

    # ---------------------------------------------------------------- Phòng Biên tập

    def rewrite_paragraph(self, ctx: dict, paragraph: str, instruction: str):
        from ..services.fact_checker import prompt_note

        system = (self.bundle.system + prompt_note("script") + "\n\nNHIỆM VỤ PHỤ: viết lại MỘT đoạn lời đọc. Trả JSON {\"paragraph\": \"...\"}. "
                  "Giữ nguyên mọi dữ kiện có claim, không thêm dữ kiện mới ngoài RESEARCH_PACK.")
        user = render_developer_message(self.bundle, ctx) + f"\n\nĐOẠN CẦN SỬA:\n{paragraph}\n\nYÊU CẦU SỬA: {instruction}"

        def validate(raw):
            p = str((raw or {}).get("paragraph", "")).strip()
            if not p:
                raise RetryableError("đoạn rỗng")
            return {"paragraph": p}

        res, usage = self._ask(Task("bien_tap", system, user, validate, query=paragraph[:300] + " " + instruction,
                                    story_id=ctx.get("story_id"), max_tokens=2000))
        return res["paragraph"], usage
