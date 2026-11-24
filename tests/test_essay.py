"""Phòng Bài luận: research web miễn phí, QA bài luận, học từ Claude + ChatGPT, lên Bậc thầy tự viết không gọi thầy."""
import json
import random
import re
from types import SimpleNamespace

import httpx
import pytest

from test_agents_budget import FakeMessages, FakeStudent, clean, settings  # noqa: F401 - fixture dùng chung


# ------------------------------------------------------------------ tiện ích


def essay_from_prompt(user: str, words_per_source: int = 60, number: str | None = None) -> str:
    """Viết một bài luận 'tốt' chỉ từ HỒ SƠ TƯ LIỆU trong đề (giả lập thầy/trò làm đúng quy tắc)."""
    blocks = re.findall(r"^\[(S\d+)\] [^\n]*\n((?:- [^\n]*\n?)+)", user, re.M)
    parts = ["Bài viết này bàn về những điều người dùng cần cân nhắc khi chọn xe điện trong đô thị hiện nay."]
    for i, (sid, body) in enumerate(blocks[:5], start=1):
        text = " ".join(line[2:] for line in body.strip().splitlines())
        chunk = " ".join(text.split()[:words_per_source])
        if number and i == 2:
            chunk += f" Con số {number} cho thấy điều đó."
        parts.append(f"## Phần {i}\n\n{chunk} [{sid}]")
    parts.append("## Kết luận\n\nTuy nhiên mỗi người cần cân nhắc nhu cầu riêng trước khi quyết định chuyển sang xe điện.")
    md = "\n\n".join(parts)
    return json.dumps({"title": "Xe điện trong đô thị", "thesis": "Cân nhắc kỹ trước khi mua.", "outline": ["Mở bài"],
                       "essay_markdown": md, "sources_used": [b[0] for b in blocks[:5]]}, ensure_ascii=False)


def _user_of(kw):
    return kw["messages"][0]["content"]


@pytest.fixture()
def wipe_essays(db_ready):
    from ev_studio.db import SessionLocal
    from ev_studio.models import Essay

    with SessionLocal() as s:
        s.query(Essay).delete()
        s.commit()
    yield


# ------------------------------------------------------------------ research web


def test_page_fetcher_respects_robots_blocks_private_hosts_and_extracts_text():
    from ev_studio.services.research import PageFetcher

    seen = []
    article = ("<html><head><title>Pin xe điện</title></head><body><nav>Trang chủ Tin tức Xe điện Pin Sạc Liên hệ Đăng nhập ngay</nav>"
               "<script>var x = 'không được lấy đoạn này vào văn bản đâu nhé các bạn';</script>"
               + "".join(f"<p>Đoạn {i}: pin LFP bền hơn pin NMC và an toàn hơn về nhiệt khi xe đỗ dưới trời nắng nóng kéo dài.</p>"
                         for i in range(6))
               + "<footer>Bản quyền thuộc về tòa soạn, mọi hình thức sao chép phải ghi nguồn rõ ràng</footer></body></html>")

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(200, text=article, headers={"content-type": "text/html; charset=utf-8"})

    f = PageFetcher(http=httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    title, text = f.fetch("https://news.example.com/bai-viet")
    assert title == "Pin xe điện" and "pin LFP bền hơn" in text
    assert "không được lấy" not in text and "Trang chủ" not in text and "Bản quyền" not in text
    assert f.fetch("https://news.example.com/private/bai") is None  # robots.txt cấm
    n = len(seen)
    for url in ("http://localhost/a", "http://127.0.0.1/a", "http://192.168.1.5/a", "ftp://news.example.com/a"):
        assert f.fetch(url) is None
    assert len(seen) == n  # địa chỉ nội bộ: không gửi request nào


def test_wikipedia_search_reads_official_api_and_skips_disambiguation():
    from ev_studio.services.research import WikipediaSearch

    long = "Xe điện là loại xe dùng động cơ điện. " * 20

    def handler(req: httpx.Request):
        p = dict(req.url.params)
        assert "EVCreatorStudio" in req.headers["user-agent"]
        if p.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"pageid": 1, "title": "Xe điện", "snippet": "<b>xe</b> điện"},
                                                                  {"pageid": 2, "title": "Xe (định hướng)", "snippet": ""}]}})
        if p["pageids"] == "1":
            return httpx.Response(200, json={"query": {"pages": [{"pageid": 1, "title": "Xe điện", "extract": long,
                                                                  "fullurl": "https://vi.wikipedia.org/wiki/Xe_điện"}]}})
        return httpx.Response(200, json={"query": {"pages": [{"pageid": 2, "title": "Xe", "extract": long,
                                                              "pageprops": {"disambiguation": ""}}]}})

    ws = WikipediaSearch(["vi"], "EVCreatorStudio/1.0 (test)", http=httpx.Client(transport=httpx.MockTransport(handler)))
    hits = ws.search("xe điện", 3)
    assert len(hits) == 1 and hits[0].text.startswith("Xe điện là") and hits[0].license == "CC BY-SA 4.0"
    assert hits[0].snippet == "xe điện"


