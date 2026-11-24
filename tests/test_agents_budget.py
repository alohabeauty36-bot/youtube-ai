"""Ban Tài chính (trần chi tiêu) + bộ não phòng ban (bộ nhớ, thầy, trò, tốt nghiệp, học từ phản hồi)."""
import json
import random
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr


# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def clean(db_ready):
    from ev_studio.db import SessionLocal
    from ev_studio.models import AgentMemory, AgentSkill, BudgetLedger

    def wipe():
        with SessionLocal() as s:
            for m in (BudgetLedger, AgentMemory, AgentSkill):
                s.query(m).delete()
            s.commit()

    wipe()
    yield
    wipe()


@pytest.fixture()
def settings(monkeypatch, clean):
    """Settings cho test: trần tháng 30 USD, không giữ lại, tốt nghiệp sau 3 lần đạt."""
    from ev_studio.agents import budget
    from ev_studio.config import get_settings

    s = get_settings().model_copy(update={
        "anthropic_api_key": SecretStr("sk-ant-test-xxxxxxxx"), "budget_monthly_usd": 30.0, "budget_safety_margin": 0.0,
        "budget_story_max_usd": 5.0, "agent_shadow_async": False, "agent_graduation_min": 3,
        "agent_graduation_window": 5, "agent_graduation_rate": 0.8, "agent_audit_rate": 0.0,
    })
    monkeypatch.setattr(budget, "get_settings", lambda: s)
    return s


class FakeMessages:
    def __init__(self, payload, usage=(1000, 500, 0, 0)):
        self.payload, self.usage, self.calls = payload, usage, []

    def create(self, **kw):
        self.calls.append(kw)
        i, o, cw, cr = self.usage
        text = self.payload(kw) if callable(self.payload) else self.payload
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw,
                                                     cache_read_input_tokens=cr))


class FakeStudent:
    provider = "local"
    model = "fake-qwen"

    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def available(self):
        return True

    def complete_json(self, system, user, max_tokens):
        self.calls += 1
        self.last_system = system
        a = self.answer(system, user) if callable(self.answer) else self.answer
        return a, {"input_tokens": 0, "output_tokens": 0, "model": self.model, "provider": "local"}


def _brain(settings, payload, student=None, usage=(1000, 500, 0, 0)):
    from ev_studio.agents.writer import build_brain

    fake = SimpleNamespace(messages=FakeMessages(payload, usage))
    return build_brain(settings, anthropic_client=fake, student=student), fake.messages


def _task(user="Đề bài số 1", dept="nghien_cuu", check=None, story_id=None):
    from ev_studio.agents.brain import Task

    def validate(raw):
        if not isinstance(raw, dict) or "answer" not in raw:
            raise ValueError("thiếu answer")
        return {"answer": raw["answer"]}

    return Task(dept, "Bạn là chuyên gia xe điện.", user, validate, check, query=user, story_id=story_id, max_tokens=1000)


ANSWER = json.dumps({"answer": "Trạm sạc mới mở rộng mạng lưới cho người dùng xe điện tại Hà Nội"}, ensure_ascii=False)


# ------------------------------------------------------------------ bảng giá


