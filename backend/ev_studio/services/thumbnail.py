"""Thumbnail 1280×720 (JPG < 2MB), 2 biến thể, chữ lớn 2–5 từ lấy từ nghịch lý thật của tiêu đề."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .media.graphics import ACCENT, BG, FG, RED, WARN, font, wrap

TW, TH = 1280, 720
STOP = {"và", "của", "là", "có", "cho", "với", "một", "những", "các", "khi", "thì", "mà", "được", "này", "đó"}


def thumb_text(title: str, max_words: int = 5) -> str:
    head = re.split(r"[:?!—]", title)[0].strip()
    words = [w for w in head.split() if w.lower() not in STOP] or head.split()
    words = words[:max_words] if len(words) > 1 else title.split()[:max_words]
    if len(words) < 2:
        words = title.split()[:3]
    return " ".join(words).upper()


def make_thumbnail(out: Path, title: str, photo: Path | None, variant: str = "A", demo: bool = False,
                   illustration: bool = False) -> Path:
    if photo and photo.exists():
        img = ImageOps.fit(Image.open(photo).convert("RGB"), (TW, TH))
        img = Image.blend(img, Image.new("RGB", (TW, TH), (0, 0, 0)), 0.35)
    else:
        img = Image.new("RGB", (TW, TH), BG)
        d0 = ImageDraw.Draw(img)
        for i in range(0, TW, 40):  # họa tiết đường sạc
            d0.line([(i, TH), (i + 300, 0)], fill=(20, 40, 60), width=10)
        img = img.filter(ImageFilter.GaussianBlur(2))
    d = ImageDraw.Draw(img)
    accent = ACCENT if variant == "A" else WARN
    d.rectangle([0, 0, 24, TH], fill=accent)
    text = thumb_text(title)
    f = font(104, True)
    lines = wrap(d, text, f, TW - 140)[:3]
    while len(lines) > 2 and f.size > 70:
        f = font(f.size - 8, True)
        lines = wrap(d, text, f, TW - 140)[:3]
    y = TH - 80 - len(lines) * int(f.size * 1.1)
    for ln in lines:
        d.text((64, y), ln, font=f, fill=FG if variant == "A" else accent, stroke_width=6, stroke_fill=(0, 0, 0))
        y += int(f.size * 1.1)
    if illustration:
        d.text((TW - 230, TH - 50), "Ảnh minh họa", font=font(26), fill=FG, stroke_width=2, stroke_fill=(0, 0, 0))
    if demo:
        d.rounded_rectangle([TW - 300, 24, TW - 24, 74], 10, fill=RED)
        d.text((TW - 285, 32), "DEMO", font=font(32, True), fill=FG)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=88)
    return out
