"""Claude writer 2 bước: ResearchPack → kịch bản 10 phút (JSON Mục III).

Prompt đọc từ `prompts/youtube_script_10min.md` (copy nguyên Mục III), KHÔNG hard-code trong Python.
`DemoWriter` là bộ viết xác định (deterministic) GẮN NHÃN DEMO, dùng khi chưa có ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from ..config import Settings, get_settings
from ..errors import BlockedByCredentials, PermanentError, RetryableError
from ..schemas import ExtractedClaims, ResearchPackLLM, ScriptOutput

# ------------------------------------------------------------------ prompts


@dataclass
class PromptBundle:
    system: str
    developer_template: str
    user_template: str
    research_system: str
    claim_system: str
    pillar_profiles: dict[str, str]
    sha256: str


def _section(md: str, start: str, end: str | None) -> str:
    i = md.find(start)
    if i == -1:
        raise PermanentError(f"Prompt thiếu mục '{start}'")
    j = md.find(end, i + len(start)) if end else -1
    return md[i + len(start): j if j != -1 else None].strip()


def load_prompts(prompts_dir: Path | None = None) -> PromptBundle:
    d = prompts_dir or get_settings().prompts_dir
    md = (d / "youtube_script_10min.md").read_text(encoding="utf-8")
    system = _section(md, "## SYSTEM PROMPT", "## DEVELOPER MESSAGE")
    dev = _section(md, "## DEVELOPER MESSAGE (BACKEND ĐƯA DỮ LIỆU)", "## QUY TẮC OUTPUT")
    dev = re.sub(r"^```\w*\n|```$", "", dev.strip(), flags=re.M).strip()
    output_rules = _section(md, "## QUY TẮC OUTPUT — JSON HỢP LỆ", "## USER TASK TEMPLATE")
    user = _section(md, "## USER TASK TEMPLATE", "---")
    user = user.strip().strip('"').strip("“”")
    profiles = {p: (d / "pillars" / f"{p}.md").read_text(encoding="utf-8") for p in ("P1", "P2", "P3")}
    sha = hashlib.sha256((md + "".join(profiles.values())).encode()).hexdigest()
    return PromptBundle(
        system=system + "\n\n## QUY TẮC OUTPUT — JSON HỢP LỆ\n" + output_rules,
        developer_template=dev, user_template=user,
        research_system=(d / "research_pack.md").read_text(encoding="utf-8"),
        claim_system=(d / "claim_extraction.md").read_text(encoding="utf-8"),
        pillar_profiles=profiles, sha256=sha,
    )


def render_developer_message(bundle: PromptBundle, ctx: dict[str, Any]) -> str:
    tz = ZoneInfo(get_settings().timezone)
    values = {
        "current_date_asia_ho_chi_minh": datetime.now(tz).strftime("%Y-%m-%d %H:%M (Asia/Ho_Chi_Minh)"),
        "channel_profile": ctx.get("channel_profile", "Kênh độc lập về xe điện tại Việt Nam, không phải kênh chính thức của hãng nào."),
        "P1_or_P2_or_P3": ctx["pillar"],
        "verified_research_pack_json": json.dumps(ctx["research_pack"], ensure_ascii=False),
        "claims_to_avoid_json": json.dumps(ctx.get("claims_to_avoid", []), ensure_ascii=False),
        "recent_video_titles_and_angles_json": json.dumps(ctx.get("recent_topics", []), ensure_ascii=False),
        "host_voice_guide": ctx.get("brand_voice", "Người kể từng trải, gần gũi, cân bằng, không tâng bốc."),
        "user_editorial_notes_or_empty": ctx.get("special_requests", ""),
        "optional_from_tts_on_revision": str(ctx.get("actual_audio_seconds") or ""),
    }
    out = bundle.developer_template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    target = ctx.get("target_duration_seconds", 600)
    out = out.replace("TARGET_DURATION_SECONDS: 600", f"TARGET_DURATION_SECONDS: {int(target)}")
    if ctx.get("previous_spoken_script") and ctx.get("actual_audio_seconds"):
        out += f"\nPREVIOUS_SPOKEN_SCRIPT_WORDS: {len(ctx['previous_spoken_script'].split())}"
        out += f"\nPREVIOUS_SPOKEN_SCRIPT: {json.dumps(ctx['previous_spoken_script'], ensure_ascii=False)}"
    return out


def render_user_task(bundle: PromptBundle, core_question: str) -> str:
    return bundle.user_template.replace("{{core_question}}", f"“{core_question}”")


# ------------------------------------------------------------------ providers


class WriterProvider(Protocol):
    name: str
    is_demo: bool

    def extract_claims(self, title: str, text: str) -> tuple[list[dict], dict]: ...
    def research_pack(self, ctx: dict) -> tuple[dict, dict]: ...
    def write_script(self, ctx: dict) -> tuple[dict, dict]: ...
    def rewrite_paragraph(self, ctx: dict, paragraph: str, instruction: str) -> tuple[str, dict]: ...


def _validate_script(raw: dict) -> dict:
    try:
        return ScriptOutput.model_validate(raw).model_dump()
    except ValidationError as e:
        raise RetryableError(f"JSON kịch bản sai schema: {e.errors()[:3]}") from e


class AnthropicWriter:
    name = "anthropic"
    is_demo = False

    def __init__(self, settings: Settings, client: Any = None):
        from .llm import AnthropicJSONClient

        key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
        self.llm = AnthropicJSONClient(key, settings.anthropic_model, settings.anthropic_max_tokens, client=client)
        self.bundle = load_prompts()
        self.model = settings.anthropic_model

    def extract_claims(self, title, text):
        raw, usage = self.llm.complete_json(self.bundle.claim_system, f"TIÊU ĐỀ: {title}\n\nVĂN BẢN:\n{text}", 4000)
        try:
            return [c.model_dump() for c in ExtractedClaims.model_validate(raw).claims], usage
        except ValidationError as e:
            raise RetryableError(f"Claim JSON sai schema: {e.errors()[:2]}") from e

    def research_pack(self, ctx):
        user = json.dumps({k: ctx[k] for k in ("news", "sources", "claims", "recent_topics", "pillar")}, ensure_ascii=False)
        raw, usage = self.llm.complete_json(self.bundle.research_system, user, 6000)
        try:
            return ResearchPackLLM.model_validate(raw).model_dump(), usage
        except ValidationError as e:
            raise RetryableError(f"ResearchPack sai schema: {e.errors()[:2]}") from e

    def write_script(self, ctx):
        system = self.bundle.system + "\n\n" + self.bundle.pillar_profiles[ctx["pillar"]]
        user = render_developer_message(self.bundle, ctx) + "\n\n" + render_user_task(self.bundle, ctx["core_question"])
        raw, usage = self.llm.complete_json(system, user)
        return _validate_script(raw), usage

    def rewrite_paragraph(self, ctx, paragraph, instruction):
        system = (self.bundle.system + "\n\nNHIỆM VỤ PHỤ: viết lại MỘT đoạn lời đọc. Trả JSON {\"paragraph\": \"...\"}. "
                  "Giữ nguyên mọi dữ kiện có claim, không thêm dữ kiện mới ngoài RESEARCH_PACK.")
        user = render_developer_message(self.bundle, ctx) + f"\n\nĐOẠN CẦN SỬA:\n{paragraph}\n\nYÊU CẦU SỬA: {instruction}"
        raw, usage = self.llm.complete_json(system, user, 2000)
        p = str(raw.get("paragraph", "")).strip()
        if not p:
            raise RetryableError("Claude trả đoạn rỗng")
        return p, usage


# ------------------------------------------------------------------ DEMO writer

DEMO_DISCLAIMER = ("Trước khi bắt đầu, xin lưu ý: đây là bản dựng thử nghiệm với dữ liệu giả lập. "
                   "Mọi tên doanh nghiệp, mẫu xe và con số trong video này chỉ là ví dụ để kiểm tra hệ thống.")


def _fact_sentence(c: dict, src: dict[str, dict]) -> str:
    s = src.get((c.get("source_ids") or ["s1"])[0], {})
    pub = s.get("publisher", "nguồn đã xác minh")
    t = c["text"]
    first = t.split()[0] if t.split() else ""
    if first[1:].islower():  # chỉ hạ chữ đầu với từ thường, giữ nguyên tên riêng/viết tắt (DEMO-A5, VF 3…)
        t = t[0].lower() + t[1:]
    return f"Theo {pub}, {t.rstrip('.')}."


class DemoWriter:
    """Viết xác định từ ResearchPack. KHÔNG phải Claude. Mọi output mang nhãn is_demo."""
    name = "demo"
    is_demo = True
    model = "demo-writer-v1"
    WORDS_PER_SECOND = 2.42

    def extract_claims(self, title, text):
        return [], {"input_tokens": 0, "output_tokens": 0, "model": self.model}

    def research_pack(self, ctx):
        claims = [c for c in ctx["claims"] if c["status"] == "verified"]
        topic = ctx["news"]["title"].replace("[DEMO] ", "")
        pack = {
            "editorial_angle": f"Điều kiện nào quyết định giá trị thật của: {topic}?",
            "why_now": "Tin mới công bố, có ngày hiệu lực và điều kiện áp dụng rõ ràng.",
            "what_changed": claims[0]["text"] if claims else "Chưa có thay đổi đã xác minh.",
            "counterarguments": ["Lợi ích có thể chỉ đến với một nhóm người dùng nhất định.",
                                 "Thông báo chưa đồng nghĩa với kết quả thực tế đã được kiểm chứng độc lập."],
            "risk_notes": [f"Không dùng claim {c['claim_key']} ({c['status']})" for c in ctx["claims"] if c["status"] in ("unverified", "contested")],
            "source_ids": sorted({s for c in claims for s in c.get("source_ids", [])}) or ["s1"],
            "recommended_pillar": ctx["pillar"],
            "hook_options": [f"Nghe thì có lợi, nhưng ai thực sự được hưởng từ {topic.lower()}?",
                             "Một điều kiện nhỏ trong thông báo có thể quyết định bạn được lợi hay không.",
                             "Trước khi vui mừng, hãy xem ngày hết hạn và đối tượng áp dụng."],
            "video_unique_value": "Tách bạch điều kiện, phép tính ví dụ có giả định công khai và tác động theo từng nhóm người dùng.",
            "core_question": f"{topic}: người dùng thực sự được lợi ở đâu và cần lưu ý điều gì?",
            "blocking_issues": [],
        }
        return pack, {"input_tokens": 0, "output_tokens": 0, "model": self.model}

    def write_script(self, ctx):
        pack = ctx["research_pack"]
        pillar = ctx["pillar"]
        verified = [c for c in pack["claims"] if c["status"] == "verified"]
        inferences = [c for c in pack["claims"] if c["status"] == "inference"]
        src = {s["source_id"]: s for s in pack["sources"]}
        topic = pack.get("topic", "chủ đề hôm nay")
        q = ctx["core_question"]
        target_s = ctx.get("target_duration_seconds", 600)
        if ctx.get("actual_audio_seconds") and ctx.get("previous_spoken_script"):
            prev_words = len(ctx["previous_spoken_script"].split())
            target_words = int(prev_words * target_s / float(ctx["actual_audio_seconds"]))
        else:
            target_words = int(target_s * self.WORDS_PER_SECOND)
        if not verified:
            out = self._empty(pillar, "Không có claim nào đã xác minh trong RESEARCH_PACK")
            return out, {"input_tokens": 0, "output_tokens": 0, "model": self.model}

        facts = [_fact_sentence(c, src) for c in verified]
        paras = _demo_paragraphs(pillar, topic, q, facts, inferences)
        teaser = ("Ở video tiếp theo, chúng ta sẽ đặt câu hỏi ngược lại: khi ưu đãi kết thúc, "
                  "điều gì còn ở lại với người dùng?")
        # chọn đoạn theo ưu tiên (đoạn có dữ kiện → mở/kết → bắt buộc → tuỳ chọn) tới khi đủ số từ mục tiêu
        def prio(p):
            has_fact = any(f and f in p[1] for f in facts)
            return (0 if has_fact else 1 if p[2] in (10, 60) else 2 if p[0] else 3, p[2])

        chosen = []
        words = len(DEMO_DISCLAIMER.split()) + len(teaser.split())
        for p in sorted(paras, key=prio):
            n = len(p[1].split())
            if prio(p)[0] <= 1 or words + n <= target_words:
                chosen.append(p)
                words += n
        chosen.sort(key=lambda p: p[2])
        body = [DEMO_DISCLAIMER] + [p[1] for p in chosen] + [teaser]
        spoken = "\n\n".join(body)
        claim_map = []
        for c, f in zip(verified, facts):
            if f in spoken:
                claim_map.append({"claim_id": c["claim_key"], "source_ids": c["source_ids"], "relevant_script_excerpt": f})
        out = {
            "pillar": pillar,
            "source_ids_used": sorted({s for c in verified for s in c["source_ids"]}),
            "main_angle": pack.get("editorial_angle", q),
            "title_options": _demo_titles(pillar, topic),
            "selected_title_index": 0,
            "spoken_script": spoken,
            "closing_teaser": teaser,
            "editorial_claim_map": claim_map,
            "production_notes": {
                "visual_concepts": ["Đồ họa so sánh điều kiện áp dụng", "Đường thời gian hiệu lực", "Bảng lợi ích / rủi ro"],
                "key_on_screen_facts": [{"text": c["text"], "claim_id": c["claim_key"]} for c in verified[:6]],
                "pronunciation_hints": [{"term": "kWh", "suggested_reading": "ki-lô-oát giờ"}],
            },
            "unresolved_questions_for_editor": [],
        }
        return _validate_script(out), {"input_tokens": 0, "output_tokens": 0, "model": self.model}

    def rewrite_paragraph(self, ctx, paragraph, instruction):
        p = paragraph.strip()
        if "ngắn" in instruction.lower():
            sents = re.split(r"(?<=[.!?])\s+", p)
            p = " ".join(sents[: max(2, len(sents) - 1)])
        else:
            p = p + " Nói gọn lại, điều cần nhớ là hãy đọc kỹ điều kiện trước khi quyết định."
        return p, {"input_tokens": 0, "output_tokens": 0, "model": self.model}

    def _empty(self, pillar, why):
        return _validate_script({
            "pillar": pillar, "source_ids_used": [], "main_angle": "—",
            "title_options": [{"title": "Chưa đủ dữ kiện để đặt tiêu đề (1)", "strategy": "question"},
                              {"title": "Chưa đủ dữ kiện để đặt tiêu đề (2)", "strategy": "paradox"},
                              {"title": "Chưa đủ dữ kiện để đặt tiêu đề (3)", "strategy": "practical"}],
            "selected_title_index": 0, "spoken_script": "", "closing_teaser": "",
            "editorial_claim_map": [], "unresolved_questions_for_editor": [why],
        })


def _demo_titles(pillar: str, topic: str) -> list[dict]:
    t = topic if len(topic) <= 55 else topic[:55].rsplit(" ", 1)[0]
    t = t.rstrip(" ,.:")
    return [
        {"title": f"{t}: Ai thực sự được lợi?", "strategy": "question"},
        {"title": "Ưu đãi nghe hấp dẫn, nhưng điều kiện mới là chuyện chính", "strategy": "paradox"},
        {"title": "Xe điện: 3 điều cần kiểm tra trước khi quyết định", "strategy": "practical"},
    ]


def _demo_paragraphs(pillar: str, topic: str, q: str, facts: list[str], inferences: list[dict]) -> list[tuple[bool, str, int]]:
    """(bắt_buộc, đoạn, thứ_tự). Mỗi đoạn 2–5 câu, không lặp nhau."""
    f = facts + [""] * 8
    infer = inferences[0]["text"] if inferences else "Lợi ích có thể không chia đều cho mọi người dùng"
    p3 = pillar == "P3"
    P: list[tuple[bool, str, int]] = [
        (True, f"Có những thông báo nghe qua thì toàn tin vui. Nhưng với người đi xe điện, một dòng điều kiện nhỏ đôi khi quyết định bạn tiết kiệm được thật hay chỉ tiết kiệm trên giấy. Câu hỏi của chúng ta hôm nay rất cụ thể: {q}", 10),
        (True, "Và có một chi tiết mà nếu bỏ qua, bạn rất dễ tính sai toàn bộ bài toán. Chúng ta sẽ quay lại chi tiết đó ở giữa video, sau khi đã có đủ dữ kiện.", 11),
        (True, f"Hãy bắt đầu từ những gì đã được xác minh. {f[0]} {f[1]}", 20),
        (False, "Ở đây cần phân biệt hai chuyện. Một là điều doanh nghiệp công bố. Hai là điều người dùng sẽ trải nghiệm ngoài thực tế. Hai chuyện này có thể trùng nhau, nhưng không phải lúc nào cũng vậy, và nhiệm vụ của chúng ta là đặt chúng cạnh nhau một cách công bằng.", 21),
        (False, "Nếu bạn mới làm quen với xe điện, có một khái niệm cần nắm ngay. Ki-lô-oát giờ là đơn vị đo lượng điện năng, giống như lít xăng với xe xăng. Xe tiêu thụ bao nhiêu ki-lô-oát giờ cho một trăm ki-lô-mét thì chi phí mỗi chuyến đi sẽ tỷ lệ thuận với con số đó.", 22),
        (True, f"Tiếp theo là phần dễ bị đọc lướt nhất. {f[2]} {f[3]}", 30),
        (False, "Nghe thì hợp lý, nhưng vấn đề lại nằm ở một nơi khác. Một con số đẹp chỉ có ý nghĩa khi bạn biết nó áp dụng cho ai, trong bao lâu và kèm điều kiện gì. Bỏ qua một trong ba yếu tố đó, kết luận của bạn có thể lệch hẳn.", 31),
        (True, "Giờ hãy làm một phép tính ví dụ, với giả định được nói rõ từng bước. Giả sử bạn đi khoảng một nghìn hai trăm ki-lô-mét mỗi tháng và xe tiêu thụ khoảng mười lăm ki-lô-oát giờ cho mỗi một trăm ki-lô-mét. Khi đó bạn cần khoảng một trăm tám mươi ki-lô-oát giờ mỗi tháng. Nhân với đơn giá điện bạn thực trả, bạn sẽ ra chi phí năng lượng của mình. Đây chỉ là phép tính minh họa, con số của bạn sẽ khác tùy cách đi và nơi sạc.", 32),
        (False, "Điều thú vị là cùng một phép tính, hai người có thể ra hai kết quả rất khác nhau. Người sạc chủ yếu ở nhà sẽ nhìn thấy một bức tranh. Người phụ thuộc vào trạm công cộng sẽ nhìn thấy một bức tranh khác. Vì thế đừng vội so sánh con số của mình với một người lạ trên mạng.", 33),
        (True, f"Nhưng câu chuyện bắt đầu căng ở chỗ này. {f[4]} {infer}. Đây là nhận định có điều kiện, chưa phải kết quả đã được kiểm chứng.", 40),
        (True, "Phe ủng hộ sẽ nói rằng mọi chương trình đều phải có giới hạn, nếu không doanh nghiệp không thể duy trì. Phe hoài nghi lại cho rằng điều kiện càng nhiều thì số người thực sự hưởng lợi càng ít. Cả hai lập luận đều có lý, và câu trả lời phụ thuộc vào việc bạn thuộc nhóm nào.", 41),
        (False, "Đến đây, bạn thử để lại bình luận: với cách bạn đang dùng xe, điều kiện này giúp bạn hay gần như không ảnh hưởng gì?", 42),
        (False, "Có một chi tiết rất ít người để ý. Ngày công bố, ngày bắt đầu áp dụng và ngày kết thúc là ba mốc khác nhau. Khi đọc bất kỳ thông báo nào, hãy ghi lại cả ba mốc, vì quyết định của bạn hôm nay có thể rơi vào lúc chương trình sắp hết hạn.", 43),
        (True, f"Vậy tác động thực tế tới người dùng ở Việt Nam là gì? {f[5]} Với người đi lại trong phố mỗi ngày, lợi ích có thể thấy rõ trong hóa đơn hằng tháng. Với người ít đi, khoản tiết kiệm có thể nhỏ đến mức không nên là lý do chính để thay đổi thói quen.", 50),
        (False, "Còn với những ai đang cân nhắc mua xe, đừng để một chương trình có thời hạn trở thành yếu tố quyết định duy nhất. Chiếc xe sẽ ở với bạn nhiều năm, còn ưu đãi thì có ngày kết thúc. Hãy tính bài toán trong dài hạn rồi mới xem ưu đãi là phần thưởng thêm.", 51),
        (False, "Một rủi ro nữa là tâm lý đám đông. Khi nhiều người cùng được lợi ở một thời điểm, trạm sạc có thể đông hơn vào giờ cao điểm. Nếu bạn có thể linh hoạt giờ sạc, bạn sẽ ít bị ảnh hưởng hơn người buộc phải sạc theo một lịch cố định.", 52),
        (True, ("Hãy thử hình dung ba kịch bản, và đây là phần phân tích có điều kiện. Kịch bản thuận lợi: điều kiện rõ ràng, trạm đủ chỗ, người dùng tiết kiệm được thật. Kịch bản trung tính: lợi ích có nhưng chỉ rõ với một nhóm. Kịch bản bất lợi: trạm quá tải và lợi ích bị bào mòn bởi thời gian chờ."
                if p3 else
                "Trước khi quyết định, hãy tự hỏi ba câu. Câu thứ nhất: điều kiện này có áp dụng cho đúng xe và tài khoản của mình không. Câu thứ hai: thời hạn còn bao lâu. Câu thứ ba: nếu bỏ phần ưu đãi đi, lựa chọn của mình còn hợp lý không."), 53),
        (False, ("Nếu nhìn theo kiểu Tam Quốc, trạm sạc giống như kho lương của một đạo quân. Nói đơn giản, đó là nơi quyết định xe có tiếp tục lăn bánh hay không. Ai giữ được kho lương ổn định và dễ tiếp cận, người đó giữ được lòng tin của người dùng."
                 if p3 else
                 "Một mẹo nhỏ là chụp lại thông báo gốc kèm ngày bạn đọc nó. Khi có tranh cãi về điều kiện, bạn sẽ có căn cứ để hỏi lại bộ phận hỗ trợ thay vì dựa vào trí nhớ."), 54),
        (True, "Quay lại câu hỏi ở đầu video. Chi tiết dễ bị bỏ qua chính là đối tượng và thời hạn áp dụng. Nếu bạn thuộc đúng nhóm và dùng trong đúng thời gian, đây là lợi ích có thật. Nếu không, hãy coi nó là thông tin tham khảo và tiếp tục tính bài toán của riêng mình.", 60),
        (False, "Điều quan trọng nhất không phải là một chương trình cụ thể, mà là thói quen đọc kỹ điều kiện. Thói quen đó sẽ giúp bạn ở mọi quyết định liên quan tới xe điện, từ lúc mua cho tới lúc sạc mỗi ngày.", 61),
        (False, "Nếu bạn thấy cách mổ xẻ từng điều kiện như thế này hữu ích, hãy đăng ký kênh để không bỏ lỡ những phân tích tiếp theo.", 62),
        (False, "Trước khi đi tiếp, hãy thống nhất một nguyên tắc. Mọi con số trong video này đều đi kèm nguồn và ngày. Những gì là nhận định của kênh sẽ được nói rõ là nhận định. Cách làm này chậm hơn một chút, nhưng giúp bạn tự kiểm tra lại bất cứ lúc nào.", 23),
        (False, "Có người sẽ hỏi: vậy tại sao không chỉ đọc thông báo gốc cho nhanh? Câu trả lời là thông báo thường được viết để giới thiệu điều tốt nhất. Phần điều kiện, ngoại lệ và thời hạn thường nằm ở cuối, viết ngắn và rất dễ bị lướt qua.", 24),
        (False, "Hãy thử đặt mình vào vị trí của một người chạy xe trong thành phố, mỗi ngày đi khoảng bốn mươi ki-lô-mét. Người này sạc chủ yếu vào buổi tối. Với họ, câu hỏi quan trọng không phải là mức giảm bao nhiêu, mà là trạm gần nhà có áp dụng hay không và có còn chỗ vào giờ họ cần.", 34),
        (False, "Giờ đổi sang một người hay đi tỉnh vào cuối tuần. Họ sạc rải rác ở nhiều nơi, mỗi lần một ít. Với nhóm này, mức giảm trên mỗi lần sạc có thể không lớn, nhưng cộng dồn cả tháng thì lại đáng kể. Cùng một chính sách, hai cách dùng, hai cảm nhận rất khác.", 35),
        (False, "Nghe thì hợp lý, nhưng đó chưa phải rủi ro lớn nhất. Rủi ro lớn nhất là người dùng ra quyết định dựa trên một con số mà không kiểm tra điều kiện đi kèm. Khi điều kiện không khớp, sự thất vọng thường đổ lên cả công nghệ xe điện, chứ không chỉ lên một chương trình cụ thể.", 44),
        (False, "Từ phía doanh nghiệp, một chương trình có thời hạn là cách thử phản ứng của thị trường. Nếu người dùng hưởng ứng, chương trình có thể được kéo dài hoặc điều chỉnh. Nếu không, nó sẽ kết thúc đúng hạn. Đây là suy luận theo logic kinh doanh thông thường, chưa phải thông tin đã được công bố.", 45),
        (False, "Có một chi tiết thực tế mà ít ai nhắc tới: hãy kiểm tra ứng dụng hoặc hóa đơn sau lần sạc đầu tiên trong thời gian ưu đãi. Nếu mức giá hiển thị không như bạn hiểu, đó là lúc nên hỏi lại ngay, thay vì chờ đến cuối tháng mới phát hiện.", 55),
        (False, "Nhìn rộng hơn, mỗi thay đổi về giá sạc hay chính sách đều là một mảnh ghép trong bài toán sở hữu xe điện. Không có mảnh ghép nào tự nó quyết định tất cả. Người mua thông minh là người nhìn được cả bức tranh, và biết mảnh nào phù hợp với hoàn cảnh của mình.", 56),
    ]
    # bỏ chỗ trống nếu không có đủ fact
    return [(req, re.sub(r"\s{2,}", " ", txt).strip(), order) for req, txt, order in P]
