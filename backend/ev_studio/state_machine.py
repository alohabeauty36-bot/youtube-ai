"""State machine của Story.

Đường duy nhất tới trạng thái công khai do app thực hiện là PUBLISHED_AUTO: chỉ khi chủ kênh bật "Tự động hoàn toàn"
với thời gian chờ > 0, video đã được Telegram báo kèm nút Dừng, và qua kiểm tra lại ngay trước khi công khai."""
from __future__ import annotations

from enum import StrEnum


class StoryState(StrEnum):
    DISCOVERED = "DISCOVERED"
    RESEARCHING = "RESEARCHING"
    FACT_CHECK_PENDING = "FACT_CHECK_PENDING"
    SCRIPT_DRAFT = "SCRIPT_DRAFT"
    SCRIPT_APPROVED_FOR_RENDER = "SCRIPT_APPROVED_FOR_RENDER"
    TTS_DONE = "TTS_DONE"
    ASSETS_READY = "ASSETS_READY"
    RENDERING = "RENDERING"
    QA_PENDING = "QA_PENDING"
    READY_TO_UPLOAD = "READY_TO_UPLOAD"
    UPLOADING = "UPLOADING"
    UPLOADED_PRIVATE = "UPLOADED_PRIVATE"
    TELEGRAM_NOTIFIED = "TELEGRAM_NOTIFIED"
    EDITOR_REVIEW = "EDITOR_REVIEW"
    APPROVED_PRIVATE = "APPROVED_PRIVATE"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    REJECTED = "REJECTED"
    PUBLISHED_MANUALLY_OBSERVED = "PUBLISHED_MANUALLY_OBSERVED"
    PUBLISHED_AUTO = "PUBLISHED_AUTO"  # app tự công khai theo chế độ Tự động hoàn toàn (sau thời gian chờ, không bị Dừng)
    NEEDS_REVIEW = "NEEDS_REVIEW"  # gate chặn (thiếu nguồn, asset, thời lượng...) → chờ người
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"


S = StoryState

# Các bước tiến/lùi hợp lệ. Lùi về bước trước được phép để chạy lại từ checkpoint (vd: sửa script).
TRANSITIONS: dict[StoryState, set[StoryState]] = {
    S.DISCOVERED: {S.RESEARCHING},
    S.RESEARCHING: {S.FACT_CHECK_PENDING},
    S.FACT_CHECK_PENDING: {S.SCRIPT_DRAFT, S.RESEARCHING},
    S.SCRIPT_DRAFT: {S.SCRIPT_APPROVED_FOR_RENDER, S.SCRIPT_DRAFT, S.FACT_CHECK_PENDING},
    S.SCRIPT_APPROVED_FOR_RENDER: {S.TTS_DONE, S.SCRIPT_DRAFT},
    S.TTS_DONE: {S.ASSETS_READY, S.SCRIPT_DRAFT},
    S.ASSETS_READY: {S.RENDERING, S.SCRIPT_DRAFT},
    S.RENDERING: {S.QA_PENDING, S.ASSETS_READY},
    S.QA_PENDING: {S.READY_TO_UPLOAD, S.RENDERING, S.ASSETS_READY},
    S.READY_TO_UPLOAD: {S.UPLOADING, S.SCRIPT_DRAFT, S.RENDERING, S.FACT_CHECK_PENDING},
    S.UPLOADING: {S.UPLOADED_PRIVATE, S.READY_TO_UPLOAD},
    S.UPLOADED_PRIVATE: {S.TELEGRAM_NOTIFIED, S.EDITOR_REVIEW},
    S.TELEGRAM_NOTIFIED: {S.EDITOR_REVIEW},
    S.EDITOR_REVIEW: {S.APPROVED_PRIVATE, S.REVISION_REQUESTED, S.REJECTED, S.PUBLISHED_MANUALLY_OBSERVED, S.PUBLISHED_AUTO},
    S.APPROVED_PRIVATE: {S.PUBLISHED_MANUALLY_OBSERVED, S.REVISION_REQUESTED, S.REJECTED, S.PUBLISHED_AUTO},
    S.REVISION_REQUESTED: set(),  # bản sửa là story-version mới; bản cũ đứng yên
    S.REJECTED: set(),
    S.PUBLISHED_MANUALLY_OBSERVED: set(),
    S.PUBLISHED_AUTO: set(),
    S.NEEDS_REVIEW: set(),  # tính riêng: quay lại trạng thái trước khi bị chặn
    S.FAILED_RETRYABLE: set(),
    S.FAILED_PERMANENT: set(),
}

FAILABLE = {s for s in StoryState} - {
    S.REJECTED, S.PUBLISHED_MANUALLY_OBSERVED, S.PUBLISHED_AUTO, S.APPROVED_PRIVATE, S.REVISION_REQUESTED, S.FAILED_PERMANENT,
}

# Trạng thái mà story đã có video private trên YouTube
HAS_PRIVATE_UPLOAD = {
    S.UPLOADED_PRIVATE, S.TELEGRAM_NOTIFIED, S.EDITOR_REVIEW, S.APPROVED_PRIVATE,
    S.REVISION_REQUESTED, S.REJECTED, S.PUBLISHED_MANUALLY_OBSERVED, S.PUBLISHED_AUTO,
}


class InvalidTransition(Exception):
    pass


def can_transition(src: StoryState, dst: StoryState, resume_state: StoryState | None = None) -> bool:
    if dst in (S.FAILED_RETRYABLE, S.FAILED_PERMANENT, S.NEEDS_REVIEW):
        return src in FAILABLE
    if src in (S.FAILED_RETRYABLE, S.NEEDS_REVIEW, S.FAILED_PERMANENT):
        # resume về đúng checkpoint đã ghi hoặc lùi về biên tập/fact-check; hoặc bỏ hẳn (tự sửa không được)
        return dst == resume_state or dst in (S.SCRIPT_DRAFT, S.FACT_CHECK_PENDING, S.RESEARCHING, S.REJECTED)
    return dst in TRANSITIONS.get(src, set())


def assert_transition(src: StoryState, dst: StoryState, resume_state: StoryState | None = None) -> None:
    if dst == S.PUBLISHED_AUTO:
        from .config import get_settings

        s = get_settings()
        if not (s.full_auto and s.auto_publish_delay_minutes > 0):
            raise InvalidTransition("App chỉ được tự công khai khi chủ kênh bật Tự động hoàn toàn với thời gian chờ > 0")
    elif "PUBLISH" in dst.value and dst != S.PUBLISHED_MANUALLY_OBSERVED:
        raise InvalidTransition("App không được phép tự công khai video")
    if not can_transition(src, dst, resume_state):
        raise InvalidTransition(f"Không cho phép chuyển {src} → {dst}")
