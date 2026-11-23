"""Schema JSON trung gian (Pydantic) — validate đầu ra Claude và API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Pillar = Literal["P1", "P2", "P3"]


class TitleOption(BaseModel):
    title: str = Field(min_length=10, max_length=100)
    strategy: str


class ClaimMapEntry(BaseModel):
    claim_id: str
    source_ids: list[str]
    relevant_script_excerpt: str


class OnScreenFact(BaseModel):
    text: str
    claim_id: str


class PronunciationHint(BaseModel):
    term: str
    suggested_reading: str


class ProductionNotes(BaseModel):
    visual_concepts: list[str] = []
    key_on_screen_facts: list[OnScreenFact] = []
    pronunciation_hints: list[PronunciationHint] = []


class ScriptOutput(BaseModel):
    """Đúng schema Mục III. `spoken_script` là phần DUY NHẤT đưa vào TTS."""
    pillar: Pillar
    source_ids_used: list[str]
    main_angle: str
    title_options: list[TitleOption]
    selected_title_index: int = Field(ge=0, le=2)
    spoken_script: str
    closing_teaser: str
    editorial_claim_map: list[ClaimMapEntry]
    production_notes: ProductionNotes = ProductionNotes()
    unresolved_questions_for_editor: list[str] = []

    @field_validator("title_options")
    @classmethod
    def _three(cls, v):
        if len(v) != 3:
            raise ValueError("Phải có chính xác 3 tiêu đề")
        return v


class ResearchPackLLM(BaseModel):
    editorial_angle: str
    why_now: str
    what_changed: str
    counterarguments: list[str]
    risk_notes: list[str]
    source_ids: list[str]
    recommended_pillar: Pillar
    hook_options: list[str]
    video_unique_value: str
    core_question: str = ""
    blocking_issues: list[str] = []


class ExtractedClaim(BaseModel):
    claim_key: str
    text: str
    claim_type: str = "general"
    subject: str | None = None
    value: str | None = None
    unit: str | None = None
    date_scope: str | None = None
    evidence_excerpt: str | None = None
    status: Literal["unverified", "inference"] = "unverified"
    is_key: bool = True


class ExtractedClaims(BaseModel):
    claims: list[ExtractedClaim]
