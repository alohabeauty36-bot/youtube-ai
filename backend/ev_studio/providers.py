"""Chọn adapter theo chế độ. Production KHÔNG bao giờ âm thầm rơi về mock: thiếu key → BLOCKED_BY_CREDENTIALS."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Settings, get_settings
from .errors import BlockedByCredentials


def _forbid_demo_in_prod(settings: Settings, what: str, choice: str) -> None:
    if settings.app_mode == "production" and choice == "demo":
        raise BlockedByCredentials(f"Không được dùng adapter DEMO cho {what} ở chế độ production")


def get_writer(settings: Settings | None = None, client: Any = None):
    """Mọi bộ viết thật đều đi qua các phòng ban (AgentWriter) → luôn có trần ngân sách + bộ nhớ.
    writer_provider=anthropic: chỉ dùng thầy Claude, không có trò chạy máy (vẫn bị Ban Tài chính kiểm soát)."""
    from .agents.writer import AgentWriter
    from .services.claude_writer import DemoWriter

    s = settings or get_settings()
    choice = s.writer_provider
    _forbid_demo_in_prod(s, "Claude writer", choice)
    if choice == "demo" or (choice == "auto" and s.is_demo and not s.anthropic_api_key and not s.local_llm_url):
        return DemoWriter()
    if choice == "anthropic":
        s = s.model_copy(update={"local_llm_url": "", "teacher_strategy": "anthropic"})
    return AgentWriter(s, anthropic_client=client)  # raise BlockedByCredentials nếu không có thầy lẫn trò


def vieneu_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("vieneu") is not None


def get_tts(settings: Settings | None = None):
    """auto: có thư viện VieNeu thì LUÔN dùng giọng thật (kể cả chế độ demo — demo chỉ cấm upload thật);
    chưa cài thì demo dùng âm giả lập (không phải giọng nói), production báo BLOCKED_BY_CREDENTIALS."""
    from .services.tts.adapters import DemoTTSAdapter, VieNeuHttpAdapter, VieNeuLocalAdapter

    s = settings or get_settings()
    choice = s.tts_provider
    _forbid_demo_in_prod(s, "TTS", choice)
    if choice == "demo" or (choice == "auto" and s.is_demo and not vieneu_installed()):
        return DemoTTSAdapter(s)
    if choice == "vieneu_http":
        return VieNeuHttpAdapter(s)
    return VieNeuLocalAdapter(s)


def youtube_mode(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    _forbid_demo_in_prod(s, "YouTube", s.youtube_provider)
    if s.is_demo:
        return "demo"  # demo mode KHÔNG BAO GIỜ upload thật
    return "google"


def get_telegram(settings: Settings | None = None):
    from .services.telegram.bot import DemoTelegramClient, RealTelegramClient

    s = settings or get_settings()
    _forbid_demo_in_prod(s, "Telegram", s.telegram_provider)
    if s.is_demo or s.telegram_provider == "demo":
        return DemoTelegramClient()
    return RealTelegramClient(s.telegram_bot_token.get_secret_value() if s.telegram_bot_token else "")


def get_asset_finders(settings: Settings | None = None) -> list:
    from .services.media.finders import (OwnLibraryFinder, PexelsFinder, PexelsVideoFinder, PixabayClient,
                                         PixabayImageFinder, PixabayVideoFinder, WikimediaFinder)

    s = settings or get_settings()
    finders: list = [OwnLibraryFinder(s.media_library_dir)]
    if not s.is_demo:
        pixabay = (PixabayClient(s.pixabay_api_key.get_secret_value(), s.artifacts_dir / "_stock_cache" / "pixabay_api")
                   if s.pixabay_api_key else None)
        # video B-roll trước (Pixabay, rồi Pexels nếu còn key cũ — Pexels đã dừng cấp key mới từ 9/2026)
        if pixabay:
            finders.append(PixabayVideoFinder(pixabay))
        if s.pexels_api_key:
            finders.append(PexelsVideoFinder(s.pexels_api_key.get_secret_value()))
        if pixabay:
            finders.append(PixabayImageFinder(pixabay))
        if s.pexels_api_key:
            finders.append(PexelsFinder(s.pexels_api_key.get_secret_value()))
        if s.wikimedia_enabled:
            finders.append(WikimediaFinder(s.wikimedia_user_agent))
    return finders


@dataclass
class Providers:
    """Có thể inject trong test (giả lập lỗi mạng, sai kênh, bot offline...)."""
    writer: Any = None
    tts: Any = None
    youtube: Any = None  # client cụ thể; None → tạo theo channel
    telegram: Any = None
    finders: list | None = None
    extra: dict = field(default_factory=dict)

    def get_writer(self):
        if self.writer is None:
            self.writer = get_writer()
        return self.writer

    def get_tts(self):
        if self.tts is None:
            self.tts = get_tts()
        return self.tts

    def get_telegram(self):
        if self.telegram is None:
            self.telegram = get_telegram()
        return self.telegram

    def get_finders(self):
        if self.finders is None:
            self.finders = get_asset_finders()
        return self.finders
