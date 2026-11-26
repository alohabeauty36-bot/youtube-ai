"""AssetFinder: thư viện chủ kênh, Pixabay API, Pexels API (key cũ), Wikimedia Commons. KHÔNG tải ảnh từ Google/web search."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from ...errors import RetryableError

log = logging.getLogger(__name__)
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov", ".webm", ".mkv"}


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
    media_type: str = "image"  # image | video
    duration: float | None = None
    cache_key: str | None = None  # tên file trong kho đệm dùng chung giữa các video (vd. pexels_video_12345)


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
            if f.suffix.lower() not in IMG_EXT | VID_EXT or "music" in f.parts:
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
                is_demo=meta.get("is_demo", False), media_type="video" if f.suffix.lower() in VID_EXT else "image",
                duration=meta.get("duration"),
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
                cache_key=f"pexels_photo_{p.get('id')}",
            ))
        return out


class PexelsVideoFinder:
    """Video B-roll từ Pexels (Pexels License: dùng thương mại, không bắt buộc ghi công). Chọn bản HD ngang 1280–1920px."""
    name = "pexels_video"

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.key = api_key
        self.client = client or httpx.Client(timeout=20)

    def search(self, query: str, limit: int = 4) -> list[FoundAsset]:
        try:
            r = self.client.get("https://api.pexels.com/videos/search", headers={"Authorization": self.key},
                                params={"query": query, "orientation": "landscape", "size": "medium", "per_page": limit})
        except httpx.HTTPError as e:
            raise RetryableError(f"Pexels lỗi mạng: {type(e).__name__}") from e
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"Pexels {r.status_code}")
        if r.status_code != 200:
            log.warning("Pexels video trả %s", r.status_code)
            return []
        out = []
        for v in r.json().get("videos", []):
            f = pick_video_file(v.get("video_files") or [])
            if f is None or not (3 <= float(v.get("duration") or 0) <= 60):
                continue
            user = (v.get("user") or {}).get("name")
            out.append(FoundAsset(
                provider="pexels", source_url=v.get("url"), creator=user, license_name="Pexels License",
                license_url="https://www.pexels.com/license/", allowed_commercial=True, attribution_required=False,
                attribution_text=f"Video: {user} / Pexels",
                permission_proof=json.dumps({"api": "pexels/videos/search", "video_id": v.get("id"), "url": v.get("url")}),
                scene_description=query, download_url=f.get("link"), media_type="video",
                duration=float(v.get("duration") or 0), cache_key=f"pexels_video_{v.get('id')}",
            ))
        return out


PIXABAY_LICENSE = ("Pixabay Content License", "https://pixabay.com/service/license-summary/")
PIXABAY_CACHE_SECONDS = 24 * 3600  # điều khoản API Pixabay: kết quả tìm kiếm phải được lưu đệm 24 giờ


class PixabayClient:
    """Gọi API Pixabay (key miễn phí, cấp ngay khi đăng ký). Lưu đệm mỗi truy vấn 24 giờ theo điều khoản, tôn trọng
    giới hạn 100 lượt/60 giây (429 → RetryableError, công việc dừng tìm ở nguồn này và dùng nguồn khác)."""

    def __init__(self, api_key: str, cache_dir: Path | None = None, client: httpx.Client | None = None):
        self.key = api_key
        self.cache_dir = cache_dir
        self.client = client or httpx.Client(timeout=20)

    def get(self, endpoint: str, params: dict) -> dict:
        ident = json.dumps([endpoint, sorted(params.items())], ensure_ascii=False)  # không gồm key → không lộ key ra đĩa
        path = None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self.cache_dir / (hashlib.sha256(ident.encode()).hexdigest()[:32] + ".json")
            if path.exists() and time.time() - path.stat().st_mtime < PIXABAY_CACHE_SECONDS:
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    pass
        try:
            r = self.client.get(f"https://pixabay.com/api/{endpoint}", params={"key": self.key, **params})
        except httpx.HTTPError as e:
            raise RetryableError(f"Pixabay lỗi mạng: {type(e).__name__}") from e
        if r.status_code == 429:
            raise RetryableError("Pixabay hết lượt (100 lượt/phút), thử lại sau")
        if r.status_code >= 500:
            raise RetryableError(f"Pixabay {r.status_code}")
        if r.status_code != 200:
            log.warning("Pixabay trả %s: %s", r.status_code, r.text[:120])
            return {"hits": []}
        data = r.json()
        if path is not None:
            tmp = path.with_suffix(".part")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        return data


class PixabayVideoFinder:
    """Video B-roll từ Pixabay (Pixabay Content License: dùng thương mại, không bắt buộc ghi công — app vẫn ghi nguồn).
    Chỉ lấy bản ngang ~1920×1080 (16:9), 3–60 giây."""
    name = "pixabay_video"

    def __init__(self, api: PixabayClient):
        self.api = api

    def search(self, query: str, limit: int = 4) -> list[FoundAsset]:
        data = self.api.get("videos/", {"q": query[:100], "lang": "en", "video_type": "film", "safesearch": "true",
                                        "min_width": 1280, "per_page": max(3, min(limit * 2, 20))})
        out = []
        for v in data.get("hits", []):
            f = pick_pixabay_rendition(v.get("videos") or {})
            if f is None or not (3 <= float(v.get("duration") or 0) <= 60):
                continue
            out.append(FoundAsset(
                provider="pixabay", source_url=v.get("pageURL"), creator=v.get("user"), license_name=PIXABAY_LICENSE[0],
                license_url=PIXABAY_LICENSE[1], allowed_commercial=True, attribution_required=False,
                attribution_text=f"Video: {v.get('user')} / Pixabay",
                permission_proof=json.dumps({"api": "pixabay/api/videos", "video_id": v.get("id"), "url": v.get("pageURL")}),
                scene_description=v.get("tags") or query, download_url=f["url"], media_type="video",
                duration=float(v.get("duration") or 0), cache_key=f"pixabay_video_{v.get('id')}",
                extra={"width": f.get("width"), "height": f.get("height")},
            ))
            if len(out) >= limit:
                break
        return out


class PixabayImageFinder:
    """Ảnh Pixabay (ảnh chụp, khổ ngang, tối đa 1280px qua API thường). Chỉ lấp từ khoá chưa có video/ảnh — video luôn
    được ưu tiên vì ảnh 1280px phóng lên 1080p kém nét hơn."""
    name = "pixabay_photo"
    fill_only = True

    def __init__(self, api: PixabayClient):
        self.api = api

    def search(self, query: str, limit: int = 3) -> list[FoundAsset]:
        data = self.api.get("", {"q": query[:100], "lang": "en", "image_type": "photo", "orientation": "horizontal",
                                 "safesearch": "true", "min_width": 1280, "per_page": max(3, min(limit * 3, 20))})
        out = []
        for h in data.get("hits", []):
            w, hgt = h.get("imageWidth") or 0, h.get("imageHeight") or 0
            url = h.get("largeImageURL")
            if not url or not hgt or w / hgt < 1.3:  # 16:9: bỏ ảnh dọc/vuông
                continue
            out.append(FoundAsset(
                provider="pixabay", source_url=h.get("pageURL"), creator=h.get("user"), license_name=PIXABAY_LICENSE[0],
                license_url=PIXABAY_LICENSE[1], allowed_commercial=True, attribution_required=False,
                attribution_text=f"Ảnh: {h.get('user')} / Pixabay",
                permission_proof=json.dumps({"api": "pixabay/api", "image_id": h.get("id"), "url": h.get("pageURL")}),
                scene_description=h.get("tags") or query, download_url=url, cache_key=f"pixabay_photo_{h.get('id')}",
            ))
            if len(out) >= limit:
                break
        return out


def pick_pixabay_rendition(videos: dict) -> dict | None:
    """Ưu tiên bản ~1920 (medium), rồi 1280 (small), cuối cùng 4K (large, nặng). Luôn phải là khung ngang."""
    for k in ("medium", "small", "large"):
        f = videos.get(k) or {}
        w, h = f.get("width") or 0, f.get("height") or 0
        if f.get("url") and w >= 1280 and h and w / h >= 1.3:
            return f
    return None


def pick_video_file(files: list[dict]) -> dict | None:
    """Ưu tiên mp4 ngang, rộng 1280–1920 (đủ nét cho 1080p, không quá nặng)."""
    ok = [f for f in files if (f.get("file_type") or "").endswith("mp4") and (f.get("width") or 0) >= (f.get("height") or 1)
          and 1280 <= (f.get("width") or 0) <= 2560]
    if not ok:  # dự phòng: vẫn phải là khung ngang (video dọc cắt sang 16:9 sẽ vỡ hình)
        ok = [f for f in files if (f.get("file_type") or "").endswith("mp4") and (f.get("width") or 0) >= 960
              and (f.get("width") or 0) > (f.get("height") or 0)]
    return min(ok, key=lambda f: abs((f.get("width") or 0) - 1920)) if ok else None


class WikimediaFinder:
    name = "wikimedia"
    API = "https://commons.wikimedia.org/w/api.php"

    def __init__(self, user_agent: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": user_agent})

    def search(self, query: str, limit: int = 5) -> list[FoundAsset]:
        params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrsearch": query,
                  "gsrlimit": limit * 3, "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 1920}
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
            w, h = int(ii.get("width") or 0), int(ii.get("height") or 0)
            if not (w and h and w / h >= 1.3) or (ii.get("mime") or "").startswith("image/svg"):
                continue  # video 16:9: chỉ lấy ảnh ngang (bỏ ảnh dọc/vuông/sơ đồ SVG)
            if len(out) >= limit:
                break
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
            ))
        return out


def _strip(s: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", s).strip()


def download(asset: FoundAsset, dest_dir: Path, client: httpx.Client | None = None, cache_dir: Path | None = None) -> Path:
    """Chỉ gọi SAU KHI gate cho phép. Ảnh/clip own_library được copy. Tư liệu kho ảnh có `cache_key` được giữ trong
    kho đệm dùng chung → video sau dùng lại, không tải lại."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if asset.local_path:
        src = Path(asset.local_path)
        dst = dest_dir / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        return dst
    if not asset.download_url:
        raise RetryableError("Asset không có download_url")
    ext_guess = ".mp4" if asset.media_type == "video" else ".jpg"
    if cache_dir is not None and asset.cache_key:
        cache_dir.mkdir(parents=True, exist_ok=True)
        hit = next(iter(sorted(cache_dir.glob(asset.cache_key + ".*"))), None)
        if hit is not None and hit.stat().st_size > 0:
            return hit
    c = client or httpx.Client(timeout=120, follow_redirects=True)
    try:
        r = c.get(asset.download_url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise RetryableError(f"Tải asset lỗi: {type(e).__name__}") from e
    ctype = r.headers.get("content-type", "")
    ext = ".mp4" if "video" in ctype or asset.media_type == "video" else ".png" if "png" in ctype else ".jpg"
    name = asset.cache_key or f"{asset.provider}_{abs(hash(asset.download_url)) % 10**10}"
    folder = cache_dir if (cache_dir is not None and asset.cache_key) else dest_dir
    dst = folder / f"{name}{ext or ext_guess}"
    tmp = dst.with_suffix(dst.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dst)
    return dst


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
