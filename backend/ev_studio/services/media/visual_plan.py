"""Kế hoạch hình ảnh cho TỪNG ĐOẠN lời đọc: từ khoá tìm ảnh thật (tiếng Anh) + mô tả cảnh cho ảnh AI dự phòng.

- Có Claude (writer thật): một lần gọi cho cả bài, prompt ở `prompts/visual_plan.md`, JSON validate bằng Pydantic.
- Không có Claude (DEMO) hoặc Claude lỗi: bộ từ khoá dựng sẵn (heuristic), không gọi mạng.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from ...config import get_settings
from ...errors import BlockedByCredentials
from ..ranker import strip_accents
from .generators import is_sensitive

log = logging.getLogger(__name__)


class ParagraphVisual(BaseModel):
    paragraph_idx: int
    search_queries: list[str] = Field(default_factory=list)
    image_prompt: str = ""
    subject_model: str | None = None
    skip_image: bool = False
    reason: str = ""
    planner: str = "heuristic"

    @field_validator("search_queries")
    @classmethod
    def _clean(cls, v):
        out = []
        for q in v:
            q = re.sub(r"\s+", " ", str(q)).strip()[:80]
            if q and q.lower() not in {x.lower() for x in out}:
                out.append(q)
        return out[:5]


class VisualPlan(BaseModel):
    paragraphs: list[ParagraphVisual]


# Từ khoá tiếng Việt (bỏ dấu) → cụm tìm ảnh tiếng Anh + cảnh cho ảnh AI. Thứ tự = độ ưu tiên.
KEYWORDS: list[tuple[str, str, str]] = [
    ("tram sac", "EV charging station", "electric cars parked at a public fast-charging station in a Vietnamese city"),
    ("sac nhanh", "DC fast charger electric car", "close-up of a DC fast charging cable plugged into an electric car"),
    ("sac tai nha", "home EV charger garage", "electric car charging from a wall charger in a home garage at night"),
    ("cong sac", "electric car charging port", "close-up of an electric car charging port with cable"),
    ("xe may dien", "electric scooter", "electric scooters parked on a busy Vietnamese street"),
    ("pin", "electric vehicle battery pack", "cutaway illustration of an electric vehicle battery pack on a factory floor"),
    ("quang duong", "electric car highway driving", "electric car driving on an open highway at sunrise"),
    ("tieu thu", "electric car dashboard energy", "dashboard of an electric car showing energy consumption, no readable text"),
    ("dieu hoa", "car air conditioning interior", "interior of a modern car cabin with air vents in summer heat"),
    ("toc do", "highway traffic speed", "cars driving fast on a multi-lane highway, motion blur"),
    ("gia dien", "electricity meter", "residential electricity meter on a wall"),
    ("chi phi", "family budget calculator", "hands with a calculator and household bills on a table"),
    ("gia ban", "car dealership showroom", "bright car showroom with generic electric cars"),
    ("uu dai", "car dealership showroom", "car showroom with generic electric cars and promotional lighting"),
    ("tra gop", "car loan contract", "people signing a car purchase contract at a desk"),
    ("chinh sach", "government policy document", "official documents and a pen on a desk"),
    ("quy dinh", "traffic regulation sign", "road signs on a city street"),
    ("thue", "tax documents", "tax forms and a calculator on a desk"),
    ("le phi truoc ba", "vehicle registration office", "vehicle registration office counter"),
    ("bao duong", "car maintenance workshop", "mechanic inspecting an electric car in a clean workshop"),
    ("bao hanh", "car service center", "car service center reception area"),
    ("nha may", "car factory assembly line", "robotic arms on a car assembly line"),
    ("xuat khau", "car carrier ship port", "car carrier ship being loaded at a port"),
    ("giao thong", "Vietnam city traffic", "busy traffic in Ho Chi Minh City with motorbikes and cars"),
    ("o nhiem", "city air pollution", "hazy city skyline with smog"),
    ("moi truong", "green city trees", "green city street with trees and clean air"),
    ("taxi", "electric taxi fleet", "fleet of generic electric taxis parked in a row"),
    ("nguoi mua", "car buyer showroom", "a customer looking at an electric car in a showroom, seen from behind"),
    ("gia dinh", "family car trip", "family loading luggage into a car, seen from behind"),
    ("thanh pho", "city street electric car", "electric car on a modern city street at dusk"),
]
GENERIC = ("electric car", "modern electric car on a city street in Southeast Asia")


def _keyword_hits(text: str) -> list[tuple[str, str]]:
    t = strip_accents(text.lower())
    return [(q, p) for k, q, p in KEYWORDS if re.search(rf"\b{re.escape(k)}\b", t)]


def _mentioned_models(text: str, models: list[str]) -> list[str]:
    t = re.sub(r"[\s\-]", "", text.lower())
    return [m for m in models if re.sub(r"[\s\-]", "", m.lower()) in t]


def heuristic_plan(paragraphs: list[dict], story_models: list[str], max_queries: int = 3) -> list[ParagraphVisual]:
    out = []
    for p in paragraphs:
        text = p["text"]
        hits = _keyword_hits(text)
        models = _mentioned_models(text, story_models)
        queries: list[str] = []
        if models:
            queries.append(f"VinFast {models[0]}")
        queries += [q for q, _ in hits]
        queries.append(GENERIC[0])
        scene = hits[0][1] if hits else GENERIC[1]
        numeric = len(re.findall(r"\d", text)) >= max(12, len(text) // 25)
        sensitive = is_sensitive(text)
        queries = list(dict.fromkeys(queries))
        if len(queries) > max_queries:
            queries = queries[:max_queries - 1] + [GENERIC[0]]
        out.append(ParagraphVisual(
            paragraph_idx=p["paragraph_idx"], search_queries=queries,
            image_prompt=scene, subject_model=models[0] if models else None,
            skip_image=sensitive or (numeric and not hits),
            reason=("Đoạn nói về sự cố/sự kiện có thật — dùng đồ họa nội bộ" if sensitive
                    else "Đoạn nhiều số liệu — dùng đồ họa nội bộ" if numeric and not hits else "Từ khoá theo nội dung đoạn"),
        ))
    return out


def plan_visuals(paragraphs: list[dict], story_models: list[str], writer=None, pillar: str = "") -> tuple[list[ParagraphVisual], dict]:
    """Trả (kế hoạch theo đoạn, usage Claude). Claude lỗi/không có → heuristic (không chặn pipeline)."""
    s = get_settings()
    fallback = heuristic_plan(paragraphs, story_models, s.media_queries_per_paragraph)
    llm = getattr(writer, "llm", None)
    if writer is None or getattr(writer, "is_demo", True) or llm is None:
        return fallback, {}
    system = (s.prompts_dir / "visual_plan.md").read_text(encoding="utf-8")
    user = json.dumps({"STORY_MODELS": story_models, "PILLAR": pillar,
                       "PARAGRAPHS": [{"paragraph_idx": p["paragraph_idx"], "text": p["text"]} for p in paragraphs]},
                      ensure_ascii=False)
    try:
        raw, usage = llm.complete_json(system, user, 6000)
        plan = VisualPlan.model_validate(raw).paragraphs
    except BlockedByCredentials:
        raise
    except (ValidationError, Exception) as e:  # noqa: BLE001 — kế hoạch ảnh không được làm hỏng pipeline
        log.warning("Claude visual plan lỗi (%s) → dùng heuristic", type(e).__name__)
        return fallback, {}
    by_idx = {v.paragraph_idx: v for v in plan}
    merged = []
    for h in fallback:
        v = by_idx.get(h.paragraph_idx)
        if v is None:
            merged.append(h)
            continue
        v.planner = "claude"
        if not v.search_queries:
            v.search_queries = h.search_queries
        if not v.image_prompt:
            v.image_prompt = h.image_prompt
        if is_sensitive(next(p["text"] for p in paragraphs if p["paragraph_idx"] == v.paragraph_idx)):
            v.skip_image = True  # chốt an toàn phía backend, không phụ thuộc vào LLM
        if v.subject_model and v.subject_model not in story_models:
            v.subject_model = None
        v.search_queries = v.search_queries[:s.media_queries_per_paragraph]
        merged.append(v)
    return merged, usage