def test_pricing_matches_published_table():
    from ev_studio.agents import pricing

    assert pricing.cost_usd("claude-sonnet-5-5", 1_000_000, 0) == 2.0
    assert pricing.cost_usd("claude-sonnet-5-5", 0, 1_000_000) == 10.0
    assert pricing.cost_usd("claude-sonnet-5-5", 0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.10)
    assert pricing.cost_usd("claude-sonnet-5-5", 0, 0, cache_write_tokens=1_000_000) == pytest.approx(2.5)
    assert pricing.cost_usd("claude-haiku-5-5", 50_000, 0) == pytest.approx(0.005)
    assert pricing.cost_usd("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)  # >100K → giá cao
    assert pricing.cost_usd("claude-opus-5-5-20261101", 1_000_000, 0) == 4.0  # hậu tố ngày
    with pytest.raises(pricing.UnknownPrice):
        pricing.cost_usd("gpt-unknown", 10, 10)
    assert pricing.cost_usd("gpt-x", 1_000_000, 1_000_000, overrides_json='{"gpt-x": [1.5, 6]}') == 7.5


# ------------------------------------------------------------------ Ban Tài chính


def test_reserve_settle_and_monthly_cap(settings):
    from ev_studio.agents import budget
    from ev_studio.db import SessionLocal

    rid = budget.reserve("bien_kich", "anthropic", "claude-sonnet-5-5", 10_000, 8_000)
    assert budget.settle(rid, {"input_tokens": 9_000, "output_tokens": 6_000}) == pytest.approx(0.078)
    with SessionLocal() as db:
        st = budget.status(db)
    assert st["spent_month_usd"] == pytest.approx(0.078) and st["monthly_cap_usd"] == 30.0
    # lần gọi xấu nhất vượt trần tháng → chặn TRƯỚC khi gọi, không ghi sổ
    with pytest.raises(budget.BudgetExceeded) as e:
        budget.reserve("bien_kich", "anthropic", "claude-opus-5-5", 10_000_000, 1_000)
    assert "tháng" in str(e.value) and "BLOCKED_BY_BUDGET" in str(e.value)
    rid2 = budget.reserve("bien_kich", "anthropic", "claude-sonnet-5-5", 100, 100)
    budget.release(rid2)
    with SessionLocal() as db:
        assert budget.status(db)["spent_month_usd"] == pytest.approx(0.078)


def test_daily_department_and_story_caps(settings, monkeypatch):
    from ev_studio.agents import budget

    # 30 USD/tháng chia đều số ngày còn lại → một lần gọi 3 USD có thể vượt trần ngày
    w = budget.window()
    allow = 30.0 / w.days_left
    tokens_for = int((allow + 0.5) / 10 * 1e6)  # output Sonnet $10/1M
    with pytest.raises(budget.BudgetExceeded) as e:
        budget.reserve("bien_kich", "anthropic", "claude-sonnet-5-5", 0, tokens_for)
    assert "ngày" in str(e.value) or "phòng ban" in str(e.value)
    # trần phòng ban: Ban Đào tạo chỉ có 3% của tháng = 0.9 USD
    monkeypatch.setattr(budget, "day_allowance", lambda db, w=None: 100.0)
    with pytest.raises(budget.BudgetExceeded) as e:
        budget.reserve("hoc_tap", "anthropic", "claude-sonnet-5-5", 0, 100_000)  # 1 USD
    assert "phòng ban" in str(e.value)
    # trần từng video
    s2 = settings.model_copy(update={"budget_story_max_usd": 0.05})
    monkeypatch.setattr(budget, "get_settings", lambda: s2)
    budget.settle(budget.reserve("bien_kich", "anthropic", "claude-sonnet-5-5", 0, 4_000, story_id="st-1"), {"output_tokens": 4_000})
    with pytest.raises(budget.BudgetExceeded) as e:
        budget.reserve("bien_kich", "anthropic", "claude-sonnet-5-5", 0, 2_000, story_id="st-1")
    assert "video" in str(e.value)
    budget.reserve("bien_kich", "anthropic", "claude-sonnet-5-5", 0, 2_000, story_id="st-2")  # video khác vẫn được


def test_can_start_story_limits_per_day(settings, db, monkeypatch):
    from ev_studio.agents import budget

    monkeypatch.setattr(budget, "story_count_today", lambda db: 3)
    ok, why = budget.can_start_story(db)
    assert not ok and "3/3" in why
    monkeypatch.setattr(budget, "story_count_today", lambda db: 1)
    assert budget.can_start_story(db)[0]
    monkeypatch.setattr(budget, "day_allowance", lambda db, w=None: 0.1)
    ok, why = budget.can_start_story(db)
    assert not ok and "hôm nay" in why


# ------------------------------------------------------------------ bộ não


def test_teacher_answer_is_remembered_and_reused(settings):
    from ev_studio.agents import budget
    from ev_studio.db import SessionLocal

    brain, fake = _brain(settings, ANSWER, usage=(800, 400, 1200, 0))
    r1, u1 = brain.ask(_task())
    assert u1["source"] == "anthropic" and len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5"  # Phòng Nghiên cứu dùng Sonnet
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}  # prompt caching
    assert u1["usd"] == pytest.approx((800 * 2 + 1200 * 2 * 1.25 + 400 * 10) / 1e6)
    r2, u2 = brain.ask(_task())
    assert r2 == r1 and u2["source"] == "memory" and u2["usd"] == 0 and len(fake.calls) == 1
    with SessionLocal() as db:
        assert budget.status(db)["spent_month_usd"] == pytest.approx(u1["usd"])


