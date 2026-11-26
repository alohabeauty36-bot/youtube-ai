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


def photo_card(path: Path, photo: Path, caption: str, credit: str | None, illustration: bool, demo: bool = False) -> Path:
    src = Image.open(photo).convert("RGB")
    bg = ImageOps.fit(src, (W, H)).filter(ImageFilter.GaussianBlur(30))
    fg = ImageOps.contain(src, (W - 200, H - 260))
    img = bg.copy()
    img.paste(fg, ((W - fg.width) // 2, 80))
    d = ImageDraw.Draw(img)
    d.rectangle([0, H - 170, W, H], fill=(0, 0, 0))
    _paragraph(d, caption, font(44, True), 90, H - 150, W - 200, max_lines=1)
    tag = " · ".join(x for x in [("Ảnh minh họa" if illustration else None), credit] if x)
    if tag:
        d.text((90, H - 80), tag, font=font(28), fill=MUTED)
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


# ------------------------------------------------------------------ lớp phủ trong suốt (RGBA) đặt lên video/ảnh nền
# Phụ đề nằm dưới (y ≈ 760–900) → chữ nhấn đặt góc trên trái, thẻ dữ kiện/số liệu ở giữa, logo kênh góc trên phải.

PALETTES = [((10, 36, 64), (18, 92, 120)), ((24, 20, 52), (88, 40, 120)), ((8, 44, 40), (20, 110, 90)),
            ((40, 22, 16), (140, 70, 30)), ((14, 26, 48), (40, 70, 140))]


def _rgba() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img, "RGBA")


def _shadow_text(d, xy, text, f, fill=FG, shadow=(0, 0, 0, 170), off=3):
    d.text((xy[0] + off, xy[1] + off), text, font=f, fill=shadow)
    d.text(xy, text, font=f, fill=fill)


def _brand(d, brand: str, demo: bool):
    if brand:
        f = font(30, True)
        tw = d.textlength(brand.upper(), font=f)
        d.rounded_rectangle([W - tw - 96, 44, W - 48, 98], 12, fill=(0, 0, 0, 120))
        d.rectangle([W - tw - 96, 44, W - tw - 88, 98], fill=ACCENT + (255,))
        d.text((W - tw - 70, 52), brand.upper(), font=f, fill=FG)
    if demo:
        f = font(26, True)
        d.rounded_rectangle([48, H - 64, 470, H - 20], 10, fill=RED + (230,))
        d.text((66, H - 58), "DEMO — DỮ LIỆU GIẢ LẬP", font=f, fill=FG)


def _bottom_scrim(img: Image.Image, strength: int = 150):
    """Dải tối dần ở đáy để phụ đề luôn đọc rõ trên mọi nền."""
    scrim = Image.new("RGBA", (W, 420), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    for y in range(420):
        sd.line([(0, y), (W, y)], fill=(0, 0, 0, int(strength * (y / 420) ** 1.6)))
    img.alpha_composite(scrim, (0, H - 420))


def overlay_clean(path: Path, brand: str, demo: bool = False) -> Path:
    img, d = _rgba()
    _bottom_scrim(img)
    _brand(d, brand, demo)
    return _save_rgba(img, path)


def overlay_keypoint(path: Path, text: str, kicker: str, brand: str, demo: bool = False) -> Path:
    img, d = _rgba()
    _bottom_scrim(img)
    f = font(64, True)
    lines = wrap(d, text, f, 1080)[:2]
    h = 58 + len(lines) * 80 + 30
    d.rounded_rectangle([60, 120, 60 + 1180, 120 + h], 16, fill=(8, 14, 24, 205))
    d.rectangle([60, 120, 72, 120 + h], fill=ACCENT + (255,))
    if kicker:
        d.text((100, 136), kicker.upper(), font=font(30, True), fill=ACCENT)
    y = 186
    for ln in lines:
        _shadow_text(d, (100, y), ln, f)
        y += 80
    _brand(d, brand, demo)
    return _save_rgba(img, path)


def overlay_question(path: Path, question: str, brand: str, demo: bool = False) -> Path:
    img, d = _rgba()
    img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 110)))
    _bottom_scrim(img)
    d = ImageDraw.Draw(img, "RGBA")
    _shadow_text(d, (110, 150), "?", font(220, True), fill=WARN)
    f = font(72, True)
    y = 200
    for ln in wrap(d, question, f, W - 520)[:4]:
        _shadow_text(d, (330, y), ln, f)
        y += 92
    _brand(d, brand, demo)
    return _save_rgba(img, path)