def test_dossier_from_fixture_numbers_sources_and_keeps_relevant_passages():
    from ev_studio.services.research import FixtureSearch, build_dossier, plan_queries

    q = plan_queries("Có nên mua xe điện ở Việt Nam không?")
    assert q[0] == "mua xe điện ở Việt Nam" and len(q) >= 4
    d = build_dossier("Có nên mua xe điện ở Việt Nam không?", "so sánh chi phí", q, [FixtureSearch()])
    assert [s.id for s in d.sources] == [f"S{i}" for i in range(1, len(d.sources) + 1)] and len(d.sources) >= 4
    assert d.is_demo and "[S1]" in d.to_prompt()
    assert any("chi phí" in s.title.lower() for s in d.sources)


# ------------------------------------------------------------------ QA bài luận


def _dossier():
    from ev_studio.services.research import FixtureSearch, build_dossier, plan_queries

    t = "Xe điện trong đô thị"
    return build_dossier(t, "", plan_queries(t), [FixtureSearch()])


def test_essay_qa_accepts_grounded_essay_and_catches_fabrication():
    from ev_studio.agents.essay import EssayRequest, analyze, validate_essay

    d = _dossier()
    req = EssayRequest("Xe điện trong đô thị", words=330)
    good = validate_essay(json.loads(essay_from_prompt(d.to_prompt() + "\n")))
    r = analyze(good, d, req)
    assert r["ok"], r["problems"]
    assert r["metrics"]["grounding"] > 0.8 and r["metrics"]["distinct_sources"] >= 3

    bad_num = validate_essay(json.loads(essay_from_prompt(d.to_prompt() + "\n", number="98765")))
    assert any("98765" in p for p in analyze(bad_num, d, req)["problems"])  # số liệu bịa

    fake_src = dict(good, essay_markdown=good["essay_markdown"].replace("[S2]", "[S9]"))
    assert any("S9" in p for p in analyze(fake_src, d, req)["problems"])  # nguồn bịa

    invented = dict(good, essay_markdown=good["essay_markdown"].replace(
        good["essay_markdown"].split("## Phần 2\n\n")[1].split(" [S")[0],
        "Robot ngoài hành tinh thích nhạc jazz cổ điển và bánh quy socola vào mỗi buổi sáng thứ hai tại sao Hoả "
        "trong khi các nhà thiên văn tranh cãi về màu sắc của bầu trời trên những hành tinh xa xôi bên ngoài thiên hà."))
    assert any("bám nguồn" in p or "đoạn" in p for p in analyze(invented, d, req)["problems"] + analyze(invented, d, req)["warnings"])

    short = EssayRequest("Xe điện trong đô thị", words=1500)
    assert any("độ dài" in p for p in analyze(good, d, short)["problems"])
    meta = dict(good, essay_markdown="Dưới đây là bài luận bạn yêu cầu.\n\n" + good["essay_markdown"])
    assert any("meta" in p for p in analyze(meta, d, req)["problems"])
    # [S1, S2] được chuẩn hoá, tiêu đề '# ...' trong markdown được tách ra
    v = validate_essay({"essay_markdown": "# Tiêu đề\n\n" + "Nội dung có dấu tiếng Việt [S1, S2]. " * 20})
    assert v["title"] == "Tiêu đề" and "[S1][S2]" in v["essay_markdown"]


def test_master_level_thresholds(settings):
    from ev_studio.agents.departments import LEVELS, MASTER, level_of

    s = settings.model_copy(update={"agent_master_min_passes": 5, "agent_master_rate": 0.95})
    assert LEVELS[MASTER] == "Bậc thầy"
    assert level_of([1] * 5, 5, True, s) == MASTER
    assert level_of([1, 1, 1, 1, 0], 5, True, s) < MASTER  # 80% < 95%
    assert level_of([1] * 5, 5, False, s) < MASTER  # chưa tốt nghiệp
    assert level_of([1] * 3, 5, True, s) < MASTER  # chưa đủ cửa sổ đánh giá