def test_claims_department_uses_cheap_model(settings):
    from ev_studio.agents.writer import AgentWriter

    payload = json.dumps({"claims": []})
    brain, fake = _brain(settings, payload)
    w = AgentWriter(settings, brain=brain)
    claims, usage = w.extract_claims("Tiêu đề", "Nội dung bài báo về trạm sạc")
    assert claims == [] and fake.calls[0]["model"] == "claude-haiku-5-5" and usage["provider"] == "anthropic"


def test_student_shadows_teacher_graduates_then_works_for_free(settings):
    from ev_studio.agents.brain import get_skill
    from ev_studio.db import SessionLocal

    student = FakeStudent({"answer": "Trạm sạc mới mở rộng mạng lưới cho người dùng xe điện tại Hà Nội"})
    brain, fake = _brain(settings, ANSWER, student=student)
    for i in range(3):
        brain.ask(_task(f"Đề bài {i}"))
    assert len(fake.calls) == 3 and student.calls == 3  # trò làm thử cùng đề, miễn phí
    with SessionLocal() as db:
        sk = get_skill(db, "nghien_cuu")
        assert sk.graduated and sk.student_passes == 3 and sk.similarity_avg > 0.9
    r, u = brain.ask(_task("Đề bài mới chưa từng gặp"))
    assert u["source"] == "local" and u["usd"] == 0 and len(fake.calls) == 3  # không gọi thầy nữa
    with SessionLocal() as db:
        sk = get_skill(db, "nghien_cuu")
        assert sk.student_live == 1 and sk.saved_usd > 0


def test_student_far_from_teacher_does_not_graduate_and_failures_demote(settings):
    from ev_studio.agents.brain import get_skill
    from ev_studio.db import SessionLocal

    student = FakeStudent({"answer": "Hôm nay trời đẹp, đi dạo công viên"})
    brain, fake = _brain(settings, ANSWER, student=student)
    for i in range(3):
        brain.ask(_task(f"Đề {i}"))
    with SessionLocal() as db:
        sk = get_skill(db, "nghien_cuu")
        assert not sk.graduated and sk.student_passes == 0
        from ev_studio.agents.memory import lessons

        assert any("khác thầy" in x for x in lessons(db, "nghien_cuu"))
        # tốt nghiệp rồi mà làm hỏng liên tiếp → bị hạ, việc quay về thầy
        sk.graduated, sk.recent = True, [1, 1, 1, 1, 1]
        db.commit()
    bad = FakeStudent({"wrong": True})
    brain.student = bad
    r, u = brain.ask(_task("Đề sau tốt nghiệp"))
    assert u["source"] == "anthropic"  # trò sai schema → thầy làm
    with SessionLocal() as db:
        assert get_skill(db, "nghien_cuu").recent[-1] == 0


def test_budget_exhausted_falls_back_to_student_or_stops(settings, monkeypatch):
    from ev_studio.agents import budget

    s2 = settings.model_copy(update={"budget_monthly_usd": 0.0001})
    monkeypatch.setattr(budget, "get_settings", lambda: s2)
    brain, fake = _brain(settings, ANSWER)
    with pytest.raises(budget.BudgetExceeded):
        brain.ask(_task("Hết tiền"))
    assert fake.calls == []  # không gọi Claude khi đã chạm trần
    student = FakeStudent({"answer": "Trạm sạc mới"})
    brain2, fake2 = _brain(settings, ANSWER, student=student)
    r, u = brain2.ask(_task("Hết tiền 2"))
    assert u["source"] == "local" and "ngân sách" in u["note"] and fake2.calls == []


def test_teacher_only_and_student_only_modes(settings, db):
    from ev_studio.agents.brain import get_skill
    from ev_studio.errors import GateBlocked

    sk = get_skill(db, "bien_tap")
    sk.mode = "student_only"
    db.commit()
    student = FakeStudent({"wrong": 1})
    brain, fake = _brain(settings, ANSWER, student=student)
    with pytest.raises(GateBlocked):
        brain.ask(_task("Sửa đoạn", dept="bien_tap"))
    assert fake.calls == []
    sk = get_skill(db, "bien_tap")
    db.refresh(sk)
    sk.mode = "teacher_only"
    db.commit()
    good = FakeStudent({"answer": "x"})
    brain2, fake2 = _brain(settings, ANSWER, student=good)
    brain2.ask(_task("Sửa đoạn 2", dept="bien_tap"))
    assert len(fake2.calls) == 1 and good.calls == 0  # teacher_only: trò không làm thử


