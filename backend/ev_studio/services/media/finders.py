"""AssetFinder: thư viện chủ kênh, Openverse (tìm ảnh giấy phép mở trên toàn web), Wikimedia Commons, Pexels, Pixabay.

Chỉ dùng nguồn trả kèm giấy phép máy đọc được. Ảnh từ Google/web search không có giấy phép → chỉ là URL tham chiếu,
KHÔNG tải. Mọi kết quả đi qua license gate trước khi tải."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from ...errors import PermanentError, RetryableError

log = logging.getLogger(__name__)
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class FoundAsset:
    provider: str
    source_url: str | None
    creator: str | None
    license_name: str | None
    license_url: str | None
    allowed_commercial: bool | None
    attribution_required: bool
    attribution_text: str | None
    permission_proof: str | None
    scene_description: str
    local_path: str | None = None
    download_url: str | None = None
    model_name: str | None = None
    year: int | None = None
    is_ai_generated: bool = False
    is_illustration: bool = False
    is_demo: bool = False
    extra: dict = field(default_factory=dict)


class AssetFinder(Protocol):
    name: str

    def search(self, query: str, limit: int = 5) -> list[FoundAsset]: ...


class OwnLibraryFinder:
    """Ảnh/clip chủ kênh tải lên `media_library/`, mỗi file kèm `<tên>.license.json`.
    File thiếu sidecar license vẫn được liệt kê (để gate loại và hiển thị lý do)."""
    name = "own_library"

    def __init__(self, root: Path):
        self.root = root

    def search(self, query: str, limit: int = 20) -> list[FoundAsset]:
        if not self.root.exists():
            return []
        q = {w.lower() for w in query.split() if len(w) > 2}
        out = []
        for f in sorted(self.root.rglob("*")):
            if f.suffix.lower() not in IMG_EXT:
                continue
            side = f.with_name(f.name + ".license.json")
            meta = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
            desc = meta.get("scene_description", f.stem.replace("_", " "))
            tags = {t.lower() for t in meta.get("tags", [])} | set(desc.lower().split())
            if meta and q and not (q & tags) and not meta.get("generic", False):
                continue
            out.append(FoundAsset(
                provider="own_library", source_url=meta.get("source_url"), creator=meta.get("creator"),
                license_name=meta.get("license_name"), license_url=meta.get("license_url"),
                allowed_commercial=meta.get("allowed_commercial"), attribution_required=meta.get("attribution_required", False),
                attribution_text=meta.get("attribution_text"), permission_proof=meta.get("permission_proof"),
                scene_description=desc, local_path=str(f), model_name=meta.get("model_name"), year=meta.get("year"),
                is_ai_generated=meta.get("is_ai_generated", False), is_illustration=meta.get("is_illustration", False),
                is_demo=meta.get("is_demo", False),
                extra={"generic": bool(meta.get("generic", False)), "text": " ".join([desc, *meta.get("tags", [])])},
            ))
            if len(out) >= limit:
                break
        return out


class PexelsFinder:
    name = "pexels"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.key = api_key
        self.client = client or httpx.Client(timeout=20)

    def search(self, query: str, limit: int = 5) -> list[FoundAsset]:
        try:
            r = self.client.get("https://api.pexels.com/v1/search", headers={"Authorization": self.key},
                                params={"query": query, "orientation": "landscape", "size": "large", "per_page": limit})
        except httpx.HTTPError as e:
            raise RetryableError(f"Pexels lỗi mạng: {type(e).__name__}") from e
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"Pexels {r.status_code}")
        if r.status_code != 200:
            log.warning("Pexels trả %s", r.status_code)
            return []
        out = []
        for p in r.json().get("photos", []):
            out.append(FoundAsset(
                provider="pexels", source_url=p.get("url"), creator=p.get("photographer"),
                license_name="Pexels License", license_url="https://www.pexels.com/license/", allowed_commercial=True,
                attribution_required=False, attribution_text=f"Ảnh: {p.get('photographer')} / Pexels",
                permission_proof=json.dumps({"api": "pexels/v1/search", "photo_id": p.get("id"), "url": p.get("url")}),
                scene_description=p.get("alt") or query, download_url=(p.get("src") or {}).get("large2x"),
                extra={"text": p.get("alt") or ""},
            ))
        return out


class WikimediaFinder:
    name = "wikimedia"
    API = "https://commons.wikimedia.org/w/api.php"

    def __init__(self, user_agent: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": user_agent})

    def search(self, query: str, limit: int = 5) -> list[FoundAsset]:
        params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrsearch": query,
                  "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": 1920}
        try:
            r = self.client.get(self.API, params=params)
        except httpx.HTTPError as e:
            raise RetryableError(f"Wikimedia lỗi mạng: {type(e).__name__}") from e
        if r.status_code >= 500 or r.status_code == 429:
            raise RetryableError(f"Wikimedia {r.status_code}")
        if r.status_code != 200:
            return []
        pages = (r.json().get("query") or {}).get("pages", {})
        out = []
        for p in pages.values():
            ii = (p.get("imageinfo") or [{}])[0]
            md = ii.get("extmetadata") or {}
            g = lambda k: (md.get(k) or {}).get("value")  # noqa: E731
            artist = _strip(g("Artist") or "")
            lic = g("LicenseShortName")
            out.append(FoundAsset(
                provider="wikimedia", source_url=ii.get("descriptionurl"), creator=artist or None, license_name=lic,
                license_url=g("LicenseUrl"), allowed_commercial=not (lic or "").upper().count("NC"),
                attribution_required=(g("AttributionRequired") or "true") == "true",
                attribution_text=f"{artist or 'Wikimedia Commons'} — {lic} — {ii.get('descriptionurl')}",
                permission_proof=json.dumps({"api": "commons extmetadata", "license": lic, "usage_terms": g("UsageTerms")}),
                scene_description=_strip(g("ImageDescription") or p.get("title", "")),
                download_url=ii.get("thumburl") or ii.get("url"),
                extra={"text": f"{p.get('title', '')} {_strip(g('ImageDescription') or '')}"},
            ))
        return out


class OpenverseFinder:
    """Openverse (api.openverse.org): công cụ tìm ảnh giấy phép mở trên TOÀN WEB (Flickr, Wikimedia, bảo tàng…).
    Chỉ lấy ảnh cho phép thương mại + chỉnh sửa (license_type=commercial,modification); mỗi kết quả kèm license,
    license_url và chuỗi ghi công do Openverse cung cấp. Gate vẫn kiểm lại từng ảnh."""
    name = "openverse"
    API = "https://api.openverse.org/v1"
    MIN_WIDTH = 800

    def __init__(self, user_agent: str, client: httpx.Client | None = None, client_id: str | None = None,
                 client_secret: str | None = None):
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": user_agent})
        self.client_id, self.client_secret = client_id, client_secret
        self._token: str | None = None

    def _auth(self) -> dict:
        if not (self.client_id and self.client_secret):
            return {}
        if self._token is None:
            try:
                r = self.client.post(f"{self.API}/auth_tokens/token/", data={
                    "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret})
                self._token = r.json().get("access_token", "") if r.status_code == 200 else ""
            except (httpx.HTTPError, ValueError):
                self._token = ""
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def search(self, query: str, limit: int = 5) -> list[FoundAsset]:
        params = {"q": query, "license_type": "commercial,modification", "page_size": max(limit * 2, 10), "mature": "false"}
        try:
            r = self.client.get(f"{self.API}/images/", params=params, headers=self._auth())
        except httpx.HTTPError as e:
            raise RetryableError(f"Openverse lỗi mạng: {type(e).__name__}") from e
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"Openverse {r.status_code}")
        if r.status_code != 200:
            log.warning("Openverse trả %s", r.status_code)
            return []
        out = []
        for p in r.json().get("results", []):
            if (p.get("width") or 0) and p["width"] < self.MIN_WIDTH:
                continue
            lic = openverse_license_name(p.get("license"), p.get("license_version"))
            title = _strip(p.get("title") or "")
            tags = " ".join(t.get("name", "") for t in (p.get("tags") or []) if isinstance(t, dict))
            out.append(FoundAsset(
                provider="openverse", source_url=p.get("foreign_landing_url"), creator=p.get("creator"),
                license_name=lic, license_url=p.get("license_url"), allowed_commercial=True,
                attribution_required=(p.get("license") or "").lower() not in ("cc0", "pdm"),
                attribution_text=_strip(p.get("attribution") or "") or (
                    f"{title or 'Ảnh'} — {p.get('creator') or 'không rõ tác giả'} — {lic} — {p.get('foreign_landing_url')}"),
                permission_proof=json.dumps({"api": "openverse/v1/images", "id": p.get("id"), "license": p.get("license"),
                                             "license_version": p.get("license_version"), "license_url": p.get("license_url"),
                                             "source": p.get("source"), "landing": p.get("foreign_landing_url")}),
                scene_description=title or query, download_url=p.get("url"),
                extra={"text": f"{title} {tags}", "source": p.get("source")},
            ))
            if len(out) >= limit:
                break
        return out


def openverse_license_name(code: str | None, version: str | None) -> str | None:
    c = (code or "").lower()
    v = (version or "").strip()
    if c == "cc0":
        return "CC0 1.0"
    if c == "pdm":
        return "PDM"
    if c.startswith("by"):
        return f"CC {c.upper()} {v}".strip()
    return code  # NC/ND… → gate loại


class PixabayFinder:
    name = "pixabay"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.key = api_key
        self.client = client or httpx.Client(timeout=20)

    def search(self, query: str, limit: int = 5) -> list[FoundAsset]:
        try:
            r = self.client.get("https://pixabay.com/api/", params={
                "key": self.key, "q": query[:100], "image_type": "photo", "orientation": "horizontal", "safesearch": "true",
                "per_page": max(3, min(limit, 200))})
        except httpx.HTTPError as e:
            raise RetryableError(f"Pixabay lỗi mạng: {type(e).__name__}") from e
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"Pixabay {r.status_code}")
        if r.status_code != 200:
            log.warning("Pixabay trả %s", r.status_code)
            return []
        out = []
        for p in r.json().get("hits", [])[:limit]:
            out.append(FoundAsset(
                provider="pixabay", source_url=p.get("pageURL"), creator=p.get("user"),
                license_name="Pixabay Content License", license_url="https://pixabay.com/service/license-summary/",
                allowed_commercial=True, attribution_required=False, attribution_text=f"Ảnh: {p.get('user')} / Pixabay",
                permission_proof=json.dumps({"api": "pixabay/api", "id": p.get("id"), "url": p.get("pageURL")}),
                scene_description=p.get("tags") or query, download_url=p.get("largeImageURL"),
                extra={"text": p.get("tags") or ""},
            ))
        return out


_MODEL_RE = re.compile(r"\b(VF|EC)\s?-?(\d{1,2}|e\d{2})\b", re.I)


def detect_model(text: str | None) -> str | None:
    """Đọc tên mẫu xe VinFast trong tiêu đề/tag ảnh ("VinFast VF8" → "VF 8"). Không đoán khi không có chữ."""
    m = _MODEL_RE.search(text or "")
    if not m:
        return None
    return f"{m.group(1).upper()} {m.group(2).lower()}"


def norm_model(name: str | None) -> str:
    return re.sub(r"[\s\-]", "", (name or "").lower())


def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).strip()


def download(asset: FoundAsset, dest_dir: Path, client: httpx.Client | None = None) -> Path:
    """Chỉ gọi SAU KHI gate cho phép. Ảnh own_library được copy."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if asset.local_path:
        src = Path(asset.local_path)
        dst = dest_dir / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return dst
    if not asset.download_url:
        raise RetryableError("Asset không có download_url")
    c = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        r = c.get(asset.download_url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise RetryableError(f"Tải asset lỗi: {type(e).__name__}") from e
    if len(r.content) > MAX_DOWNLOAD_BYTES:
        raise PermanentError(f"Asset quá lớn ({len(r.content) // 1_000_000} MB)")
    ext = _image_ext(r.content)
    if ext is None:
        raise PermanentError("Nội dung tải về không phải ảnh hợp lệ")
    dst = dest_dir / f"{asset.provider}_{hashlib.sha1(asset.download_url.encode()).hexdigest()[:12]}{ext}"
    tmp = dst.with_suffix(dst.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dst)
    return dst


MAX_DOWNLOAD_BYTES = 40_000_000


def _image_ext(data: bytes) -> str | None:
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as im:
            im.verify()
            fmt = (im.format or "").upper()
    except Exception:
        return None
    return {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(fmt)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
