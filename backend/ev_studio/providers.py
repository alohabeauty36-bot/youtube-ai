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
    from .services.claude_writer import AnthropicWriter, DemoWriter

    s = settings or get_settings()
    choice = s.writer_provider
    _forbid_demo_in_prod(s, "Claude writer", choice)
    if choice == "demo" or (choice == "auto" and s.is_demo and not s.anthropic_api_key):
        return DemoWriter()
    return AnthropicWriter(s, client=client)  # raise BlockedByCredentials nếu thiếu key


def get_tts(settings: Settings | None = None):
    from .services.tts.adapters import DemoTTSAdapter, VieNeuHttpAdapter, VieNeuLocalAdapter

    s = settings or get_settings()
    choice = s.tts_provider
    _forbid_demo_in_prod(s, "TTS", choice)
    if choice == "demo" or (choice == "auto" and s.is_demo):
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
    """Thứ tự = độ ưu tiên: thư viện chủ kênh → Openverse (toàn web) → Wikimedia → Pexels → Pixabay.
    DEMO không gọi mạng: chỉ thư viện chủ kênh."""
    from .services.media.finders import OpenverseFinder, OwnLibraryFinder, PexelsFinder, PixabayFinder, WikimediaFinder

    s = settings or get_settings()
    finders: list = [OwnLibraryFinder(s.media_library_dir)]
    if not s.is_demo:
        if s.openverse_enabled:
            finders.append(OpenverseFinder(s.wikimedia_user_agent, client_id=s.openverse_client_id,
                                           client_secret=s.openverse_client_secret.get_secret_value() if s.openverse_client_secret else None))
        if s.wikimedia_enabled:
            finders.append(WikimediaFinder(s.wikimedia_user_agent))
        if s.pexels_api_key:
            finders.append(PexelsFinder(s.pexels_api_key.get_secret_value()))
        if s.pixabay_api_key:
            finders.append(PixabayFinder(s.pixabay_api_key.get_secret_value()))
    return finders


def get_image_generator(settings: Settings | None = None):
    """Bộ tạo ảnh AI dự phòng (chỉ dùng khi không tìm được ảnh hợp quyền). Tuỳ chọn: thiếu key → None
    (cảnh dùng đồ họa nội bộ), KHÔNG chặn pipeline."""
    from .services.media.generators import DemoImageGenerator, GeminiImageGenerator

    s = settings or get_settings()
    choice = s.image_gen_provider
    _forbid_demo_in_prod(s, "tạo ảnh AI", choice)
    if choice == "off":
        return None
    if choice == "demo" or s.is_demo:  # DEMO không bao giờ gọi Gemini thật
        return DemoImageGenerator()
    if not s.gemini_api_key:
        return None
    return GeminiImageGenerator(s.gemini_api_key.get_secret_value(), s.gemini_image_model, s.gemini_price_per_image_usd)


@dataclass
class Providers:
    """Có thể inject trong test (giả lập lỗi mạng, sai kênh, bot offline...)."""
    writer: Any = None
    tts: Any = None
    youtube: Any = None  # client cụ thể; None → tạo theo channel
    telegram: Any = None
    finders: list | None = None
    image_generator: Any = None  # None → theo cấu hình; False → tắt hẳn (test)
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

    def get_image_generator(self):
        if self.image_generator is None:
            self.image_generator = get_image_generator() or False
        return self.image_generator or None

    def get_finders(self):
        if self.finders is None:
            self.finders = get_asset_finders()
        return self.finders
