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
    "budget": "Ngân sách AI (Ban Tài chính)",
    "agents": "Bộ não phòng ban (học từ Claude/ChatGPT)",
    "auto": "Tự động hoàn toàn",
    "growth": "Ban Tăng trưởng & KPI doanh thu",
    "essay": "Phòng Bài luận (research web + viết bài luận)",
    "anthropic": "Claude (Anthropic API)",
    "tts": "Giọng đọc VieNeu-TTS",
    "youtube": "YouTube (OAuth)",
    "telegram": "Telegram",
    "media": "Kho tư liệu (Pixabay · Pexels · Wikimedia)",
    "ai_images": "Ảnh AI minh họa (Cloudflare / fal.ai)",
    "editorial": "Biên tập & thời lượng",
    "render": "Dựng video & job",
    "security": "Bảo mật dashboard",
}

FIELDS: list[ConfigField] = [
    ConfigField("app_mode", "Chế độ", "mode", "select", ("demo", "production"),
                "demo: adapter giả lập gắn nhãn DEMO, không upload thật. production: dùng dịch vụ thật, "
                "lưu sẽ XOÁ HẾT dữ liệu DEMO; thiếu key → video dừng ở BLOCKED_BY_CREDENTIALS."),
    ConfigField("public_base_url", "Địa chỉ truy cập ứng dụng", "mode", "text",
                help="Dùng cho link xem trước trong Telegram và OAuth redirect. Chạy máy cá nhân: http://localhost:8010",
                placeholder="http://localhost:8010"),
    ConfigField("anthropic_api_key", "Anthropic API key", "anthropic", "secret",
                help="Tạo tại console.anthropic.com → API Keys. Gói Claude trên web KHÔNG bao gồm quyền API.", placeholder="sk-ant-…"),
    ConfigField("anthropic_model", "Model", "anthropic", "text", help="Bấm 'Kiểm tra' để xem danh sách model tài khoản được dùng.",
                placeholder="claude-sonnet-5-5"),
    ConfigField("anthropic_max_tokens", "Max tokens mỗi lần viết", "anthropic", "int"),
    ConfigField("writer_provider", "Bộ viết", "anthropic", "select", ("auto", "demo", "agents", "anthropic"),
                "auto/agents: các phòng ban (bộ nhớ + thầy + trò). anthropic: chỉ dùng thầy Claude. "
                "Mọi lựa chọn thật đều bị Ban Tài chính giới hạn chi tiêu."),
    ConfigField("budget_monthly_usd", "Trần chi tiêu AI mỗi tháng (USD)", "budget", "float",
                help="Trần CỨNG cho Claude + ChatGPT. Chạm trần: video dừng ở NEEDS_REVIEW (BLOCKED_BY_BUDGET), không tiêu thêm."),
    ConfigField("budget_safety_margin", "Phần trăm giữ lại phòng sai số (0.05 = 5%)", "budget", "float"),
    ConfigField("budget_story_max_usd", "Trần chi cho một video (USD)", "budget", "float",
                help="Gồm ResearchPack, kịch bản và các lần viết lại cho đúng thời lượng."),
    ConfigField("stories_per_day", "Số tin tối đa mỗi ngày (Autopilot)", "budget", "int"),
    ConfigField("stories_per_day_min", "Số tin tối thiểu mong muốn mỗi ngày", "budget", "int"),
    ConfigField("model_prices_json", "Bảng giá bổ sung (JSON)", "budget", "text",
                help='Bắt buộc cho model ChatGPT. Ví dụ {"ten-model": [gia_vao, gia_ra]} USD/1 triệu token.'),
    ConfigField("agents_enabled", "Bật bộ não phòng ban", "agents", "bool"),
    ConfigField("teacher_strategy", "Thầy chính", "agents", "select", ("anthropic", "openai", "alternate"),
                "alternate: luân phiên Claude và ChatGPT để các phòng học văn phong của cả hai (cùng chi phí)."),
    ConfigField("model_claims", "Model Claude — Phòng Săn tin", "agents", "text", placeholder="claude-haiku-5-5"),
    ConfigField("model_research", "Model Claude — Phòng Nghiên cứu", "agents", "text", placeholder="claude-sonnet-5-5"),
    ConfigField("model_script", "Model Claude — Phòng Biên kịch (trống = model mặc định)", "agents", "text"),
    ConfigField("model_editor", "Model Claude — Phòng Biên tập & Ban Đào tạo", "agents", "text", placeholder="claude-haiku-5-5"),
    ConfigField("model_director", "Model Claude — Phòng Đạo diễn hình ảnh", "agents", "text", placeholder="claude-haiku-5-5"),
    ConfigField("openai_api_key", "OpenAI API key (ChatGPT, tuỳ chọn)", "agents", "secret", placeholder="sk-…"),
    ConfigField("openai_model", "Model ChatGPT", "agents", "text", help="Nhớ khai giá model này trong Bảng giá bổ sung."),
    ConfigField("local_llm_url", "Địa chỉ Ollama (trò chạy trên máy)", "agents", "text", placeholder="http://localhost:11434",
                help="Cài Ollama, chạy `ollama pull qwen2.5:7b-instruct`. Để trống = chưa có trò, mọi việc do thầy làm."),
    ConfigField("local_llm_model", "Model trò", "agents", "text", placeholder="qwen2.5:7b-instruct"),
    ConfigField("agent_shadow_learning", "Trò làm thử mỗi đề của thầy để học (miễn phí)", "agents", "bool"),
    ConfigField("agent_graduation_min", "Số lần thử tối thiểu trước khi tốt nghiệp", "agents", "int"),
    ConfigField("agent_graduation_rate", "Tỷ lệ đạt để tốt nghiệp (0.8 = 80%)", "agents", "float"),
    ConfigField("agent_audit_rate", "Tỷ lệ việc vẫn gửi thầy sau khi tốt nghiệp", "agents", "float"),
    ConfigField("agent_master_min_passes", "Bậc thầy: số lần trò đạt tối thiểu", "agents", "int"),
    ConfigField("agent_master_rate", "Bậc thầy: tỷ lệ đạt (0.95 = 95%)", "agents", "float"),
    ConfigField("agent_master_audit_rate", "Bậc thầy: tỷ lệ việc vẫn gửi thầy kiểm tra", "agents", "float",
                help="0 = phòng đạt Bậc thầy tự làm hoàn toàn, chỉ nhờ thầy khi bài của trò không qua QA."),
    ConfigField("full_auto", "Bật Tự động hoàn toàn", "auto", "bool",
                help="Tự quét tin, tự chọn tin, tự làm video, tự sửa lỗi (không được thì bỏ tin), tự tải lên và tự công khai sau "
                     "thời gian chờ nếu bạn không bấm Dừng trên Telegram. Luôn trong trần ngân sách AI."),
    ConfigField("auto_publish_delay_minutes", "Tự công khai sau (phút) — 0 = không tự công khai", "auto", "int",
                help="Video tải lên RIÊNG TƯ, Telegram báo kèm nút Dừng; hết thời gian này mà không bị Dừng thì app công khai."),
    ConfigField("auto_heal_max_attempts", "Số lần tự chạy lại video lỗi trước khi bỏ", "auto", "int"),
    ConfigField("kpi_monthly_revenue_usd", "KPI doanh thu quảng cáo mỗi tháng (USD)", "growth", "float",
                help="Doanh thu quảng cáo YouTube ước tính (estimatedAdRevenue). 0 = chưa đặt KPI. Ban Tăng trưởng "
                     "đối chiếu mỗi ngày và tự chỉnh kế hoạch sản xuất để bám KPI."),
    ConfigField("growth_auto_apply", "Ban Tăng trưởng tự áp dụng kế hoạch", "growth", "bool",
                help="Bật: tự đổi số video/ngày và ưu tiên trụ cột cho Autopilot, trong trần ngân sách. "
                     "Không bao giờ tự công khai video, không tự tăng trần chi tiêu."),
    ConfigField("growth_max_stories_per_day", "Trần số video/ngày Ban Tăng trưởng được đặt", "growth", "int"),
    ConfigField("model_growth", "Model Claude — Ban Tăng trưởng (bản ghi nhớ hằng tuần)", "growth", "text",
                placeholder="claude-haiku-5-5"),
    ConfigField("model_essay", "Model Claude — Phòng Bài luận", "essay", "text", placeholder="claude-sonnet-5-5"),
    ConfigField("essay_teacher_strategy", "Thầy của Phòng Bài luận", "essay", "select", ("alternate", "anthropic", "openai", "inherit"),
                "alternate: luân phiên Claude và ChatGPT để trò học cách viết của cả hai. inherit: theo 'Thầy chính'."),
    ConfigField("essay_language", "Ngôn ngữ mặc định", "essay", "select", ("vi", "en")),
    ConfigField("essay_default_words", "Độ dài mặc định (số từ)", "essay", "int"),
    ConfigField("research_search_provider", "Nguồn tìm kiếm", "essay", "select", ("auto", "wikipedia", "searxng", "brave", "fixture"),
                "auto: Wikipedia (miễn phí, không cần key) + SearXNG/Brave nếu có cấu hình. fixture: tư liệu giả lập DEMO."),
    ConfigField("research_online_in_demo", "Chế độ DEMO vẫn tìm trên mạng thật", "essay", "bool"),
    ConfigField("research_wiki_langs", "Ngôn ngữ Wikipedia", "essay", "text", placeholder="vi,en"),
    ConfigField("searxng_url", "Địa chỉ SearXNG (tuỳ chọn)", "essay", "text", placeholder="http://localhost:8888",
                help="Máy tìm kiếm mã nguồn mở tự cài, miễn phí. Nhớ bật định dạng json trong settings.yml."),
    ConfigField("brave_api_key", "Brave Search API key (tuỳ chọn)", "essay", "secret",
                help="Gói miễn phí tại api-dashboard.search.brave.com, khoảng 2.000 lượt tìm/tháng."),
    ConfigField("research_max_sources", "Số nguồn tối đa mỗi bài", "essay", "int"),
    ConfigField("research_max_fetch", "Số trang web tải tối đa mỗi bài", "essay", "int"),
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
    ConfigField("pixabay_api_key", "Pixabay API key", "media", "secret",
                help="Miễn phí, cấp ngay: đăng ký tài khoản pixabay.com, đăng nhập rồi mở pixabay.com/api/docs — key nằm sẵn "
                     "trong mục Parameters. Nguồn video B-roll + ảnh chính của app."),
    ConfigField("pexels_api_key", "Pexels API key (chỉ khi đã có key cũ)", "media", "secret",
                help="Pexels đã tạm dừng cấp key mới (từ cuối 9/2026). Có key cũ thì nhập để thêm nguồn; không có thì bỏ trống."),
    ConfigField("wikimedia_enabled", "Tìm ảnh trên Wikimedia Commons", "media", "bool"),
    ConfigField("wikimedia_user_agent", "User-Agent gửi Wikimedia (kèm email liên hệ)", "media", "text"),
    ConfigField("fact_mode", "Cách xử lý tin chưa xác minh", "editorial", "select", ("analysis", "strict"),
                help="analysis (mặc định): video là bài PHÂN TÍCH tin báo chí đã đăng — tin mới có một báo đưa vẫn làm được, "
                     "kịch bản phải ghi rõ \"theo <tên báo>\"; con số/trích dẫn phải có trong bài gốc. strict: chỉ dùng thông tin "
                     "đã được nguồn chính thức hoặc ≥2 báo uy tín xác nhận, tin chưa xác minh bị chặn."),
    ConfigField("duration_target_seconds", "Thời lượng mục tiêu (giây)", "editorial", "float",
                help="Mặc định 660 giây (11 phút) để audio đo thật có biên an toàn trên 10 phút."),
    ConfigField("duration_min_seconds", "Tối thiểu (giây)", "editorial", "float",
                help="Không được dưới 600 giây (10 phút). Video ngắn hơn bị chặn, không render/upload."),
    ConfigField("duration_max_seconds", "Tối đa (giây)", "editorial", "float"),
    ConfigField("score_autopilot", "Ngưỡng điểm Autopilot", "editorial", "int"),
    ConfigField("max_videos_per_week", "Tối đa video/tuần (Autopilot)", "editorial", "int"),
    ConfigField("scan_interval_hours", "Chu kỳ quét tin (giờ)", "editorial", "float", restart=True),
    ConfigField("channel_brand", "Chữ logo góc màn hình (trống = tên kênh)", "render", "text"),
    ConfigField("music_volume_db", "Âm lượng nhạc nền (dB, ví dụ -20)", "render", "float",
                help="Nhạc lấy từ media_library/music (mỗi file kèm .license.json, ví dụ nhạc tải từ YouTube Audio Library). "
                     "Tự giảm thêm khi có giọng đọc."),
    ConfigField("ai_image_provider", "Nguồn ảnh AI", "ai_images", "select", ("auto", "cloudflare", "fal", "off"),
                "auto: Cloudflare Workers AI (miễn phí ~150 ảnh/ngày) trước, hết hạn mức hoặc thiếu key thì dùng fal.ai "
                "(~0,003 USD/ảnh, tính vào trần ngân sách AI). Ảnh AI chỉ lấp cảnh không có video/ảnh thật phù hợp."),
    ConfigField("ai_images_per_video", "Số ảnh AI tối đa mỗi video", "ai_images", "int"),
    ConfigField("cloudflare_account_id", "Cloudflare Account ID", "ai_images", "text",
                help="dash.cloudflare.com → chọn tài khoản → Account ID ở trang tổng quan."),
    ConfigField("cloudflare_api_token", "Cloudflare API token (quyền Workers AI)", "ai_images", "secret",
                help="My Profile → API Tokens → Create Token → mẫu 'Workers AI'."),
    ConfigField("cloudflare_daily_images", "Cloudflare: số ảnh tối đa/ngày (giữ trong mức miễn phí)", "ai_images", "int"),
    ConfigField("fal_api_key", "fal.ai API key", "ai_images", "secret", help="fal.ai/dashboard/keys. Dịch vụ trả phí, nạp trước."),
    ConfigField("fal_model", "fal.ai model", "ai_images", "text", placeholder="fal-ai/flux-1/schnell"),
    ConfigField("fal_price_per_image", "fal.ai: giá mỗi ảnh (USD) để Ban Tài chính tính", "ai_images", "float"),
    ConfigField("stock_max_queries", "Số từ khoá tìm tư liệu tối đa mỗi video", "media", "int"),
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
    demo_purged: dict | None = None  # có giá trị khi vừa chuyển sang production và đã xoá dữ liệu DEMO


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
    if not merged.get("allow_short_duration_for_tests"):
        from .config import MIN_VIDEO_SECONDS

        for k in ("duration_min_seconds", "duration_target_seconds", "duration_max_seconds"):
            if k in staged and float(staged[k]) < MIN_VIDEO_SECONDS:
                raise ValueError(f"{BY_KEY[k].label}: video không được dưới 10 phút ({MIN_VIDEO_SECONDS:.0f} giây)")
    if merged.get("duration_min_seconds", 0) >= merged.get("duration_max_seconds", 1):
        raise ValueError("Thời lượng tối thiểu phải nhỏ hơn tối đa")
    if not merged.get("duration_min_seconds", 0) <= merged.get("duration_target_seconds", 0) <= merged.get("duration_max_seconds", 0):
        raise ValueError("Thời lượng mục tiêu phải nằm giữa tối thiểu và tối đa")
    if not 0 <= int(merged.get("auto_publish_delay_minutes", 120)) <= 7 * 24 * 60:
        raise ValueError("Thời gian chờ tự công khai phải trong khoảng 0–10080 phút")
    if merged.get("full_auto") and 0 < int(merged.get("auto_publish_delay_minutes", 120)) < 15:
        raise ValueError("Thời gian chờ tự công khai tối thiểu 15 phút để bạn kịp bấm Dừng")
    if not 0 <= int(merged.get("auto_heal_max_attempts", 2)) <= 10:
        raise ValueError("Số lần tự chạy lại phải trong khoảng 0–10")
    if not 0 <= float(merged.get("kpi_monthly_revenue_usd", 0)) <= 10_000_000:
        raise ValueError("KPI doanh thu tháng phải là số USD không âm")
    if not 1 <= int(merged.get("growth_max_stories_per_day", 6)) <= 24:
        raise ValueError("Trần số video/ngày của Ban Tăng trưởng phải trong khoảng 1–24")
    if not 0 < float(merged.get("budget_monthly_usd", 50)) <= 10000:
        raise ValueError("Trần chi tiêu tháng phải trong khoảng 0–10.000 USD")
    if not 0 <= float(merged.get("budget_safety_margin", 0.05)) < 0.5:
        raise ValueError("Phần trăm giữ lại phải trong khoảng 0–0.5")
    if int(merged.get("stories_per_day_min", 2)) > int(merged.get("stories_per_day", 3)):
        raise ValueError("Số tin tối thiểu/ngày không được lớn hơn tối đa")
    if merged.get("model_prices_json"):
        from .agents.pricing import price_table

        price_table(merged["model_prices_json"])  # JSON sai → GateBlocked
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
    # Chọn chạy thật → xoá sạch dữ liệu DEMO, cùng một transaction với việc đổi chế độ (lỗi thì không đổi gì cả)
    purge = None
    if merged.get("app_mode") == "production" and ("app_mode" in staged or "app_mode" in clear):
        from .demo import purge_demo_data

        purge = purge_demo_data(session, actor)  # còn video DEMO đang chạy → DemoPurgeBlocked (ValueError)
    audit(session, actor, "config.update", "config", None, updated=",".join(res.updated), cleared=",".join(res.cleared))
    session.commit()
    reset_settings_cache()
    if purge is not None:
        from .demo import remove_demo_files

        purge["files_removed"] = remove_demo_files(purge.pop("files"))
        res.demo_purged = purge
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