def test_budget_shares_include_essay_department_and_sum_to_one():
    from ev_studio.agents.budget import DEPARTMENT_SHARES
    from ev_studio.agents.departments import DEPARTMENTS

    assert set(DEPARTMENT_SHARES) == set(DEPARTMENTS)
    assert sum(DEPARTMENT_SHARES.values()) == pytest.approx(1.0)


# ------------------------------------------------------------------ học từ Claude + ChatGPT → Bậc thầy


def _two_teacher_brain(s, student):
    from ev_studio.agents.brain import Brain
    from ev_studio.agents.teachers import OpenAITeacher
    from ev_studio.agents.writer import PerDeptAnthropic

    oa_calls = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        oa_calls.append(body)
        user = body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": essay_from_prompt(user)}}],
                                         "usage": {"prompt_tokens": 3000, "completion_tokens": 900}})

    oa = OpenAITeacher("sk-test", "gpt-test", http=httpx.Client(transport=httpx.MockTransport(handler)))
    fake = SimpleNamespace(messages=FakeMessages(lambda kw: essay_from_prompt(_user_of(kw)), usage=(3000, 900, 0, 0)))
    brain = Brain(s, {"anthropic": PerDeptAnthropic(s, "sk-ant-x", fake), "openai": oa}, student, rng=random.Random(3))
    return brain, fake.messages, oa_calls


def test_essay_department_learns_from_claude_and_chatgpt_then_writes_alone_as_master(settings, monkeypatch):
    from ev_studio.agents import budget
    from ev_studio.agents.brain import get_skill
    from ev_studio.agents.departments import MASTER, level_of
    from ev_studio.agents.essay import EssayRequest, EssayWriter, teacher_styles
    from ev_studio.db import SessionLocal
    from ev_studio.services.research import FixtureSearch

    s = settings.model_copy(update={
        "essay_teacher_strategy": "alternate", "model_prices_json": '{"gpt-test": [1.0, 4.0]}',
        "agent_audit_rate": 1.0,  # đã tốt nghiệp nhưng chưa Bậc thầy → vẫn gửi thầy mọi bài (để thấy rõ khác biệt)
        "agent_master_min_passes": 5, "agent_master_rate": 0.95, "agent_master_audit_rate": 0.0,
    })
    monkeypatch.setattr(budget, "get_settings", lambda: s)
    jobs = {"plan": 0, "essay": 0}

    def student_answer(system, user):
        if "kế hoạch tìm kiếm" in system:  # trò cũng lập kế hoạch tìm kiếm (miễn phí)
            jobs["plan"] += 1
            return {"queries": ["chi phí xe điện", "trạm sạc chung cư"]}
        jobs["essay"] += 1
        return json.loads(essay_from_prompt(user))

    student = FakeStudent(student_answer)
    brain, claude, chatgpt = _two_teacher_brain(s, student)
    w = EssayWriter(s, brain=brain, searchers=[FixtureSearch()], fetcher=None)
    providers = []
    for i in range(5):
        req = EssayRequest(f"Xe điện trong đô thị, góc nhìn {i}", words=330)
        essay, usage, qa = w.write(req, w.research(req))
        assert qa["ok"], qa["problems"]
        providers.append(usage["provider"])
    assert set(providers) == {"anthropic", "openai"}  # học cả Claude lẫn ChatGPT
    assert jobs == {"plan": 5, "essay": 5}  # mỗi bài của thầy, trò viết thử cùng đề (miễn phí)
    with SessionLocal() as db:
        sk = get_skill(db, "bai_luan")
        assert sk.graduated and level_of(list(sk.recent), sk.student_passes, sk.graduated, s) == MASTER
        st = teacher_styles(db)
        assert {"anthropic", "openai"} <= set(st) and st["anthropic"]["essays"] >= 2
    # tốt nghiệp từ bài thứ 3 nhưng audit 100% → bài 4, 5 vẫn do thầy viết; Bậc thầy thì không
    n_teacher = len(claude.calls) + len(chatgpt)
    req = EssayRequest("Trạm sạc ở chung cư", words=330)
    essay, usage, qa = w.write(req, w.research(req))
    assert usage["source"] == "local" and usage["usd"] == 0 and usage["level"] == MASTER and qa["ok"]
    assert len(claude.calls) + len(chatgpt) == n_teacher  # không gọi Claude/ChatGPT
    # trò nhận bài mẫu của thầy + mục tiêu cấu trúc rút ra từ bài của thầy
    assert "MỤC TIÊU CẤU TRÚC" in student.last_system and "BÀI LUẬN MẪU" in student.last_system


