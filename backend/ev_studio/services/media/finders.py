"""AssetFinder: thư viện chủ kênh, Pexels API, Wikimedia Commons. KHÔNG tải ảnh từ Google/web search."""
from __future__ import annotations

import json
import logging
import shutil
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