def overlay_fact(path: Path, fact: str, source: str, claim_id: str, brand: str, demo: bool = False) -> Path:
    img, d = _rgba()
    img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 90)))
    _bottom_scrim(img)
    d = ImageDraw.Draw(img, "RGBA")
    f = font(58, True)
    lines = wrap(d, fact, f, W - 420)[:5]
    top, h = 150, 110 + len(lines) * 74 + 90
    d.rounded_rectangle([150, top, W - 150, top + h], 26, fill=(10, 22, 38, 225), outline=ACCENT2 + (255,), width=4)
    d.text((200, top + 34), "DỮ KIỆN ĐÃ XÁC MINH", font=font(30, True), fill=ACCENT2)
    y = top + 92
    for ln in lines:
        d.text((200, y), ln, font=f, fill=FG)
        y += 74
    d.text((200, y + 18), f"Nguồn: {source}  [{claim_id}]"[:110], font=font(32), fill=MUTED)
    _brand(d, brand, demo)
    return _save_rgba(img, path)


def overlay_stat(path: Path, value: str, unit: str, label: str, source: str, brand: str, demo: bool = False) -> Path:
    img, d = _rgba()
    img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 120)))
    _bottom_scrim(img)
    d = ImageDraw.Draw(img, "RGBA")
    big = font(230, True)
    vw = d.textlength(value, font=big)
    uf = font(80, True)
    uw = d.textlength(unit, font=uf) if unit else 0
    x = (W - vw - (uw + 30 if unit else 0)) / 2
    _shadow_text(d, (x, 150), value, big, fill=ACCENT, off=5)
    if unit:
        _shadow_text(d, (x + vw + 30, 290), unit, uf)
    f = font(50, True)
    y = 450
    for ln in wrap(d, label, f, W - 500)[:3]:
        lw = d.textlength(ln, font=f)
        _shadow_text(d, ((W - lw) / 2, y), ln, f)
        y += 66
    src = f"Nguồn: {source}"[:100]
    sw = d.textlength(src, font=font(32))
    d.text(((W - sw) / 2, y + 16), src, font=font(32), fill=MUTED)
    _brand(d, brand, demo)
    return _save_rgba(img, path)


def overlay_title(path: Path, title: str, subtitle: str, kicker: str, brand: str, demo: bool = False) -> Path:
    img, d = _rgba()
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(W):  # tối dần từ trái sang phải để chữ nổi
        sd.line([(x, 0), (x, H)], fill=(4, 10, 20, int(225 * max(0.0, 1 - x / (W * 0.85)))))
    img.alpha_composite(shade)
    _bottom_scrim(img)
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([90, 150, 104, 210], fill=ACCENT + (255,))
    d.text((130, 156), kicker.upper(), font=font(40, True), fill=ACCENT)
    # mọi chữ nằm trên y ≈ 700 để không đè phụ đề (y ≈ 760–900)
    f = font(80, True)
    lines = wrap(d, title, f, 1250)
    if len(lines) > 4:
        f = font(66, True)
        lines = wrap(d, title, f, 1250)
    lines = lines[:4]
    step = int(f.size * 1.2)
    y = 250
    for ln in lines:
        _shadow_text(d, (90, y), ln, f, off=4)
        y += step
    if subtitle and y < 620:
        for ln in wrap(d, subtitle, font(38), 1150)[: (1 if y > 540 else 2)]:
            d.text((90, y + 20), ln, font=font(38), fill=(220, 228, 238))
            y += 50
    _brand(d, brand, demo)
    return _save_rgba(img, path)


def backdrop(path: Path, idx: int, label: str = "") -> Path:
    """Nền đồ họa tự vẽ (khi không có video/ảnh hợp quyền): dải màu + vệt sáng, đổi tông theo cảnh; có chuyển động
    Ken Burns lúc dựng nên không đứng hình."""
    c0, c1 = PALETTES[idx % len(PALETTES)]
    img = Image.new("RGB", (W, H), c0)
    d = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(c0[i] * (1 - k) + c1[i] * k) for i in range(3)))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    import random

    rng = random.Random(idx)
    for _ in range(7):
        r = rng.randint(180, 520)
        cx, cy = rng.randint(0, W), rng.randint(0, H)
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, rng.randint(10, 26)))
    for k in range(5):  # vệt đường cao tốc chéo
        y0 = rng.randint(200, H)
        gd.line([(0, y0), (W, y0 - 600)], fill=ACCENT2 + (40,), width=rng.randint(2, 6))
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(40))).convert("RGB")
    if label:
        ImageDraw.Draw(img).text((90, H - 260), label.upper(), font=font(28, True), fill=(255, 255, 255))
    return _save(img, path)


def _save_rgba(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", compress_level=1)
    return path


def compose_preview(path: Path, base: Path, overlay: Path | None) -> Path:
    """Ảnh xem trước của một cảnh: khung nền (ảnh/khung video/đồ họa) phủ lớp chữ."""
    try:
        img = ImageOps.fit(Image.open(base).convert("RGB"), (W, H)).convert("RGBA")
    except Exception:  # noqa: BLE001 — nền không mở được dạng ảnh → nền tối
        img = Image.new("RGBA", (W, H), BG + (255,))
    if overlay is not None and Path(overlay).exists():
        img.alpha_composite(Image.open(overlay).convert("RGBA"))
    return _save(img.convert("RGB"), path)
