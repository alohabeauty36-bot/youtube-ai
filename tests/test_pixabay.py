"""Pixabay thay Pexels (Pexels dừng cấp key mới): video B-roll 16:9, ảnh ngang, lưu đệm 24 giờ, giới hạn lượt, kiểm tra kết nối."""
import os
import time

import httpx
import pytest
from pydantic import SecretStr

from ev_studio.config import get_settings
from ev_studio.errors import RetryableError
from ev_studio.services.media.finders import (PixabayClient, PixabayImageFinder, PixabayVideoFinder, download,
                                              pick_pixabay_rendition)
from ev_studio.services.media.license_gate import evaluate

VIDEOS = {"total": 3, "totalHits": 3, "hits": [
    {"id": 21, "pageURL": "https://pixabay.com/videos/id-21/", "tags": "electric car, charging", "duration": 12, "user": "Lan",
     "videos": {"large": {"url": "https://cdn.pixabay.com/v/21_large.mp4", "width": 3840, "height": 2160},
                "medium": {"url": "https://cdn.pixabay.com/v/21_medium.mp4", "width": 1920, "height": 1080},
                "small": {"url": "https://cdn.pixabay.com/v/21_small.mp4", "width": 1280, "height": 720},
                "tiny": {"url": "https://cdn.pixabay.com/v/21_tiny.mp4", "width": 960, "height": 540}}},
    {"id": 22, "pageURL": "https://pixabay.com/videos/id-22/", "tags": "road", "duration": 140, "user": "Long",
     "videos": {"medium": {"url": "https://cdn.pixabay.com/v/22.mp4", "width": 1920, "height": 1080}}},
    {"id": 23, "pageURL": "https://pixabay.com/videos/id-23/", "tags": "phone", "duration": 8, "user": "Doc",
     "videos": {"medium": {"url": "https://cdn.pixabay.com/v/23.mp4", "width": 1080, "height": 1920}}},
]}
IMAGES = {"total": 2, "totalHits": 2, "hits": [
    {"id": 31, "pageURL": "https://pixabay.com/photos/id-31/", "tags": "charger", "user": "Mai",
     "largeImageURL": "https://pixabay.com/get/31_1280.jpg", "imageWidth": 6000, "imageHeight": 3375},
    {"id": 32, "pageURL": "https://pixabay.com/photos/id-32/", "tags": "square", "user": "Vuong",
     "largeImageURL": "https://pixabay.com/get/32_1280.jpg", "imageWidth": 4000, "imageHeight": 4000},
]}


def mock(calls, status=200):
    def h(req: httpx.Request):
        calls.append(req)
        if req.url.host == "pixabay.com" and req.url.path.startswith("/api"):
            if status != 200:
                return httpx.Response(status, text="API rate limit exceeded.")
            return httpx.Response(200, json=VIDEOS if req.url.path == "/api/videos/" else IMAGES)
        return httpx.Response(200, content=b"\x00" * 64, headers={"content-type": "video/mp4"})
    return httpx.Client(transport=httpx.MockTransport(h))


def test_video_finder_keeps_only_landscape_hd_short_clips(tmp_path):
    calls = []
    api = PixabayClient("pk", tmp_path / "api", mock(calls))
    found = PixabayVideoFinder(api).search("ev charging station", 2)
    assert [f.download_url for f in found] == ["https://cdn.pixabay.com/v/21_medium.mp4"]  # bỏ clip 140 s và clip dọc
    fa = found[0]
    assert fa.media_type == "video" and fa.provider == "pixabay" and fa.cache_key == "pixabay_video_21"
    assert fa.license_name == "Pixabay Content License" and "Pixabay" in fa.attribution_text
    q = calls[0].url.params
    assert q["key"] == "pk" and q["safesearch"] == "true" and q["video_type"] == "film"
    # qua license gate và tải về kho đệm dùng chung như Pexels
    assert evaluate(fa.__dict__, story_models=[]).status == "approved"
    p = download(fa, tmp_path / "a", client=mock(calls), cache_dir=tmp_path / "cache")
    assert p.name == "pixabay_video_21.mp4"


def test_rendition_prefers_1080_then_720_never_vertical():
    assert pick_pixabay_rendition(VIDEOS["hits"][0]["videos"])["width"] == 1920
    assert pick_pixabay_rendition({"small": {"url": "s", "width": 1280, "height": 720},
                                   "large": {"url": "", "width": 0, "height": 0}})["url"] == "s"
    assert pick_pixabay_rendition({"medium": {"url": "v", "width": 1080, "height": 1920}}) is None


def test_image_finder_landscape_only_and_fill_only(tmp_path):
    calls = []
    f = PixabayImageFinder(PixabayClient("pk", tmp_path / "api", mock(calls)))
    found = f.search("charger", 3)
    assert [x.cache_key for x in found] == ["pixabay_photo_31"]  # ảnh vuông bị bỏ (16:9)
    assert f.fill_only and calls[0].url.params["orientation"] == "horizontal"


def test_results_cached_24h_and_key_not_written_to_disk(tmp_path):
    calls = []
    api = PixabayClient("secret-pixabay-key", tmp_path / "api", mock(calls))
    PixabayVideoFinder(api).search("ev")
    PixabayVideoFinder(api).search("ev")
    assert len(calls) == 1  # lần 2 lấy từ bộ đệm (điều khoản Pixabay: lưu đệm 24 giờ)
    cached = list((tmp_path / "api").glob("*.json"))
    assert len(cached) == 1 and "secret-pixabay-key" not in cached[0].read_text()
    old = time.time() - 25 * 3600
    os.utime(cached[0], (old, old))
    PixabayVideoFinder(api).search("ev")
    assert len(calls) == 2  # quá 24 giờ → gọi lại


def test_rate_limit_raises_retryable(tmp_path):
    api = PixabayClient("pk", None, mock([], status=429))
    with pytest.raises(RetryableError, match="hết lượt"):
        PixabayVideoFinder(api).search("ev")


def test_finder_order_pixabay_before_pexels_when_both_keys(tmp_path):
    from ev_studio.providers import get_asset_finders

    s = get_settings().model_copy(update={"app_mode": "production", "pixabay_api_key": SecretStr("pk"),
                                          "pexels_api_key": SecretStr("old"), "wikimedia_enabled": True})
    names = [f.name for f in get_asset_finders(s)]
    assert names == ["own_library", "pixabay_video", "pexels_video", "pixabay_photo", "pexels", "wikimedia"]
    s2 = s.model_copy(update={"pixabay_api_key": None, "pexels_api_key": None})
    assert [f.name for f in get_asset_finders(s2)] == ["own_library", "wikimedia"]


def test_connection_test_explains_pexels_pause(tmp_path):
    from ev_studio.services.connection_tests import test_media

    base = get_settings().model_copy(update={"pexels_api_key": None})
    r = test_media(base.model_copy(update={"pixabay_api_key": None}))
    assert not r["ok"] and "pixabay.com/api/docs" in r["message"] and "Pexels" in r["message"]
    r = test_media(base.model_copy(update={"pixabay_api_key": SecretStr("pk")}), client=mock([]))
    assert r["ok"] and "3 video" in r["message"]