def test_openai_teacher_and_alternate_strategy(settings, monkeypatch):
    from ev_studio.agents.brain import Brain
    from ev_studio.agents.teachers import OpenAITeacher
    from ev_studio.agents.writer import PerDeptAnthropic

    seen = []

    def handler(req: httpx.Request):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": ANSWER}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                                                   "prompt_tokens_details": {"cached_tokens": 400}}})

    s = settings.model_copy(update={"teacher_strategy": "alternate", "model_prices_json": '{"gpt-test": [1.0, 4.0]}'})
    from ev_studio.agents import budget

    monkeypatch.setattr(budget, "get_settings", lambda: s)
    oa = OpenAITeacher("sk-test", "gpt-test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    fake = SimpleNamespace(messages=FakeMessages(ANSWER))
    brain = Brain(s, {"anthropic": PerDeptAnthropic(s, "sk-ant-x", fake), "openai": oa}, None, rng=random.Random(1))
    _, u1 = brain.ask(_task("A"))
    _, u2 = brain.ask(_task("B"))
    assert {u1["provider"], u2["provider"]} == {"anthropic", "openai"}
    uo = u1 if u1["provider"] == "openai" else u2
    assert uo["input_tokens"] == 600 and uo["cache_read_tokens"] == 400
    assert uo["usd"] == pytest.approx((600 * 1 + 400 * 0.1 + 200 * 4) / 1e6)
    assert seen[0]["response_format"] == {"type": "json_object"}


def test_unknown_price_blocks_openai(settings, monkeypatch):
    from ev_studio.agents.brain import Brain
    from ev_studio.agents.pricing import UnknownPrice
    from ev_studio.agents.teachers import OpenAITeacher

    s = settings.model_copy(update={"teacher_strategy": "openai"})
    calls = []
    oa = OpenAITeacher("sk-test", "gpt-mystery", http=httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r))))
    with pytest.raises(UnknownPrice):
        Brain(s, {"openai": oa}).ask(_task("C"))
    assert calls == []


def test_learning_from_feedback(settings, db):
    from ev_studio.agents import learning, memory
    from ev_studio.agents.brain import build_context
    from ev_studio.agents.departments import DEPARTMENTS

    brain, fake = _brain(settings, ANSWER)
    brain.ask(_task("Kịch bản A", dept="bien_kich", story_id="story-x"))
    learning.on_script_qa_failed(db, "story-x", ["Không URL trong lời đọc: tìm thấy http"])
    learning.on_audio_measured(db, 1500, 600)
    db.commit()
    brain.ask(_task("Kịch bản A", dept="bien_kich", story_id="story-x"))
    assert len(fake.calls) == 2  # bản trượt QA không được bộ nhớ trả lại
    ctx = build_context(db, DEPARTMENTS["bien_kich"], "")
    assert "trượt QA" in ctx and "2.5 từ/giây" in ctx
    n = learning.remember_claims(db, "Tin A", [{"text": "Giá sạc giảm 20% đến 31/12", "status": "verified", "source_url": "https://a.vn"},
                                              {"text": "Tin đồn", "status": "unverified"}])
    db.commit()
    assert n == 1 and memory.search(db, "san_tin", "giá sạc giảm", kinds=["knowledge"])


def test_reflect_writes_style_guide_weekly(settings, db):
    from ev_studio.agents import learning, memory

    for t in ("Câu ngắn dễ đọc", "Luôn nêu ngày hiệu lực", "Không tâng bốc hãng"):
        memory.add_lesson(db, "bien_kich", t)
    db.commit()
    brain, fake = _brain(settings, json.dumps({"style_guide": "- Câu ngắn\n- Nêu ngày hiệu lực", "dropped": []}, ensure_ascii=False))
    r = learning.reflect(db, "bien_kich", brain)
    assert r["ran"] and fake.calls[0]["model"] == "claude-haiku-5-5"
    assert memory.style_guide(db, "bien_kich").startswith("- Câu ngắn")
    assert not learning.reflect(db, "bien_kich", brain)["ran"]  # chưa đủ 7 ngày


