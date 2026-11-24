"""Cấu hình ứng dụng. Không chứa secret mặc định; secrets đọc từ env/.env."""
from __future__ import annotations

import secrets as _secrets
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = Path(__file__).resolve().parent
FONT_DIR = PKG_DIR / "assets" / "fonts"


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
    writer_provider: Literal["auto", "demo", "anthropic"] = "auto"

    # --- TTS ---
    tts_provider: Literal["auto", "demo", "vieneu_local", "vieneu_http"] = "auto"
    vieneu_mode: str = "v3turbo"
    vieneu_backend: str | None = None  # "onnx" để ép CPU
    vieneu_http_url: str = "http://localhost:8001"
    vieneu_voice: str = "Quang Sơn"
    vieneu_model_version: str = "vieneu-3.8.5-v3turbo"

    # --- media ---
    pexels_api_key: SecretStr | None = None
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
    duration_target_seconds: float = 600
    duration_min_seconds: float = 570
    duration_max_seconds: float = 630
    max_script_revisions: int = 3
    score_shortlist: int = 75
    score_research: int = 60
    score_autopilot: int = 80
    max_videos_per_week: int = 3
    scan_interval_hours: float = 3
    freshness_max_age_hours: float = 24

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
