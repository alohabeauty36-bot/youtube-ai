"""Cấu hình ứng dụng. Không chứa secret mặc định; secrets đọc từ env/.env."""
from __future__ import annotations

import secrets as _secrets
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = Path(__file__).resolve().parent
FONT_DIR = PKG_DIR / "assets" / "fonts"
MIN_VIDEO_SECONDS = 600.0  # 10 phút — video không bao giờ được ngắn hơn


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore",
                                      populate_by_name=True)

    # --- chế độ ---
    app_mode: Literal["demo", "production"] = "demo"
    data_dir: Path = REPO_ROOT / "data"
    database_url: str = f"sqlite:///{(REPO_ROOT / 'data' / 'ev_studio.db').as_posix()}"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    prompts_dir: Path = REPO_ROOT / "prompts"
    configs_dir: Path = REPO_ROOT / "configs"
    fixtures_dir: Path = REPO_ROOT / "fixtures"
    media_library_dir: Path = REPO_ROOT / "media_library"
    public_base_url: str = "http://localhost:8010"
    trust_localhost: bool = Field(False, validation_alias="EVCS_TRUST_LOCALHOST")  # run.py bật khi chỉ nghe 127.0.0.1
    frontend_base_url: str = "http://localhost:3000"
    timezone: str = "Asia/Ho_Chi_Minh"

    # --- bảo mật ---
    secret_key: SecretStr | None = None  # bắt buộc ở production (mã hoá token + ký link)
    admin_token: SecretStr | None = None  # quyền owner
    editor_token: SecretStr | None = None  # quyền editor (không upload)
    signed_link_ttl_seconds: int = 72 * 3600

    # --- Anthropic ---
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_max_tokens: int = 16000
    writer_provider: Literal["auto", "demo", "anthropic", "agents"] = "auto"

    # --- Ban Tài chính: ngân sách AI (USD). Đây là trần CỨNG: chạm trần thì không gọi Claude/ChatGPT nữa ---
    budget_monthly_usd: float = 50.0
    budget_safety_margin: float = 0.05  # giữ lại 5% trần tháng phòng sai số ước tính
    budget_story_max_usd: float = 0.75  # trần chi cho một video (gồm cả các lần viết lại)
    stories_per_day: int = 3  # Autopilot làm tối đa N tin/ngày
    stories_per_day_min: int = 2  # mục tiêu tối thiểu; ngân sách ngày luôn chừa đủ cho số tin này
    model_prices_json: str = ""  # ghi đè/bổ sung giá: {"ten-model": [gia_vao, gia_ra]} USD/1M token

    # --- Bộ não phòng ban (agent học từ Claude/ChatGPT rồi tự viết bằng model chạy trên máy) ---
    agents_enabled: bool = True
    teacher_strategy: Literal["anthropic", "openai", "alternate"] = "anthropic"
    model_claims: str = "claude-haiku-5-5"  # Phòng Săn tin: việc đơn giản → model rẻ
    model_research: str = "claude-sonnet-5-5"
    model_script: str = ""  # để trống = dùng anthropic_model
    model_editor: str = "claude-haiku-5-5"
    model_director: str = "claude-haiku-5-5"  # Phòng Đạo diễn hình ảnh: danh sách cảnh quay
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    local_llm_url: str = ""  # Ollama, ví dụ http://localhost:11434 — để trống = chưa có "trò"
    local_llm_model: str = "qwen2.5:7b-instruct"
    local_llm_timeout_seconds: float = 1800
    agent_shadow_learning: bool = True  # mỗi lần thầy làm, trò làm thử cùng đề để chấm điểm (miễn phí)
    agent_shadow_async: bool = True
    agent_graduation_window: int = 20
    agent_graduation_min: int = 10
    agent_graduation_rate: float = 0.8
    agent_audit_rate: float = 0.15  # sau khi tốt nghiệp vẫn nhờ thầy chấm ngẫu nhiên 15% số việc
    # cấp Bậc thầy: tốt nghiệp + đủ số lần đạt + tỷ lệ đạt rất cao → tự làm, không gửi thầy kiểm tra nữa
    agent_master_min_passes: int = 40
    agent_master_rate: float = 0.95
    agent_master_audit_rate: float = 0.0

    # --- Tự động hoàn toàn: một công tắc cho Autopilot + lịch quét + tự sửa lỗi + tự công khai có hẹn giờ ---
    full_auto: bool = False
    auto_publish_delay_minutes: int = 120  # 0 = không tự công khai (vẫn giữ riêng tư như cũ)
    auto_heal_max_attempts: int = 2  # tự chạy lại video lỗi tối đa N lần, vẫn hỏng thì bỏ tin đó

    # --- Ban Tăng trưởng: theo dõi kênh bằng YouTube Analytics, chạy theo KPI doanh thu tháng ---
    kpi_monthly_revenue_usd: float = 0.0  # 0 = chưa đặt KPI. Doanh thu quảng cáo YouTube (estimatedAdRevenue), USD
    growth_auto_apply: bool = True  # Ban Tăng trưởng tự chỉnh số video/ngày + tỷ trọng trụ cột (trong trần ngân sách)
    growth_max_stories_per_day: int = 6  # trần cứng số video/ngày mà Ban Tăng trưởng được đặt
    model_growth: str = "claude-haiku-5-5"  # viết bản ghi nhớ chiến lược hằng tuần (rẻ)

    # --- Phòng Bài luận: tự research web + viết bài luận theo chủ đề ---
    model_essay: str = "claude-sonnet-5-5"
    essay_teacher_strategy: Literal["inherit", "anthropic", "openai", "alternate"] = "alternate"  # học cả Claude lẫn ChatGPT
    essay_language: str = "vi"
    essay_default_words: int = 1200
    research_search_provider: Literal["auto", "wikipedia", "searxng", "brave", "fixture"] = "auto"
    research_online_in_demo: bool = False  # DEMO mặc định dùng tư liệu giả lập; bật để DEMO cũng tìm trên mạng thật
    research_wiki_langs: str = "vi,en"
    searxng_url: str = ""  # SearXNG tự cài, ví dụ http://localhost:8888
    brave_api_key: SecretStr | None = None
    research_max_sources: int = 8
    research_max_fetch: int = 10

    # --- TTS ---
    tts_provider: Literal["auto", "demo", "vieneu_local", "vieneu_http"] = "auto"
    vieneu_mode: str = "v3turbo"
    vieneu_backend: str | None = None  # "onnx" để ép CPU
    vieneu_http_url: str = "http://localhost:8001"
    vieneu_voice: str = "Quang Sơn"
    vieneu_model_version: str = "vieneu-3.8.5-v3turbo"

    # --- media ---
    # analysis: bài phân tích tin đã đăng (tin mới có 1 báo đưa vẫn làm được, phải ghi "theo <báo>"); strict: chỉ claim đã xác minh
    # off (mặc định): không luật xác minh nào chặn video
    fact_mode: Literal["off", "analysis", "strict"] = "off"
    pixabay_api_key: SecretStr | None = None  # nguồn video/ảnh chính (key miễn phí, cấp ngay)
    pexels_api_key: SecretStr | None = None  # chỉ dùng key cũ: Pexels dừng cấp key mới từ ~30/9/2026
    wikimedia_enabled: bool = True
    wikimedia_user_agent: str = "EVCreatorStudio/1.0 (contact: owner@example.invalid)"

    # --- YouTube ---
    youtube_provider: Literal["auto", "demo", "google"] = "auto"
    google_client_secrets_file: Path | None = None
    google_client_secrets_json: SecretStr | None = None  # dán nội dung client_secret.json trong trang Cấu hình
    youtube_expected_channel_id: str | None = None
    youtube_made_for_kids_confirmed_false: bool = False  # chủ kênh xác nhận nội dung KHÔNG dành cho trẻ em
    youtube_contains_synthetic_media: bool = True  # giọng AI → khai báo nội dung tổng hợp
    youtube_category_id: str = "2"  # Autos & Vehicles

    # --- Telegram ---
    telegram_provider: Literal["auto", "demo", "telegram"] = "auto"
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_chat_ids: str = ""  # danh sách, phân tách bằng dấu phẩy
    telegram_webhook_secret: SecretStr | None = None
    telegram_callback_ttl_seconds: int = 7 * 24 * 3600

    # --- biên tập / thời lượng ---
    # Video KHÔNG được dưới 10 phút (MIN_VIDEO_SECONDS). Mục tiêu 11 phút để audio đo thật có biên an toàn.
    duration_target_seconds: float = 660
    duration_min_seconds: float = 600
    duration_max_seconds: float = 720
    # CHỈ dành cho test tự động (rút ngắn thời lượng cho nhanh). Không đặt biến này khi chạy thật.
    allow_short_duration_for_tests: bool = Field(False, validation_alias="EVCS_ALLOW_SHORT_DURATION")
    max_script_revisions: int = 3
    score_shortlist: int = 75
    score_research: int = 60
    score_autopilot: int = 80
    max_videos_per_week: int = 21
    scan_interval_hours: float = 3
    freshness_max_age_hours: float = 24

    # --- hình ảnh video ---
    stock_max_queries: int = 30  # tối đa số từ khoá tìm tư liệu cho một video (Pexels: 200 lượt/giờ)
    music_volume_db: float = -20.0  # nhạc nền (thư viện chủ kênh media_library/music), tự giảm thêm khi có giọng đọc
    channel_brand: str = ""  # chữ logo góc màn hình; trống = tên kênh YouTube

    # --- ảnh minh họa AI (FLUX.1 schnell): lấp cảnh kho tư liệu không có hình phù hợp ---
    ai_image_provider: Literal["auto", "cloudflare", "fal", "off"] = "auto"  # auto: Cloudflare (miễn phí) trước, rồi fal.ai
    ai_images_per_video: int = 12
    cloudflare_account_id: str = ""
    cloudflare_api_token: SecretStr | None = None
    cloudflare_image_model: str = "@cf/black-forest-labs/flux-1-schnell"
    cloudflare_daily_images: int = 150  # ~57 neurons/ảnh → 150 ảnh ≈ 8.600 neurons, dưới 10.000 neurons miễn phí/ngày
    fal_api_key: SecretStr | None = None
    fal_model: str = "fal-ai/flux-1/schnell"
    fal_image_size: str = "landscape_16_9"
    fal_price_per_image: float = 0.003  # USD; Ban Tài chính giữ chỗ trước mỗi ảnh

    # --- render ---
    render_preset: str = "veryfast"
    render_burn_subtitles: bool = True
    render_fps: int = 30
    font_path: Path = FONT_DIR / "DejaVuSans.ttf"  # đóng gói kèm → chạy được trên Windows/macOS/Linux
    font_bold_path: Path = FONT_DIR / "DejaVuSans-Bold.ttf"

    # --- jobs ---
    job_executor: Literal["inline", "celery"] = "inline"
    redis_url: str = "redis://localhost:6379/0"
    job_max_attempts: int = 4
    job_backoff_base_seconds: float = 2.0
    job_concurrency: int = 2

    @field_validator("data_dir", "artifacts_dir", "prompts_dir", "configs_dir", "fixtures_dir", "media_library_dir", mode="before")
    @classmethod
    def _abs(cls, v):
        p = Path(v)
        return p if p.is_absolute() else (REPO_ROOT / p)

    @model_validator(mode="after")
    def _enforce_min_duration(self):
        """Sàn cứng 10 phút. Giá trị cũ (vd. .env có DURATION_MIN_SECONDS=570) được nâng lên, không làm sập app."""
        if self.allow_short_duration_for_tests:
            return self
        lo = max(float(self.duration_min_seconds), MIN_VIDEO_SECONDS)
        target = float(self.duration_target_seconds)
        if target < lo + 30:  # mục tiêu sát sàn → audio đo thật dễ hụt dưới 10 phút
            target = lo + 60
        hi = max(float(self.duration_max_seconds), target + 60)
        object.__setattr__(self, "duration_min_seconds", lo)
        object.__setattr__(self, "duration_target_seconds", target)
        object.__setattr__(self, "duration_max_seconds", hi)
        return self

    @property
    def is_demo(self) -> bool:
        return self.app_mode == "demo"

    @property
    def allowed_chat_ids(self) -> set[str]:
        return {c.strip() for c in self.telegram_allowed_chat_ids.split(",") if c.strip()}

    def secret_bytes(self) -> bytes:
        if self.secret_key:
            return self.secret_key.get_secret_value().encode()
        if self.app_mode == "production":
            raise RuntimeError("SECRET_KEY bắt buộc ở chế độ production")
        return b"demo-only-insecure-secret-key-do-not-use-in-production"


