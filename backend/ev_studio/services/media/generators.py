"""Tạo ảnh minh họa bằng AI — CHỈ là phương án dự phòng khi không tìm được ảnh hợp quyền trên mạng cho một đoạn.

- `GeminiImageGenerator`: Gemini API (dòng "Nano Banana", `generateContent` với responseModalities=IMAGE, 16:9).
- `DemoImageGenerator`: vẽ ảnh giả lập gắn nhãn DEMO, không gọi mạng.

Quy tắc an toàn (đặc tả mục "Chặn sai mẫu xe"): không logo/thương hiệu, không biển số, không người thật/người nổi tiếng,
không chữ, không dựng tai nạn/sự kiện như đã xảy ra. Ảnh AI luôn mang `is_ai_generated=True` và nhãn "minh họa".
Ảnh Gemini có watermark SynthID.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Protocol

import httpx
from PIL import Image, ImageDraw, ImageOps

from ...errors import BlockedByCredentials, PermanentError, RetryableError
from ...security import redact
from .finders import FoundAsset

log = logging.getLogger(__name__)

SAFETY_SUFFIX = (
    "Photorealistic editorial b-roll photo, 16:9 landscape, natural light, documentary style. "
    "Generic modern electric vehicle with no brand logo, no badge, no emblem; do not imitate any specific real car model. "
    "No readable text, no captions, no watermarks, no license plates (leave plate areas blank). "
    "No real or famous people, no identifiable faces (people, if any, are distant or from behind). "
    "No crashes, fires, injuries or disasters."
)
# Không tạo ảnh AI cho đoạn nói về sự cố/sự kiện có thật → dùng đồ họa nội bộ thay vì "dựng lại" sự việc.
SENSITIVE_RE = re.compile(r"\b(tai nạn|va chạm|cháy|phát nổ|nổ|thương vong|tử vong|sự cố|thu hồi|khởi kiện|kiện tụng|"
                          r"khởi tố|accident|crash|fire|explosion|recall|lawsuit)\b", re.I)
AI_LICENSE_NAME = "AI-generated"


def is_sensitive(text: str) -> bool:
    return bool(SENSITIVE_RE.search(text or ""))


def build_image_prompt(brief: str) -> str:
    brief = re.sub(r"\s+", " ", brief or "").strip()[:600]
    return f"{brief}. {SAFETY_SUFFIX}"


@dataclass
class GeneratedImage:
    path: Path
    model: str
    prompt: str
    is_demo: bool
    usage_usd: float = 0.0


class ImageGenerator(Protocol):
    name: str
    is_demo: bool

    def generate(self, brief: str, dest: Path) -> GeneratedImage: ...


def as_found_asset(img: GeneratedImage, provider: str, scene_description: str) -> FoundAsset:
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    label = "Ảnh minh họa tạo bằng AI" + (" (DEMO)" if img.is_demo else f" ({img.model})")
    return FoundAsset(
        provider=provider, source_url=None, creator=img.model, license_name=AI_LICENSE_NAME, license_url=None,
        allowed_commercial=True, attribution_required=False, attribution_text=label,
        permission_proof=json.dumps({"generator": provider, "model": img.model, "prompt": img.prompt, "generated_at": at,
                                     "watermark": "SynthID" if provider == "gemini" else None}, ensure_ascii=False),
        scene_description=scene_description, local_path=str(img.path), is_ai_generated=True, is_illustration=True,
        is_demo=img.is_demo,
    )


class GeminiImageGenerator:
    name = "gemini"
    is_demo = False
    API = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, model: str, price_per_image: float = 0.0, client: httpx.Client | None = None):
        if not api_key:
            raise BlockedByCredentials("Thiếu GEMINI_API_KEY")
        self.key, self.model, self.price = api_key, model, price_per_image
        self.client = client or httpx.Client(timeout=120)

    def generate(self, brief: str, dest: Path) -> GeneratedImage:
        prompt = build_image_prompt(brief)
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}}}
        try:
            r = self.client.post(f"{self.API}/models/{self.model}:generateContent", json=body,
                                 headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise RetryableError(f"Gemini lỗi mạng: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise BlockedByCredentials("GEMINI_API_KEY không hợp lệ hoặc chưa bật Gemini API")
        if r.status_code == 404:
            raise PermanentError(f"Model ảnh Gemini '{self.model}' không tồn tại — bấm 'Kiểm tra' để chọn model")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"Gemini {r.status_code}")
        if r.status_code != 200:
            raise PermanentError(f"Gemini trả {r.status_code}: {redact(r.text[:300])}")
        data = r.json()
        raw = _first_image(data)
        if raw is None:
            reason = (data.get("promptFeedback") or {}).get("blockReason") or \
                ((data.get("candidates") or [{}])[0].get("finishReason")) or "không có ảnh"
            raise PermanentError(f"Gemini không trả ảnh ({reason})")
        _save_16x9(raw, dest)
        return GeneratedImage(dest, self.model, prompt, False, self.price)


def _first_image(data: dict) -> bytes | None:
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and str(inline.get("mimeType") or inline.get("mime_type") or "").startswith("image/") and inline.get("data"):
                return base64.b64decode(inline["data"])
    return None


def _save_16x9(raw: bytes, dest: Path) -> None:
    try:
        im = Image.open(BytesIO(raw)).convert("RGB")
    except Exception as e:
        raise PermanentError("Ảnh Gemini trả về không đọc được") from e
    if abs(im.width / im.height - 16 / 9) > 0.02:
        im = ImageOps.fit(im, (1920, 1080))
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.png")
    im.save(tmp, "PNG")
    tmp.replace(dest)


class DemoImageGenerator:
    """Ảnh giả lập (không gọi mạng) để kiểm thử nhánh dự phòng. Luôn gắn nhãn DEMO + AI."""
    name = "demo_ai"
    is_demo = True
    model = "demo-image-v1"

    def generate(self, brief: str, dest: Path) -> GeneratedImage:
        prompt = build_image_prompt(brief)
        h = hashlib.sha256(brief.encode()).digest()
        base = (20 + h[0] % 40, 30 + h[1] % 50, 50 + h[2] % 60)
        img = Image.new("RGB", (1920, 1080), base)
        d = ImageDraw.Draw(img)
        for y in range(0, 1080):  # bầu trời gradient
            k = y / 1080
            d.line([(0, y), (1920, y)], fill=tuple(min(255, int(c + 90 * (1 - k))) for c in base))
        d.rectangle([0, 760, 1920, 1080], fill=(48, 52, 60))
        x0 = 300 + h[3] % 700
        d.rounded_rectangle([x0, 560, x0 + 760, 800], 90, fill=(210, 218, 230))  # xe chung chung, không thương hiệu
        d.ellipse([x0 + 90, 740, x0 + 250, 900], fill=(20, 20, 20))
        d.ellipse([x0 + 510, 740, x0 + 670, 900], fill=(20, 20, 20))
        from .graphics import font

        d.rounded_rectangle([60, 50, 1100, 130], 14, fill=(180, 40, 40))
        d.text((90, 66), "DEMO — ảnh AI giả lập (không gọi Gemini)", font=font(40, True), fill=(255, 255, 255))
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "PNG")
        return GeneratedImage(dest, self.model, prompt, True, 0.0)
