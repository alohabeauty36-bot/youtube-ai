"""Prompt Mục III đọc từ file, Claude JSON schema, quality gates kịch bản."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ev_studio.errors import BlockedByCredentials, RetryableError
from ev_studio.services.claude_writer import load_prompts, render_developer_message
from ev_studio.services.script_qa import run_script_qa

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = [{"claim_key": "c1", "text": "Ưu đãi đến 31/12", "status": "verified", "source_ids": ["s1"], "is_key": True},
          {"claim_key": "c2", "text": "Có 150 trạm đang vận hành", "status": "contested", "source_ids": ["s2"], "value": "150", "is_key": True}]


def good_output(**over):
    spoken = ("Có một điều kiện nhỏ quyết định tất cả. Theo hãng, ưu đãi đến 31/12.\n\n"
              "Phần còn lại là phân tích của chúng ta. Ở video tiếp theo, ta sẽ xem điều gì ở lại.")
    out = {"pillar": "P3", "source_ids_used": ["s1"], "main_angle": "Điều kiện ưu đãi",
           "title_options": [{"title": "Ưu đãi sạc: ai thực sự được lợi?", "strategy": "question"},
                             {"title": "Ưu đãi nghe hấp dẫn nhưng điều kiện mới là chính", "strategy": "paradox"},
                             {"title": "3 điều cần kiểm tra trước khi sạc", "strategy": "practical"}],
           "selected_title_index": 0, "spoken_script": spoken,
           "closing_teaser": "Ở video tiếp theo, ta sẽ xem điều gì ở lại.",
           "editorial_claim_map": [{"claim_id": "c1", "source_ids": ["s1"], "relevant_script_excerpt": "ưu đãi đến 31/12"}],
           "production_notes": {"key_on_screen_facts": [{"text": "Ưu đãi đến 31/12", "claim_id": "c1"}]},
           "unresolved_questions_for_editor": []}
    out.update(over)
    return out


def test_prompt_file_is_section_III_of_spec():
    spec = (ROOT / "docs" / "EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md").read_text(encoding="utf-8")
    prompt = (ROOT / "prompts" / "youtube_script_10min.md").read_text(encoding="utf-8")
    assert prompt.strip() in spec
    b = load_prompts()
    assert "TỔNG BIÊN TẬP độc lập" in b.system and "spoken_script" in b.system
    assert "{{verified_research_pack_json}}" in b.developer_template
    assert "{{core_question}}" in b.user_template


def test_developer_message_fills_placeholders():
    b = load_prompts()
    msg = render_developer_message(b, {"pillar": "P2", "research_pack": {"x": 1}, "actual_audio_seconds": 760,
                                       "target_duration_seconds": 600})
    assert "{{" not in msg and "PILLAR: P2" in msg and "ACTUAL_AUDIO_SECONDS: 760" in msg


class FakeMessages:
    def __init__(self, text, stop="end_turn"):
        self.text, self.stop, self.calls = text, stop, []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], stop_reason=self.stop,
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=3000))


def _writer(text, stop="end_turn"):
    from ev_studio.config import get_settings
    from ev_studio.services.claude_writer import AnthropicWriter

    s = get_settings().model_copy(update={"anthropic_api_key": __import__("pydantic").SecretStr("sk-ant-test-xxxxxxxx")})
    fake = SimpleNamespace(messages=FakeMessages(text, stop))
    return AnthropicWriter(s, client=fake), fake


def test_anthropic_writer_validates_json_and_uses_prompt_file():
    w, fake = _writer("```json\n" + json.dumps(good_output(), ensure_ascii=False) + "\n```")
    out, usage = w.write_script({"pillar": "P3", "research_pack": {}, "core_question": "Ai được lợi?"})
    assert out["title_options"][0]["strategy"] == "question" and usage["output_tokens"] == 3000
    call = fake.messages.calls[0]
    assert "TỔNG BIÊN TẬP" in call["system"] and "Hồ sơ trụ cột P3" in call["system"]
    assert "“Ai được lợi?”" in call["messages"][0]["content"]


def test_anthropic_writer_rejects_bad_schema_and_truncation():
    w, _ = _writer(json.dumps(good_output(title_options=[{"title": "Chỉ một tiêu đề thôi", "strategy": "x"}])))
    with pytest.raises(RetryableError):
        w.write_script({"pillar": "P3", "research_pack": {}, "core_question": "?"})
    w, _ = _writer("{}", stop="max_tokens")
    with pytest.raises(RetryableError):
        w.write_script({"pillar": "P3", "research_pack": {}, "core_question": "?"})


def test_missing_key_blocks_in_production(monkeypatch):
    from ev_studio import providers
    from ev_studio.config import get_settings

    s = get_settings().model_copy(update={"app_mode": "production", "anthropic_api_key": None})
    with pytest.raises(BlockedByCredentials):
        providers.get_writer(s)
    s2 = get_settings().model_copy(update={"app_mode": "production", "tts_provider": "demo"})
    with pytest.raises(BlockedByCredentials):
        providers.get_tts(s2)


def qa(out, **kw):
    return run_script_qa(out, pack_claims=CLAIMS, source_texts=["nguồn gốc ngắn"], recent_scripts=[], **kw)


def test_script_qa_pass():
    assert qa(good_output()).passed


@pytest.mark.parametrize("mutate,expect", [
    ({"spoken_script": "[CẢNH: xe chạy] Theo hãng, ưu đãi đến 31/12. Ở video tiếp theo, ta sẽ xem điều gì ở lại."}, "chỉ dẫn dựng"),
    ({"spoken_script": "Xem https://x.vn. Ở video tiếp theo, ta sẽ xem điều gì ở lại."}, "URL"),
    ({"spoken_script": "Phút 2:30 nói về giá. Ở video tiếp theo, ta sẽ xem điều gì ở lại."}, "timestamp"),
    ({"editorial_claim_map": [{"claim_id": "c2", "source_ids": ["s2"], "relevant_script_excerpt": "150 trạm"}]}, "Claim map"),
    ({"editorial_claim_map": [{"claim_id": "c99", "source_ids": ["s1"], "relevant_script_excerpt": "?"}]}, "Claim map"),
    ({"title_options": [{"title": "Sạc miễn phí vĩnh viễn cho mọi người", "strategy": "paradox"},
                        {"title": "Tiêu đề thứ hai đủ dài", "strategy": "q"}, {"title": "Tiêu đề thứ ba đủ dài", "strategy": "p"}]}, "quá mức"),
    ({"closing_teaser": "Một câu không nằm cuối bài."}, "closing_teaser"),
    ({"spoken_script": "", "unresolved_questions_for_editor": ["Hai báo bất đồng số trạm"]}, "rỗng"),
])
def test_script_qa_blocks(mutate, expect):
    r = qa(good_output(**mutate))
    assert not r.passed
    assert any(expect.lower() in (c["name"] + c["detail"]).lower() for c in r.checks if not c["ok"])


def test_contested_claim_text_cannot_leak():
    out = good_output(spoken_script="Theo báo, có 150 trạm đang vận hành. Ở video tiếp theo, ta sẽ xem điều gì ở lại.")
    assert not qa(out).passed


def test_demo_writer_respects_actual_audio_seconds():
    from ev_studio.services.claude_writer import DemoWriter

    w = DemoWriter()
    pack = {"claims": [{"claim_key": f"c{i}", "text": f"Dữ kiện số {i} đã xác minh", "status": "verified", "source_ids": ["s1"]} for i in range(1, 4)],
            "sources": [{"source_id": "s1", "publisher": "Nguồn A"}], "editorial_angle": "Góc nhìn", "topic": "Chủ đề"}
    a, _ = w.write_script({"pillar": "P1", "research_pack": pack, "core_question": "?", "target_duration_seconds": 600})
    b, _ = w.write_script({"pillar": "P1", "research_pack": pack, "core_question": "?", "target_duration_seconds": 600,
                           "actual_audio_seconds": 760, "previous_spoken_script": a["spoken_script"]})
    assert len(b["spoken_script"].split()) < len(a["spoken_script"].split())
    assert all(e["claim_id"] in {"c1", "c2", "c3"} for e in b["editorial_claim_map"])
