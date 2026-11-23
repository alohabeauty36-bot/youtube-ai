"""Đồ họa nội bộ tự dựng (Pillow) 1920×1080: thẻ dữ kiện có nguồn, công thức, timeline, lợi ích/rủi ro..."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from ...config import get_settings

W, H = 1920, 1080
BG = (12, 20, 33)
BG2 = (20, 34, 54)
ACCENT = (46, 204, 113)
ACCENT2 = (52, 152, 219)
WARN = (241, 196, 15)
RED = (231, 76, 60)
FG = (240, 244, 248)
MUTED = (150, 165, 185)


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    s = get_settings()
    path = s.font_bold_path if bold else s.font_path
    return ImageFont.truetype(str(path), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _base(demo: bool, kicker: str | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for y in range(H):  # gradient dọc
        k = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(BG[i] * (1 - k) + BG2[i] * k) for i in range(3)))
    d.rectangle([0, 0, 18, H], fill=ACCENT)
    if kicker:
        d.text((90, 70), kicker.upper(), font=font(34, True), fill=ACCENT)
    if demo:
        badge = "DEMO — DỮ LIỆU GIẢ LẬP"
        f = font(28, True)
        tw = d.textlength(badge, font=f)
        d.rounded_rectangle([W - tw - 90, 40, W - 50, 92], 10, fill=RED)
        d.text((W - tw - 70, 48), badge, font=f, fill=FG)
    return img, d


def _footer(d: ImageDraw.ImageDraw, text: str | None):
    if text:
        f = font(26)
        lines = wrap(d, text, f, W - 200)[:2]
        y = H - 60 - 34 * len(lines)
        for ln in lines:
            d.text((90, y), ln, font=f, fill=MUTED)
            y += 34


def _paragraph(d, text, f, x, y, max_w, fill=FG, spacing=1.25, max_lines=8):
    for ln in wrap(d, text, f, max_w)[:max_lines]:
        d.text((x, y), ln, font=f, fill=fill)
        y += int(f.size * spacing)
    return y


def title_card(path: Path, title: str, subtitle: str = "", demo: bool = False, kicker: str = "Phân tích") -> Path:
    img, d = _base(demo, kicker)
    y = _paragraph(d, title, font(84, True), 90, 300, W - 260, max_lines=4)
    if subtitle:
        _paragraph(d, subtitle, font(42), 90, y + 40, W - 260, fill=MUTED, max_lines=3)
    return _save(img, path)


def question_card(path: Path, question: str, demo: bool = False) -> Path:
    img, d = _base(demo, "Câu hỏi còn mở")
    d.text((90, 170), "?", font=font(260, True), fill=WARN)
    _paragraph(d, question, font(64, True), 330, 300, W - 480, max_lines=6)
    return _save(img, path)


def fact_card(path: Path, fact: str, source: str, date: str | None, claim_id: str, demo: bool = False,
              kicker: str = "Dữ kiện đã xác minh") -> Path:
    img, d = _base(demo, kicker)
    d.rounded_rectangle([90, 200, W - 90, 760], 24, fill=(26, 44, 70), outline=ACCENT2, width=4)
    _paragraph(d, fact, font(62, True), 140, 250, W - 300, max_lines=6)
    src = f"Nguồn: {source}" + (f" — {date}" if date else "") + f"  [{claim_id}]"
    _paragraph(d, src, font(34), 140, 800, W - 300, fill=MUTED, max_lines=2)
    return _save(img, path)


def formula_card(path: Path, title: str, steps: list[str], note: str, demo: bool = False) -> Path:
    img, d = _base(demo, "Phép tính ví dụ — giả định công khai")
    _paragraph(d, title, font(56, True), 90, 150, W - 200, max_lines=2)
    y = 330
    for i, s in enumerate(steps, 1):
        d.ellipse([90, y, 150, y + 60], fill=ACCENT2)
        d.text((109, y + 6), str(i), font=font(40, True), fill=FG)
        y = _paragraph(d, s, font(44), 190, y + 4, W - 320, max_lines=2) + 30
    _footer(d, note)
    return _save(img, path)


def timeline_card(path: Path, items: list[tuple[str, str]], demo: bool = False) -> Path:
    img, d = _base(demo, "Đường thời gian")
    n = max(1, len(items))
    y0 = 560
    d.line([(160, y0), (W - 160, y0)], fill=ACCENT2, width=8)
    for i, (when, what) in enumerate(items):
        x = 160 + int((W - 320) * (i / max(1, n - 1))) if n > 1 else W // 2
        d.ellipse([x - 26, y0 - 26, x + 26, y0 + 26], fill=ACCENT)
        f = font(40, True)
        tw = d.textlength(when, font=f)
        d.text((x - tw / 2, y0 - 110), when, font=f, fill=WARN)
        lines = wrap(d, what, font(34), 420)[:4]
        yy = y0 + 60
        for ln in lines:
            lw = d.textlength(ln, font=font(34))
            d.text((x - lw / 2, yy), ln, font=font(34), fill=FG)
            yy += 44
    return _save(img, path)


def pros_cons_card(path: Path, pros: list[str], cons: list[str], demo: bool = False) -> Path:
    img, d = _base(demo, "Lợi ích / Rủi ro")
    for col, (items, color, head) in enumerate(((pros, ACCENT, "LỢI ÍCH"), (cons, RED, "RỦI RO"))):
        x = 90 + col * (W // 2 - 40)
        d.rounded_rectangle([x, 170, x + W // 2 - 140, 960], 24, fill=(26, 44, 70), outline=color, width=4)
        d.text((x + 40, 200), head, font=font(54, True), fill=color)
        y = 300
        for it in items[:4]:
            d.text((x + 40, y), "•", font=font(46, True), fill=color)
            y = _paragraph(d, it, font(40), x + 90, y + 4, W // 2 - 300, max_lines=3) + 24
    return _save(img, path)


def narration_card(path: Path, text: str, demo: bool = False, kicker: str = "") -> Path:
    img, d = _base(demo, kicker or None)
    f = font(60, True)
    lines = wrap(d, text, f, W - 360)[:6]
    y = (H - 160 - len(lines) * int(f.size * 1.25)) // 2
    d.text((90, y - 150), "“", font=font(200, True), fill=ACCENT2)
    for ln in lines:
        d.text((180, y), ln, font=f, fill=FG)
        y += int(f.size * 1.25)
    d.line([(180, y + 30), (480, y + 30)], fill=ACCENT, width=6)
    return _save(img, path)


def photo_card(path: Path, photo: Path, caption: str, credit: str | None, illustration: bool, demo: bool = False,
               ai_generated: bool = False) -> Path:
    src = Image.open(photo).convert("RGB")
    bg = ImageOps.fit(src, (W, H)).filter(ImageFilter.GaussianBlur(30))
    fg = ImageOps.contain(src, (W - 200, H - 260))
    img = bg.copy()
    img.paste(fg, ((W - fg.width) // 2, 80))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 170, W, H], fill=(0, 0, 0))
    _paragraph(d, caption, font(44, True), 90, H - 150, W - 200, max_lines=1)
    label = "Ảnh minh họa tạo bằng AI" if ai_generated else ("Ảnh minh họa" if illustration else None)
    if label and credit and credit.startswith(label):
        label = None  # tránh lặp chữ khi phần ghi công đã nêu
    tag = " · ".join(x for x in [label, credit] if x)
    if tag:
        d.text((90, H - 80), tag, font=font(28), fill=MUTED)
    if ai_generated:  # nhãn luôn thấy rõ trên khung hình: ảnh AI không phải ảnh thật
        f = font(28, True)
        txt = "MINH HỌA AI"
        tw = d.textlength(txt, font=f)
        d.rounded_rectangle([50, 40, 50 + tw + 40, 92], 10, fill=WARN)
        d.text((70, 48), txt, font=f, fill=(20, 20, 20))
    if demo:
        d.rounded_rectangle([W - 470, 40, W - 50, 92], 10, fill=RED)
        d.text((W - 450, 48), "DEMO — DỮ LIỆU GIẢ LẬP", font=font(28, True), fill=FG)
    return _save(img, path)


def credits_card(path: Path, sources: list[str], asset_credits: list[str], demo: bool = False) -> Path:
    img, d = _base(demo, "Nguồn & ghi công")
    y = 160
    for s in sources[:8]:
        y = _paragraph(d, "• " + s, font(32), 90, y, W - 200, fill=FG, max_lines=2) + 8
    y += 20
    for s in asset_credits[:6]:
        y = _paragraph(d, "• " + s, font(28), 90, y, W - 200, fill=MUTED, max_lines=1) + 4
    _footer(d, "Kênh độc lập, không phải kênh chính thức của bất kỳ hãng xe nào. Giọng đọc tạo bằng AI.")
    return _save(img, path)


def _save(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=False, compress_level=1)
    return path


def glyph_check(text: str = "ạảãấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđĐ") -> bool:
    """Kiểm tra font có đủ glyph tiếng Việt (tránh lỗi ô vuông)."""
    f = font(40)
    notdef = f.getmask("￿").getbbox()
    for ch in text:
        if f.getmask(ch).getbbox() in (None, notdef) and ch.strip():
            return False
    return True
