"""Ảnh minh họa tạo bằng AI (FLUX.1 [schnell]) để lấp những cảnh kho tư liệu không có hình phù hợp.

Nguồn:
- Cloudflare Workers AI (`@cf/black-forest-labs/flux-1-schnell`): miễn phí trong hạn mức 10.000 neurons/ngày
  (~150 ảnh/ngày; app tự dừng ở `cloudflare_daily_images`).
- fal.ai (`fal-ai/flux-1/schnell`): trả phí ~0,003 USD/ảnh 16:9 (tính tròn lên megapixel), có quyền dùng thương mại;
  mỗi ảnh được Ban Tài chính giữ chỗ trước (trần tháng/ngày/phòng/video).
`auto`: Cloudflare trước, hết hạn mức ngày / thiếu key → fal.ai.

Nguyên tắc nội dung: chỉ cảnh chung, không người có thể nhận ra, không xe/thương hiệu/biển số thật, không chữ;
ảnh luôn được đánh dấu là AI (is_ai_generated) và dán nhãn "Ảnh minh họa AI" trên màn hình.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ...errors import BlockedByCredentials, PermanentError, RetryableError
from .finders import FoundAsset

log = logging.getLogger(__name__)
BRANDS = r"vinfast|tesla|byd|toyota|honda|hyundai|kia|mazda|ford|mercedes|bmw|audi|porsche|nissan|mitsubishi|suzuki|" \
         r"wuling|chevrolet|lexus|volvo|xiaomi|apple|samsung|v-?green|evn|petrolimex|\bvf\s?\d+|\bev\d+"
STYLE = ("Photorealistic editorial photograph, natural light, wide 16:9 composition, Southeast Asian city context when "
         "relevant. No text, no letters, no logos, no brand names, no license plates, no watermark, no recognizable faces.")
NEGATIVE_FACTS = re.compile(BRANDS, re.I)


def build_prompt(query: str, image_prompt: str | None = None) -> str:
    base = (image_prompt or query or "electric car on a city street").strip()
    base = NEGATIVE_FACTS.sub("", base)
    base = re.sub(r"\d", "", base)
    base = re.sub(r"\s+", " ", base).strip(" ,.")
    return f"{base}. {STYLE}"[:1500]


def _to_1080p(raw: bytes, out: Path) -> Path:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = ImageOps.fit(img, (1920, 1080), method=Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    img.save(tmp, "JPEG", quality=90)
    tmp.replace(out)
    return out


@dataclass
class Generated:
    path: Path
    provider: str
    model: str
    prompt: str
    cached: bool
    usd: float = 0.0


class CloudflareImages:
    name, paid = "cloudflare", False

    def __init__(self, account_id: str, token: str, model: str = "@cf/black-forest-labs/flux-1-schnell",
                 client: httpx.Client | None = None, steps: int = 4):
        if not account_id or not token:
            raise BlockedByCredentials("Thiếu Cloudflare Account ID / API token (Thiết lập → Ảnh AI minh họa)")
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self.token, self.model, self.steps = token, model, steps
        self.client = client or httpx.Client(timeout=90)

    def generate(self, prompt: str, seed: int) -> bytes:
        try:
            r = self.client.post(self.url, headers={"Authorization": f"Bearer {self.token}"},
                                 json={"prompt": prompt, "steps": self.steps, "seed": seed})
        except httpx.HTTPError as e:
            raise RetryableError(f"Cloudflare AI lỗi mạng: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise BlockedByCredentials("Cloudflare từ chối token (cần quyền Workers AI)")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"Cloudflare AI {r.status_code} (có thể đã hết hạn mức miễn phí trong ngày)")
        if r.headers.get("content-type", "").startswith("image/"):
            return r.content
        body = r.json() if r.content else {}
        img = ((body.get("result") or {}).get("image")) if isinstance(body, dict) else None
        if r.status_code != 200 or not img:
            raise PermanentError(f"Cloudflare AI {r.status_code}: {str(body.get('errors') or body)[:200]}")
        return base64.b64decode(img)


class FalImages:
    name, paid = "fal", True

    def __init__(self, key: str, model: str = "fal-ai/flux-1/schnell", image_size: str = "landscape_16_9",
                 client: httpx.Client | None = None, steps: int = 4):
        if not key:
            raise BlockedByCredentials("Thiếu fal.ai API key (Thiết lập → Ảnh AI minh họa)")
        self.url = f"https://fal.run/{model}"
        self.key, self.model, self.size, self.steps = key, model, image_size, steps
        self.client = client or httpx.Client(timeout=120, follow_redirects=True)

    def generate(self, prompt: str, seed: int) -> bytes:
        try:
            r = self.client.post(self.url, headers={"Authorization": f"Key {self.key}"},
                                 json={"prompt": prompt, "image_size": self.size, "num_inference_steps": self.steps,
                                       "seed": seed, "num_images": 1, "enable_safety_checker": True,
                                       "output_format": "jpeg", "sync_mode": True})
        except httpx.HTTPError as e:
            raise RetryableError(f"fal.ai lỗi mạng: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise BlockedByCredentials("fal.ai từ chối API key")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"fal.ai {r.status_code}")
        if r.status_code != 200:
            raise PermanentError(f"fal.ai {r.status_code}: {r.text[:200]}")
        body = r.json()
        if any(body.get("has_nsfw_concepts") or []):
            raise PermanentError("fal.ai: ảnh bị bộ lọc an toàn chặn")
        url = ((body.get("images") or [{}])[0]).get("url") or ""
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        if url.startswith("https://"):
            try:
                img = self.client.get(url)
                img.raise_for_status()
            except httpx.HTTPError as e:
                raise RetryableError(f"Tải ảnh fal.ai lỗi: {type(e).__name__}") from e
            return img.content
        raise PermanentError("fal.ai không trả ảnh")


def generator_for(s, provider: str):
    if provider == "cloudflare":
        return CloudflareImages(s.cloudflare_account_id or "", s.cloudflare_api_token.get_secret_value() if s.cloudflare_api_token else "",
                                s.cloudflare_image_model)
    if provider == "fal":
        return FalImages(s.fal_api_key.get_secret_value() if s.fal_api_key else "", s.fal_model, s.fal_image_size)
    raise BlockedByCredentials("Chưa chọn nguồn ảnh AI")


def providers_in_order(s) -> list[str]:
    mode = s.ai_image_provider
    if mode == "off":
        return []
    has_cf = bool(s.cloudflare_account_id and s.cloudflare_api_token)
    has_fal = bool(s.fal_api_key)
    if mode == "cloudflare":
        return ["cloudflare"] if has_cf else []
    if mode == "fal":
        return ["fal"] if has_fal else []
    return [p for p, ok in (("cloudflare", has_cf), ("fal", has_fal)) if ok]


def daily_key(provider: str) -> str:
    return f"ai_images_count:{provider}:{datetime.now(timezone.utc).date().isoformat()}"  # Cloudflare reset 00:00 UTC


class AIImageMaker:
    """Tạo (hoặc lấy từ kho đệm) một ảnh minh họa cho một từ khoá/cảnh. Giữ hạn mức ngày và ngân sách."""

    def __init__(self, s, cache_dir: Path, db, story_id: str | None = None, generators: dict | None = None):
        self.s, self.cache, self.db, self.story_id = s, cache_dir, db, story_id
        self.order = list(generators) if generators else providers_in_order(s)
        self._gens = generators or {}
        self.made = 0
        self.notes: list[str] = []

    def _gen(self, p):
        if p not in self._gens:
            self._gens[p] = generator_for(self.s, p)
        return self._gens[p]

    def _count(self, p) -> int:
        from ...workers.jobs import get_setting

        return int(get_setting(self.db, daily_key(p), 0) or 0)

    def _bump(self, p) -> None:
        from ...workers.jobs import set_setting

        set_setting(self.db, daily_key(p), self._count(p) + 1)

    def make(self, query: str, image_prompt: str | None, seed: int) -> Generated | None:
        prompt = build_prompt(query, image_prompt)
        for p in self.order:
            model = self.s.cloudflare_image_model if p == "cloudflare" else self.s.fal_model
            key = hashlib.sha1(f"{p}|{model}|{prompt}|{seed}".encode()).hexdigest()[:20]
            out = self.cache / f"ai_{p}_{key}.jpg"
            if out.exists() and out.stat().st_size > 0:
                return Generated(out, p, model, prompt, True)
            if p == "cloudflare" and self._count(p) >= self.s.cloudflare_daily_images:
                self.notes.append("Cloudflare: đã dùng hết hạn mức ảnh miễn phí hôm nay")
                continue
            rid = None
            try:
                if p == "fal":
                    from ...agents import budget

                    self.db.commit()  # SQLite: nhả khoá ghi để Ban Tài chính ghi sổ cái (phiên riêng)
                    rid = budget.reserve_usd("dao_dien", "fal", model, self.s.fal_price_per_image, self.story_id,
                                             note="ảnh minh họa AI")
                raw = self._gen(p).generate(prompt, seed)
                _to_1080p(raw, out)
            except Exception as e:  # noqa: BLE001 — một nguồn lỗi/hết tiền → thử nguồn sau, không làm hỏng video
                if rid is not None:
                    from ...agents import budget

                    budget.release(rid)
                self.notes.append(f"{p}: {e}")
                log.info("Tạo ảnh AI qua %s không được: %s", p, e)
                continue
            usd = 0.0
            if rid is not None:
                from ...agents import budget

                usd = budget.settle_usd(rid, self.s.fal_price_per_image)
            self._bump(p)
            self.db.commit()  # bộ đếm hạn mức ngày lưu ngay (kể cả khi bước sau lỗi)
            self.made += 1
            return Generated(out, p, model, prompt, False, usd)
        return None


def as_found_asset(g: Generated, query: str) -> FoundAsset:
    terms = "https://bfl.ai/legal/terms-of-service" if g.provider == "cloudflare" else "https://fal.ai/legal/terms-of-service"
    via = "Cloudflare Workers AI" if g.provider == "cloudflare" else "fal.ai"
    return FoundAsset(
        provider=f"ai_{g.provider}", source_url=f"ai://{g.provider}/{g.model}/{g.path.stem}", creator=f"FLUX.1 [schnell] qua {via}",
        license_name="AI generated", license_url=terms, allowed_commercial=True, attribution_required=False,
        attribution_text=f"Ảnh minh họa tạo bằng AI (FLUX.1 schnell, {via})",
        permission_proof=json.dumps({"provider": g.provider, "model": g.model, "prompt": g.prompt, "terms": terms}, ensure_ascii=False),
        scene_description=query, local_path=str(g.path), is_ai_generated=True, is_illustration=True, media_type="image",
    )