def test_agent_writer_script_end_to_end(settings):
    from ev_studio.agents.writer import AgentWriter
    from tests.test_writer_and_script_qa import good_output

    brain, fake = _brain(settings, json.dumps(good_output(), ensure_ascii=False))
    w = AgentWriter(settings, brain=brain)
    ctx = {"pillar": "P3", "core_question": "Ai được lợi?", "target_duration_seconds": 10, "story_id": "s-e2e",
           "research_pack": {"claims": [{"claim_key": "c1", "text": "ưu đãi đến 31/12", "status": "verified",
                                         "source_ids": ["s1"], "is_key": True}]}}
    out, usage = w.write_script(ctx)
    assert out["title_options"][0]["strategy"] == "question" and usage["usd"] > 0
    assert "TỔNG BIÊN TẬP" in fake.calls[0]["system"][0]["text"]


def test_budget_and_agents_api(clean, seeded):
    from fastapi.testclient import TestClient

    from ev_studio.api.main import app

    with TestClient(app) as c:
        b = c.get("/api/budget").json()
        assert b["monthly_cap_usd"] == 50.0 and "by_department" in b and "can_start_story" in b
        a = c.get("/api/agents").json()
        codes = {d["code"] for d in a["departments"]}
        assert {"san_tin", "nghien_cuu", "bien_kich", "bien_tap", "hoc_tap"} <= codes
        assert a["finance"]["name"] == "Ban Tài chính"
        r = c.post("/api/agents/bien_kich/mode", json={"mode": "teacher_only"})
        assert r.status_code == 200
        assert next(d for d in c.get("/api/agents").json()["departments"] if d["code"] == "bien_kich")["mode"] == "teacher_only"
        assert c.post("/api/agents/khong_co/mode", json={"mode": "auto"}).status_code == 404


def test_ingest_skips_offtopic_and_defers_when_budget_exhausted(settings, db, monkeypatch):
    from datetime import datetime, timezone

    from ev_studio.agents import budget
    from ev_studio.agents.writer import AgentWriter
    from ev_studio.models import BudgetLedger, CostUsage
    from ev_studio.services.ingest import ingest_articles
    from ev_studio.services.sources.fetcher import RawArticle

    now = datetime.now(timezone.utc)
    excerpt = "V-Green công bố mở rộng mạng lưới trạm sạc tại Hà Nội từ ngày 1/12."
    claims = {"claims": [{"claim_key": "c1", "text": "V-Green mở rộng mạng lưới trạm sạc tại Hà Nội",
                          "claim_type": "general", "evidence_excerpt": excerpt[:40], "is_key": True}]}
    brain, fake = _brain(settings, json.dumps(claims, ensure_ascii=False))
    w = AgentWriter(settings, brain=brain)
    arts = [RawArticle("Mở rộng trạm sạc Hà Nội (test ingest)", "https://vnexpress.net/test-ingest-tram-sac-1", "VnExpress", now, excerpt),
            RawArticle("Giá vàng hôm nay tăng mạnh (test ingest)", "https://vnexpress.net/test-ingest-vang-1", "VnExpress", now,
                       "Giá vàng miếng tăng 500 nghìn đồng một lượng sáng nay.")]
    stats = ingest_articles(db, arts, writer=w, actor="test")
    db.commit()
    assert len(fake.calls) == 1 and stats.get("skipped_offtopic") == 1  # tin lạc đề không tốn tiền
    assert db.query(CostUsage).filter(CostUsage.job_type == "ClaimExtraction").count() >= 1
    assert db.query(BudgetLedger).filter(BudgetLedger.department == "san_tin", BudgetLedger.status == "settled").count() == 1
    # hết ngân sách → bài bị hoãn (không lưu), lần quét sau làm tiếp
    s2 = settings.model_copy(update={"budget_monthly_usd": 0.0001})
    monkeypatch.setattr(budget, "get_settings", lambda: s2)
    more = [RawArticle("Pin xe điện bảo hành mới (test ingest)", "https://vnexpress.net/test-ingest-pin-2", "VnExpress", now,
                       "Chính sách bảo hành pin xe điện mới có hiệu lực từ tháng sau.")]
    stats2 = ingest_articles(db, more, writer=w, actor="test")
    db.commit()
    assert stats2.get("budget_skipped") == 1 and stats2["created"] == 0 and len(fake.calls) == 1