# ---------------------------------------------------------------------------------------------
# Cấu hình nhiều lớp: mặc định < biến môi trường/.env < giá trị chủ kênh lưu trong trang Cấu hình (DB, mã hoá).
# SECRET_KEY, DATABASE_URL, các thư mục là cấu hình hạ tầng → chỉ đọc từ env (không sửa qua giao diện).

_base: Settings | None = None
_cached: tuple[float, Settings] | None = None
_lock = threading.RLock()
CACHE_TTL_SECONDS = 15.0


def _secret_file(data_dir: Path) -> Path:
    return data_dir / ".secret_key"


def get_base_settings() -> Settings:
    """Settings từ env; tự sinh SECRET_KEY lưu ở data/.secret_key nếu chưa có (để mã hoá key API)."""
    global _base
    with _lock:
        if _base is None:
            s = Settings()
            if not s.secret_key:
                f = _secret_file(s.data_dir)
                if not f.exists():
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(_secrets.token_urlsafe(48), encoding="utf-8")
                    try:
                        f.chmod(0o600)
                    except OSError:  # Windows
                        pass
                s.secret_key = SecretStr(f.read_text(encoding="utf-8").strip())
            _base = s
        return _base


def get_settings() -> Settings:
    global _cached
    with _lock:
        now = time.monotonic()
        if _cached and now - _cached[0] < CACHE_TTL_SECONDS:
            return _cached[1]
        base = get_base_settings()
        from .runtime_config import load_overrides  # import muộn: tránh vòng lặp

        ov = load_overrides(base)
        if ov:
            data = {k: v for k, v in base.model_dump().items()}
            data.update(ov)
            try:
                merged = Settings.model_validate(data)
            except Exception:  # giá trị DB hỏng không được làm sập ứng dụng
                merged = base
        else:
            merged = base
        _cached = (now, merged)
        return merged


def reset_settings_cache(base_too: bool = False) -> None:
    global _cached, _base
    with _lock:
        _cached = None
        if base_too:
            _base = None