def test_master_student_failing_qa_falls_back_to_teacher(settings, monkeypatch):
    from ev_studio.agents import budget
    from ev_studio.agents.brain import get_skill
    from ev_studio.agents.essay import EssayRequest, EssayWriter
    from ev_studio.db import SessionLocal
    from ev_studio.services.research import FixtureSearch

    s = settings.model_copy(update={"essay_teacher_strategy": "anthropic", "agent_master_min_passes": 5})
    monkeypatch.setattr(budget, "get_settings", lambda: s)
    with SessionLocal() as db:
        sk = get_skill(db, "bai_luan")
        sk.graduated, sk.recent, sk.student_passes = True, [1] * 5, 50
        db.commit()
    liar = FakeStudent(lambda system, user: json.loads(essay_from_prompt(user, number="98765")))
    brain, claude, _ = _two_teacher_brain(s, liar)
    w = EssayWriter(s, brain=brain, searchers=[FixtureSearch()])
    req = EssayRequest("Pin xe điện", words=330)
    essay, usage, qa = w.write(req, w.research(req))
    # lập kế hoạch + tự viết (bịa số → QA chặn) → thầy viết → trò học thử lại cùng đề
    assert liar.calls == 3 and usage["source"] == "anthropic" and qa["ok"]
    with SessionLocal() as db:
        assert get_skill(db, "bai_luan").recent[-1] == 0  # bị trừ điểm, có thể bị hạ cấp


# ------------------------------------------------------------------ API


def test_essay_api_demo_flow_review_and_learning(clean, seeded, wipe_essays):  # noqa: F811
    from fastapi.testclient import TestClient

    from ev_studio.agents.memory import lessons
    from ev_studio.api.main import app
    from ev_studio.db import SessionLocal
    from ev_studio.models import AgentMemory

    with TestClient(app) as c:
        r = c.post("/api/essays", json={"topic": "Có nên mua xe điện ở Việt Nam không?", "requirements": "nói về chi phí",
                                        "words": 400, "wait": True})
        assert r.status_code == 200, r.text
        e = r.json()
        # không có Claude/ChatGPT/Ollama trong test → bản nháp DEMO ghép từ tư liệu giả lập
        assert e["status"] in ("done", "needs_review") and e["source"] == "demo" and e["is_demo"]
        assert e["dossier"]["sources"] and "[S1]" in e["markdown"] and e["usd"] == 0
        assert any(x["id"] == e["id"] for x in c.get("/api/essays").json())
        md = c.get(f"/api/essays/{e['id']}/export.md")
        assert md.status_code == 200 and "## Nguồn tham khảo" in md.text and "demo.invalid" in md.text
        assert c.post(f"/api/essays/{e['id']}/review", json={"action": "feedback", "note": ""}).status_code == 422
        c.post(f"/api/essays/{e['id']}/review", json={"action": "feedback", "note": "Mở bài cần nêu luận điểm rõ hơn"})
        edited = e["markdown"] + "\n\nĐoạn chủ kênh thêm vào."
        r = c.post(f"/api/essays/{e['id']}/review", json={"action": "approve", "markdown": edited})
        assert r.json()["status"] == "approved" and r.json()["markdown"].endswith("Đoạn chủ kênh thêm vào.")
        with SessionLocal() as db:
            assert any("luận điểm rõ hơn" in x for x in lessons(db, "bai_luan", 20))
            owner = db.query(AgentMemory).filter_by(department="bai_luan", source="owner", kind="answer").one()
            assert owner.approved and owner.response["essay_markdown"].endswith("Đoạn chủ kênh thêm vào.")
        a = c.get("/api/agents").json()
        d = next(x for x in a["departments"] if x["code"] == "bai_luan")
        assert d["name"] == "Phòng Bài luận" and "styles" in d and "owner" in d["styles"]
        assert a["graduation"]["master_audit_rate"] == 0.0
        assert c.get("/api/essays/khong-co").status_code == 404
        assert c.post("/api/essays", json={"topic": "x"}).status_code == 422
