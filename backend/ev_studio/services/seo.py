"""SEO: tiêu đề (3 phương án), mô tả có nguồn + ngày truy cập, chương, tags, hashtag."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import get_settings

BASE_TAGS = ["xe điện", "xe điện Việt Nam", "trạm sạc", "pin xe điện", "chi phí xe điện"]
PILLAR_TAGS = {"P1": ["mua xe điện lần đầu", "tổng chi phí sở hữu"], "P2": ["mẹo dùng xe điện", "tiết kiệm điện"],
               "P3": ["chính sách xe điện", "hạ tầng sạc"]}
PILLAR_HASHTAG = {"P1": "#muaxedien", "P2": "#meoxedien", "P3": "#tramsac"}


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def chapters_from_scenes(scenes: list[dict], paragraphs_titles: list[tuple[float, str]] | None = None) -> list[dict]:
    """Chương YouTube: bắt đầu 00:00, ≥3 chương, mỗi chương ≥10s."""
    marks = paragraphs_titles or []
    out = [{"t": 0.0, "title": "Mở đầu: câu hỏi chính"}]
    for t, title in marks:
        if t - out[-1]["t"] >= 60:
            out.append({"t": t, "title": title})
    return out if len(out) >= 3 else out[:1]


def build_description(*, main_angle: str, summary_line: str, chapters: list[dict], sources: list[dict],
                      asset_credits: list[str], has_estimates: bool, pillar: str, is_demo: bool) -> str:
    tz = ZoneInfo(get_settings().timezone)
    access = datetime.now(tz).strftime("%d/%m/%Y")
    lines = []
    if is_demo:
        lines.append("⚠️ BẢN DEMO — DỮ LIỆU GIẢ LẬP, KHÔNG PHẢI TIN THẬT.")
    lines += [main_angle.strip(), summary_line.strip(), ""]
    if len(chapters) >= 3:
        lines.append("Mục lục:")
        lines += [f"{fmt_ts(c['t'])} {c['title']}" for c in chapters]
        lines.append("")
    lines.append("Nguồn tham khảo chính:")
    for s in sources:
        pub = _fmt_date(s.get("published_at"))
        lines.append(f"- {s['publisher']} ({pub}): {s['url']} — truy cập {access}")
    lines.append("")
    if has_estimates:
        lines.append("Các phép tính trong video là ví dụ minh họa dựa trên giả định được nêu rõ, không phải báo giá.")
    if asset_credits:
        lines.append("Ghi công hình ảnh: " + "; ".join(asset_credits))
    lines.append("Kênh độc lập, không phải kênh chính thức của VinFast hay bất kỳ hãng xe nào. Giọng đọc tạo bằng AI (VieNeu-TTS).")
    lines.append("")
    lines.append("Bạn nghĩ sao về điều kiện trong video? Hãy để lại bình luận.")
    lines.append("")
    lines.append(" ".join(["#xedien", PILLAR_HASHTAG[pillar], "#vietnam"]))
    text = "\n".join(lines)
    return text[:4900]


def _fmt_date(v) -> str:
    if not v:
        return "không rõ ngày đăng"
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return str(v)[:10]


def build_tags(pillar: str, model_names: list[str], entities: list[str], idem_tag: str) -> list[str]:
    tags = [idem_tag] + PILLAR_TAGS[pillar] + BASE_TAGS + model_names + entities
    out, total = [], 0
    for t in tags:
        t = t.replace("<", "").replace(">", "").strip()
        if t and t not in out and total + len(t) + 2 <= 480:
            out.append(t)
            total += len(t) + 2
    return out
