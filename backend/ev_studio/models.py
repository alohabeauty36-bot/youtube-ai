"""Mô hình dữ liệu. `story_id` xuyên suốt pipeline; các version là immutable."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    oauth_token_encrypted: Mapped[str | None] = mapped_column(Text)  # Fernet, không bao giờ plaintext
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    token_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(200), index=True)
    feed_url: Mapped[str | None] = mapped_column(String(1000))
    kind: Mapped[str] = mapped_column(String(20), default="rss")  # rss | fixture | manual
    trust_tier: Mapped[int] = mapped_column(Integer, default=2)  # 1 chính thức, 2 báo uy tín, 3 cộng đồng
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit_seconds: Mapped[int] = mapped_column(Integer, default=60)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class NewsItem(Base):
    __tablename__ = "news_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    canonical_url: Mapped[str] = mapped_column(String(1000), index=True)
    publisher: Mapped[str | None] = mapped_column(String(200))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_excerpt: Mapped[str] = mapped_column(Text, default="")
    article_hash: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(10), default="vi")
    model_names: Mapped[list] = mapped_column(JSON, default=list)
    entities: Mapped[list] = mapped_column(JSON, default=list)
    supporting_sources: Mapped[list] = mapped_column(JSON, default=list)  # [{source_id,url,publisher,published_at,trust_tier}]
    contradictions: Mapped[list] = mapped_column(JSON, default=list)
    trust_tier: Mapped[int] = mapped_column(Integer, default=3)
    flags: Mapped[list] = mapped_column(JSON, default=list)  # rumor, clickbait...
    pillar: Mapped[str | None] = mapped_column(String(4))
    score: Mapped[float] = mapped_column(Float, default=0)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    score_reasons: Mapped[list] = mapped_column(JSON, default=list)
    curiosity_title: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="new")  # shortlist|research|reference|blocked|duplicate|dismissed
    duplicate_of: Mapped[str | None] = mapped_column(String(36))
    block_reason: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    claims: Mapped[list["Claim"]] = relationship(back_populates="news_item", cascade="all, delete-orphan")


class Story(TimestampMixin, Base):
    __tablename__ = "stories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    news_item_id: Mapped[str | None] = mapped_column(ForeignKey("news_items.id"))
    parent_story_id: Mapped[str | None] = mapped_column(ForeignKey("stories.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    pillar: Mapped[str] = mapped_column(String(4))
    topic: Mapped[str] = mapped_column(String(500))
    core_question: Mapped[str] = mapped_column(Text, default="")
    editorial_notes: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(40), default="DISCOVERED", index=True)
    resume_state: Mapped[str | None] = mapped_column(String(40))
    mode: Mapped[str] = mapped_column(String(20), default="MANUAL")  # MANUAL | AUTOPILOT_PRIVATE
    autopilot_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    upload_requested_by: Mapped[str | None] = mapped_column(String(100))
    script_approved_by: Mapped[str | None] = mapped_column(String(100))
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id"))
    current_script_version_id: Mapped[str | None] = mapped_column(String(36))
    current_render_id: Mapped[str | None] = mapped_column(String(36))
    freshness_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    blockers: Mapped[list] = mapped_column(JSON, default=list)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    news_item: Mapped[NewsItem | None] = relationship()


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("status <> 'verified' OR (source_url IS NOT NULL AND source_url <> '')", name="ck_verified_has_source"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    news_item_id: Mapped[str] = mapped_column(ForeignKey("news_items.id"))
    claim_key: Mapped[str] = mapped_column(String(20))  # c1, c2 ...
    text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(40), default="general")
    subject: Mapped[str | None] = mapped_column(String(200))
    value: Mapped[str | None] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(50))
    date_scope: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence_excerpt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="unverified")  # verified|unverified|contested|inference|removed
    is_key: Mapped[bool] = mapped_column(Boolean, default=True)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_reason: Mapped[str | None] = mapped_column(Text)
    editor_note: Mapped[str | None] = mapped_column(Text)
    volatile: Mapped[bool] = mapped_column(Boolean, default=False)  # giá/ưu đãi... cần freshness_check
    freshness_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    news_item: Mapped[NewsItem] = relationship(back_populates="claims")


class ResearchPack(TimestampMixin, Base):
    __tablename__ = "research_packs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(100))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ScriptVersion(TimestampMixin, Base):
    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("story_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[str | None] = mapped_column(String(36))
    content: Mapped[dict] = mapped_column(JSON)  # toàn bộ JSON output của Claude (đã validate)
    spoken_script: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    actual_audio_seconds: Mapped[float | None] = mapped_column(Float)
    qa: Mapped[dict] = mapped_column(JSON, default=dict)
    qa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(100), default="claude")
    change_note: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(100))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class VoiceProfile(TimestampMixin, Base):
    __tablename__ = "voice_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(40))
    voice_id: Mapped[str] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_clone: Mapped[bool] = mapped_column(Boolean, default=False)
    clone_consent_attested: Mapped[bool] = mapped_column(Boolean, default=False)


class TTSSegment(TimestampMixin, Base):
    __tablename__ = "tts_segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    segment_key: Mapped[str] = mapped_column(String(20))
    paragraph_idx: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    tts_text: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    voice: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(String(1000))
    duration: Mapped[float] = mapped_column(Float)
    start: Mapped[float] = mapped_column(Float, default=0)
    end: Mapped[float] = mapped_column(Float, default=0)
    qc: Mapped[dict] = mapped_column(JSON, default=dict)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str | None] = mapped_column(ForeignKey("stories.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40))  # own_library|pexels|wikimedia|internal_graphic|web_reference
    source_url: Mapped[str | None] = mapped_column(String(1000))
    creator: Mapped[str | None] = mapped_column(String(300))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_name: Mapped[str | None] = mapped_column(String(100))
    year: Mapped[int | None] = mapped_column(Integer)
    scene_description: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    local_path: Mapped[str | None] = mapped_column(String(1000))
    media_type: Mapped[str] = mapped_column(String(10), default="image")
    review_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_illustration: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    license: Mapped["AssetLicense | None"] = relationship(back_populates="asset", uselist=False, cascade="all, delete-orphan")


class AssetLicense(TimestampMixin, Base):
    __tablename__ = "asset_licenses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), unique=True)
    license_name: Mapped[str | None] = mapped_column(String(200))
    license_url: Mapped[str | None] = mapped_column(String(1000))
    allowed_commercial: Mapped[bool | None] = mapped_column(Boolean)
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=False)
    attribution_text: Mapped[str | None] = mapped_column(Text)
    permission_proof: Mapped[str | None] = mapped_column(Text)
    asset: Mapped[Asset] = relationship(back_populates="license")


class Scene(TimestampMixin, Base):
    __tablename__ = "scenes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id"))
    idx: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    narration_segment_ids: Mapped[list] = mapped_column(JSON, default=list)
    visual_type: Mapped[str] = mapped_column(String(40))
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    claim_ids: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    image_path: Mapped[str | None] = mapped_column(String(1000))


class Render(TimestampMixin, Base):
    __tablename__ = "renders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    script_version_id: Mapped[str] = mapped_column(ForeignKey("script_versions.id"))
    path: Mapped[str] = mapped_column(String(1000))
    preview_path: Mapped[str | None] = mapped_column(String(1000))
    subtitle_srt_path: Mapped[str | None] = mapped_column(String(1000))
    subtitle_ass_path: Mapped[str | None] = mapped_column(String(1000))
    thumbnail_paths: Mapped[list] = mapped_column(JSON, default=list)
    audio_path: Mapped[str | None] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64))
    duration: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    qa: Mapped[dict] = mapped_column(JSON, default=dict)
    qa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class SEOVariant(TimestampMixin, Base):
    __tablename__ = "seo_variants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    render_id: Mapped[str | None] = mapped_column(String(36))
    title_options: Mapped[list] = mapped_column(JSON)
    selected_index: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    chapters: Mapped[list] = mapped_column(JSON, default=list)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)

    @property
    def selected_title(self) -> str:
        return self.title_options[self.selected_index]["title"]


class YouTubeUpload(TimestampMixin, Base):
    __tablename__ = "youtube_uploads"
    __table_args__ = (
        CheckConstraint("privacy_status_requested = 'private'", name="ck_upload_private_only"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    render_id: Mapped[str] = mapped_column(ForeignKey("renders.id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|uploading|uploaded|verified|failed
    youtube_video_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    privacy_status_requested: Mapped[str] = mapped_column(String(20), default="private")
    privacy_status_observed: Mapped[str | None] = mapped_column(String(20))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    thumbnail_set: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class ReviewEvent(TimestampMixin, Base):
    __tablename__ = "review_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))  # approve|request_revision|reject|observed_published|claim_*|asset_*
    actor: Mapped[str] = mapped_column(String(100))
    via: Mapped[str] = mapped_column(String(20), default="dashboard")
    note: Mapped[str | None] = mapped_column(Text)
    new_story_id: Mapped[str | None] = mapped_column(String(36))


class TelegramMessage(TimestampMixin, Base):
    __tablename__ = "telegram_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), index=True)
    chat_id: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|sent|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class TelegramCallbackToken(TimestampMixin, Base):
    __tablename__ = "telegram_callback_tokens"
    token: Mapped[str] = mapped_column(String(40), primary_key=True)
    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"))
    action: Mapped[str] = mapped_column(String(30))  # approve | request_revision
    chat_id: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    story_id: Mapped[str | None] = mapped_column(ForeignKey("stories.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(300), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|failed_retryable|failed_permanent|blocked
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    history: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class ErrorLog(Base):
    """Nhật ký lỗi của tool. Lỗi giống hệt nhau (cùng fingerprint, chưa xử lý) được gộp: tăng `count`, cập nhật `last_seen_at`."""
    __tablename__ = "error_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[str] = mapped_column(String(10), index=True)  # WARNING | ERROR | CRITICAL
    source: Mapped[str] = mapped_column(String(30), index=True)  # pipeline|job|ingest|tts|media|render|youtube|telegram|scheduler|api|config|system
    logger: Mapped[str] = mapped_column(String(120), default="")
    message: Mapped[str] = mapped_column(Text)
    exc_type: Mapped[str | None] = mapped_column(String(120))
    traceback: Mapped[str | None] = mapped_column(Text)
    story_id: Mapped[str | None] = mapped_column(String(36), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36))
    job_type: Mapped[str | None] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class CostUsage(Base):
    __tablename__ = "cost_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    story_id: Mapped[str | None] = mapped_column(String(36), index=True)
    job_type: Mapped[str | None] = mapped_column(String(40))
    service: Mapped[str] = mapped_column(String(40))  # anthropic|vieneu|render|youtube|pexels|storage
    units: Mapped[float] = mapped_column(Float)
    unit_type: Mapped[str] = mapped_column(String(40))  # input_tokens|output_tokens|audio_seconds|cpu_seconds|bytes|quota_units
    estimated_usd: Mapped[float] = mapped_column(Float, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------------------------ Ban Tài chính + bộ não phòng ban


class BudgetLedger(Base):
    """Sổ cái chi tiền AI. Ghi bằng session riêng và commit ngay, nên job rollback cũng không làm mất dấu tiền đã tiêu.

    Mỗi lần gọi Claude/ChatGPT: giữ chỗ (reserved, ước tính trường hợp xấu nhất) → chốt (settled, số thật) hoặc huỷ (released).
    """
    __tablename__ = "budget_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    department: Mapped[str] = mapped_column(String(30), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # anthropic | openai
    model: Mapped[str] = mapped_column(String(80))
    story_id: Mapped[str | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(10), index=True)  # reserved | settled | released
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(String(200))


class AgentMemory(Base):
    """Bộ nhớ dài hạn của từng phòng ban: câu hỏi + câu trả lời của thầy, bài học, kiến thức đã xác minh, sổ tay văn phong."""
    __tablename__ = "agent_memories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    department: Mapped[str] = mapped_column(String(30), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # answer | lesson | knowledge | style_guide
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(20))  # anthropic | openai | local | owner | system
    model: Mapped[str | None] = mapped_column(String(80))
    request: Mapped[str | None] = mapped_column(Text)
    response: Mapped[Any] = mapped_column(JSON, default=dict)
    text: Mapped[str] = mapped_column(Text, default="")  # dạng chữ để truy xuất
    quality: Mapped[float] = mapped_column(Float, default=0.0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    story_id: Mapped[str | None] = mapped_column(String(36), index=True)


class AgentSkill(Base):
    """Hồ sơ năng lực của một phòng ban: trò (model chạy máy) đã tự làm đạt bao nhiêu lần so với thầy."""
    __tablename__ = "agent_skills"
    department: Mapped[str] = mapped_column(String(30), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20), default="auto")  # auto | teacher_only | student_only
    teacher_calls: Mapped[int] = mapped_column(Integer, default=0)
    student_attempts: Mapped[int] = mapped_column(Integer, default=0)
    student_passes: Mapped[int] = mapped_column(Integer, default=0)
    student_live: Mapped[int] = mapped_column(Integer, default=0)  # số lần trò tự làm thay thầy (không tốn tiền)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    recent: Mapped[list] = mapped_column(JSON, default=list)  # 1/0 của các lần thử gần nhất
    similarity_avg: Mapped[float] = mapped_column(Float, default=0.0)
    graduated: Mapped[bool] = mapped_column(Boolean, default=False)
    saved_usd: Mapped[float] = mapped_column(Float, default=0.0)
    last_reflect_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
