"""Cấu hình API do chủ kênh nhập trong trang Cấu hình của backend.

Lưu trong bảng `app_settings` với khoá `cfg:<tên>`; giá trị bí mật (key, token, client secret) được mã hoá Fernet
bằng SECRET_KEY (tự sinh ở data/.secret_key). Không bao giờ trả giá trị bí mật ra API — chỉ trả dạng che `••••abcd`.
Thứ tự ưu tiên: mặc định < env/.env < giá trị trong trang Cấu hình.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "cfg:"


@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    group: str
    kind: str = "text"  # text | secret | bool | int | float | select | textarea_secret
    options: tuple[str, ...] = ()
    help: str = ""
    placeholder: str = ""
    restart: bool = False  # cần khởi động lại để có hiệu lực


GROUPS = {
    "mode": "Chế độ chạy",
    "anthropic": "Claude (Anthropic API)",
    "tts": "Giọng đọc VieNeu-TTS",
    "youtube": "YouTube (OAuth)",
    "telegram": "Telegram",
    "media": "Kho tư liệu",
    "editorial": "Biên tập & thời lượng",
    "render": "Dựng video & job",
    "security": "Bảo mật dashboard",
}

FIELDS: list[ConfigField] = [
    ConfigField("app_mode", "Chế độ", "mode", "select", ("demo", "production"),
                "demo: adapter giả lập gắn nhãn DEMO, không upload thật. production: dùng dịch vụ thật; thiếu key → video dừng ở BLOCKED_BY_CREDENTIALS."),
    ConfigField("public_base_url", "Địa chỉ truy cập ứng dụng", "mode", "text",
                help="Dùng cho link xem trước trong Telegram và OAuth redirect. Chạy máy cá nhân: http://localhost:8000",
                placeholder="http://localhost:8000"),
    ConfigField("anthropic_api_key", "Anthropic API key", "anthropic", "secret",
                help="Tạo tại console.anthropic.com → API Keys. Gói Claude trên web KHÔNG bao gồm quyền API.", placeholder="sk-ant-…"),
    ConfigField("anthropic_model", "Model", "anthropic", "text", help="Bấm 'Kiểm tra' để xem danh sách model tài khoản được dùng.",
                placeholder="claude-sonnet-5-5"),
    ConfigField("anthropic_max_tokens", "Max tokens mỗi lần viết", "anthropic", "int"),
    ConfigField("writer_provider", "Bộ viết", "anthropic", "select", ("auto", "demo", "anthropic"),
                "auto: demo khi ở chế độ demo và chưa có key; ngược lại dùng Anthropic."),
    ConfigField("tts_provider", "Cách chạy VieNeu", "tts", "select", ("auto", "demo", "vieneu_local", "vieneu_http"),
                "vieneu_local: thư viện `vieneu` cài cùng máy (CPU/ONNX). vieneu_http: server VieNeu riêng (máy GPU)."),
    ConfigField("vieneu_http_url", "Địa chỉ server VieNeu", "tts", "text", placeholder="http://192.168.1.20:8001"),
    ConfigField("vieneu_voice", "Giọng mặc định", "tts", "text", help="Gợi ý: Quang Sơn, Minh Quân Pro, Thanh Bình…"),
    ConfigField("vieneu_backend", "Backend VieNeu (local)", "tts", "select", ("", "onnx"), "Để trống = tự chọn GPU nếu có; onnx = ép CPU.",
                restart=True),
    ConfigField("google_client_secrets_json", "OAuth client (nội dung client_secret.json)", "youtube", "textarea_secret",
                help="Google Cloud Console → bật YouTube Data API v3 → Credentials → OAuth client ID loại 'Web application' "
                     "→ thêm Authorized redirect URI hiển thị bên dưới → tải JSON và dán vào đây."),
    ConfigField("youtube_expected_channel_id", "ID kênh đích (UC…)", "youtube", "text",
                help="Kênh OAuth khác ID này sẽ bị chặn upload.", placeholder="UCxxxxxxxxxxxxxxxxxxxxxx"),
    ConfigField("youtube_made_for_kids_confirmed_false", "Tôi xác nhận nội dung KHÔNG dành cho trẻ em", "youtube", "bool"),
    ConfigField("youtube_contains_synthetic_media", "Khai báo nội dung tổng hợp (giọng AI)", "youtube", "bool"),
    ConfigField("youtube_category_id", "Category ID", "youtube", "text", help="2 = Autos & Vehicles"),
    ConfigField("telegram_bot_token", "Bot token", "telegram", "secret", help="Tạo bot với @BotFather.", placeholder="123456:ABC…"),
    ConfigField("telegram_allowed_chat_ids", "Chat ID được phép", "telegram", "text",
                help="Nhắn /start cho bot rồi bấm 'Kiểm tra' để thấy chat ID. Nhiều ID cách nhau bằng dấu phẩy."),
    ConfigField("telegram_webhook_secret", "Webhook secret (chỉ khi chạy VPS HTTPS)", "telegram", "secret",
                help="Để trống khi chạy trên máy cá nhân → dùng long-polling.", restart=True),
    ConfigField("pexels_api_key", "Pexels API key", "media", "secret", help="Miễn phí tại pexels.com/api (tuỳ chọn)."),
    ConfigField("wikimedia_enabled", "Tìm ảnh trên Wikimedia Commons", "media", "bool"),
    ConfigField("wikimedia_user_agent", "User-Agent gửi Wikimedia (kèm email liên hệ)", "media", "text"),
    ConfigField("duration_target_seconds", "Thời lượng mục tiêu (giây)", "editorial", "float"),
    ConfigField("duration_min_seconds", "Tối thiểu (giây)", "editorial", "float"),
    ConfigField("duration_max_seconds", "Tối đa (giây)", "editorial", "float"),
    ConfigField("score_autopilot", "Ngưỡng điểm Autopilot", "editorial", "int"),
    ConfigField("max_videos_per_week", "Tối đa video/tuần (Autopilot)", "editorial", "int"),
    ConfigField("scan_interval_hours", "Chu kỳ quét tin (giờ)", "editorial", "float", restart=True),
    ConfigField("render_preset", "FFmpeg preset", "render", "select", ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")),
    ConfigField("render_burn_subtitles", "Gắn cứng phụ đề vào video", "render", "bool"),
    ConfigField("job_concurrency", "Số job chạy song song", "render", "int", restart=True),
    ConfigField("admin_token", "Mật khẩu chủ kênh (ADMIN_TOKEN)", "security", "secret",
                help="Bắt buộc khi truy cập từ máy khác. Trên chính máy chạy ứng dụng (127.0.0.1) không cần nhập."),
    ConfigField("editor_token", "Mật khẩu biên tập viên (không được upload)", "security", "secret"),
]
BY_KEY = {f.key: f for f in FIELDS}
SECRET_KINDS = {"secret", "textarea_secret"}


def _fernet(secret: bytes) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"evcs-config|" + secret).digest()))


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def load_overrides(base) -> dict[str, Any]:
    """Đọc giá trị trong DB. Lỗi (bảng chưa tạo, key sai) → bỏ qua, không làm sập ứng dụng."""
    try:
        from sqlalchemy import select

        from .db import get_engine
        from .models import AppSetting

        with get_engine().connect() as conn:
            rows = conn.execute(select(AppSetting.key, AppSetting.value).where(AppSetting.key.like(PREFIX + "%"))).all()
    except Exception:
        return {}
    f = _fernet(base.secret_bytes())
    out: dict[str, Any] = {}
    for k, v in rows:
        name = k[len(PREFIX):]
        fld = BY_KEY.get(name)
        if fld is None or not isinstance(v, dict):
            continue
        if fld.kind in SECRET_KINDS:
            try:
                out[name] = f.decrypt(v["enc"].encode()).decode()
            except (InvalidToken, KeyError):
                continue
        else:
            out[name] = v.get("v")
    return out


def coerce(fld: ConfigField, value: Any) -> Any:
    if fld.kind == "bool":
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    if fld.kind == "int":
        return int(value)
    if fld.kind == "float":
        return float(value)
    if fld.kind == "select" and fld.options and value not in fld.options:
        raise ValueError(f"{fld.label}: giá trị phải là một trong {list(fld.options)}")
    v = "" if value is None else str(value)
    if fld.key == "google_client_secrets_json" and v.strip():
        data = json.loads(v)
        if not ("web" in data or "installed" in data):
            raise ValueError("client_secret.json không hợp lệ (thiếu khoá 'web' hoặc 'installed')")
    return v.strip() if fld.kind != "textarea_secret" else v


@dataclass
class SaveResult:
    updated: list[str] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)
    restart_needed: list[str] = field(default_factory=list)


def save(session, values: dict[str, Any], clear: list[str], actor: str) -> SaveResult:
    """values: chỉ các trường muốn đổi. Trường bí mật để trống = giữ nguyên. clear: xoá giá trị trong DB (quay về env)."""
    from .config import Settings, get_base_settings, get_settings, reset_settings_cache
    from .models import AppSetting
    from .security import audit

    base = get_base_settings()
    f = _fernet(base.secret_bytes())
    res = SaveResult()
    staged: dict[str, Any] = {}
    for name, raw in values.items():
        fld = BY_KEY.get(name)
        if fld is None:
            raise ValueError(f"Không hỗ trợ cấu hình '{name}' qua giao diện")
        if fld.kind in SECRET_KINDS and (raw is None or str(raw) == ""):
            continue
        try:
            staged[name] = coerce(fld, raw)
        except (ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"{fld.label}: {e}") from e
    # validate toàn bộ cấu hình sau khi gộp trước khi ghi
    merged = get_settings().model_dump()
    merged.update(staged)
    for c in clear:
        merged.pop(c, None)
    Settings.model_validate(merged)
    if merged.get("duration_min_seconds", 0) >= merged.get("duration_max_seconds", 1):
        raise ValueError("Thời lượng tối thiểu phải nhỏ hơn tối đa")
    for name, v in staged.items():
        fld = BY_KEY[name]
        stored = {"enc": f.encrypt(str(v).encode()).decode()} if fld.kind in SECRET_KINDS else {"v": v}
        row = session.get(AppSetting, PREFIX + name)
        if row:
            row.value = stored
        else:
            session.add(AppSetting(key=PREFIX + name, value=stored))
        res.updated.append(name)
        if fld.restart:
            res.restart_needed.append(name)
    for name in clear:
        if name not in BY_KEY:
            raise ValueError(f"Không hỗ trợ cấu hình '{name}'")
        row = session.get(AppSetting, PREFIX + name)
        if row:
            session.delete(row)
            res.cleared.append(name)
    audit(session, actor, "config.update", "config", None, updated=",".join(res.updated), cleared=",".join(res.cleared))
    session.commit()
    reset_settings_cache()
    return res


def describe(session) -> dict:
    """Mô tả cho giao diện: giá trị hiện hành (che bí mật) + nguồn (mặc định / env / giao diện)."""
    from sqlalchemy import select

    from .config import Settings, get_base_settings, get_settings
    from .models import AppSetting

    cur = get_settings()
    base = get_base_settings()
    defaults = Settings.model_construct()
    in_db = {k[len(PREFIX):] for (k,) in session.execute(select(AppSetting.key).where(AppSetting.key.like(PREFIX + "%")))}
    items = []
    for fld in FIELDS:
        val = getattr(cur, fld.key)
        raw = val.get_secret_value() if hasattr(val, "get_secret_value") else val
        default = getattr(defaults, fld.key, None)
        default_raw = default.get_secret_value() if hasattr(default, "get_secret_value") else default
        base_v = getattr(base, fld.key)
        base_raw = base_v.get_secret_value() if hasattr(base_v, "get_secret_value") else base_v
        source = "ui" if fld.key in in_db else ("env" if base_raw != default_raw else "default")
        item = {**asdict(fld), "source": source}
        if fld.kind in SECRET_KINDS:
            item.update(is_set=bool(raw), value=None, masked=mask(raw) if fld.kind == "secret" else ("đã lưu" if raw else None))
        else:
            item["value"] = "" if raw is None else (str(raw) if not isinstance(raw, (bool, int, float)) else raw)
        items.append(item)
    return {"groups": GROUPS, "fields": items,
            "oauth_redirect_uri": cur.public_base_url.rstrip("/") + "/api/oauth/google/callback"}
