"""Hình ảnh video: kế hoạch cảnh quay, tìm video Pexels, chọn nền không lặp, dựng nhiều lớp, nhạc nền tự giảm khi có giọng."""
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from ev_studio.services import renderer, shotplan, storyboard
from ev_studio.services.media import graphics
from ev_studio.services.media.finders import PexelsVideoFinder, download, pick_video_file

SCENES = [
    {"idx": 0, "text": "Có những thông báo nghe qua thì toàn tin vui.", "start": 0, "end": 5},
    {"idx": 1, "text": "Trạm sạc nhanh công cộng thường quá tải vào giờ cao điểm.", "start": 5, "end": 10},
    {"idx": 2, "text": "Giá bán và chi phí sạc mỗi tháng quyết định bạn tiết kiệm được bao nhiêu.", "start": 10, "end": 16},
    {"idx": 3, "text": "Theo V-Green, mạng lưới đạt 150.000 cổng sạc.", "start": 16, "end": 21},
    {"idx": 4, "text": "Vậy người ở chung cư sẽ sạc ở đâu?", "start": 21, "end": 25},
]
CLAIMS = [{"claim_key": "c1", "text": "Mạng lưới đạt 150.000 cổng sạc", "value": "150.000", "unit": "cổng"}]
CMAP = [{"claim_id": "c1", "relevant_script_excerpt": "Theo V-Green, mạng lưới đạt 150.000 cổng sạc."}]


def test_heuristic_plan_topics_variety_and_stat_only_from_claims():
    plan = shotplan.plan_shots(SCENES, claims=CLAIMS, claim_map=CMAP, title="t", pillar="P3")
    by = {p["idx"]: p for p in plan}
    assert by[1]["query_en"] == "electric car charging station"
    assert by[2]["query_en"] == "calculator money saving"
    assert by[3]["kind"] == "stat" and by[3]["claim_id"] == "c1"
    assert by[4]["kind"] == "question"
    assert all(p["query_en"].isascii() for p in plan)
    assert len({p["query_en"] for p in plan}) >= 4  # đa dạng cảnh


def test_llm_plan_is_validated_no_invented_numbers():
    raw = {"shots": [
        {"idx": 0, "kind": "broll", "query_en": "sunrise city skyline", "overlay_vi": "Tin vui hay bẫy điều kiện"},
        {"idx": 1, "kind": "keypoint", "query_en": "ev charging station night", "overlay_vi": "Trạm sạc quá tải 300% giờ cao điểm"},
        {"idx": 2, "kind": "stat", "query_en": "trạm sạc", "overlay_vi": "Chi phí"},  # stat không có claim, query không phải tiếng Anh
        {"idx": 3, "kind": "stat", "query_en": "ev charger close up", "overlay_vi": "Mạng lưới cổng sạc", "claim_id": "c1"},
    ]}  # thiếu idx 4
    plan = shotplan.validate_llm_plan(raw, SCENES, {"c1": {**CLAIMS[0], "_excerpt": ""}})
    by = {p["idx"]: p for p in plan}
    assert by[0]["query_en"] == "sunrise city skyline" and by[0]["source"] == "llm"
    assert "300" not in by[1]["overlay_vi"]  # số không có trong lời đọc → thay bằng câu trích từ lời đọc
    assert by[2]["kind"] == "keypoint" and by[2]["query_en"].isascii()
    assert by[3]["kind"] == "stat" and by[3]["claim_id"] == "c1"
    assert by[4]["source"] == "heuristic"


def test_brain_failure_falls_back_to_dictionary():
    class Broken:
        def ask(self, task):
            raise RuntimeError("hết ngân sách")

    plan = shotplan.plan_shots(SCENES, claims=CLAIMS, claim_map=CMAP, title="t", pillar="P1", brain=Broken())
    assert len(plan) == len(SCENES) and all(p["source"] == "heuristic" for p in plan)


def test_pexels_video_search_picks_hd_landscape_and_caches(tmp_path):
    body = {"videos": [
        {"id": 11, "url": "https://www.pexels.com/video/11/", "duration": 14, "user": {"name": "An"},
         "video_files": [{"file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://v/4k.mp4"},
                         {"file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://v/1080.mp4"},
                         {"file_type": "video/mp4", "width": 640, "height": 360, "link": "https://v/360.mp4"}]},
        {"id": 12, "url": "https://www.pexels.com/video/12/", "duration": 200, "user": {"name": "B"},
         "video_files": [{"file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://v/long.mp4"}]},
        {"id": 13, "url": "https://www.pexels.com/video/13/", "duration": 9, "user": {"name": "C"},
         "video_files": [{"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://v/vertical.mp4"}]},
    ]}
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.host == "api.pexels.com":
            assert req.url.path == "/videos/search" and req.headers["Authorization"] == "k"
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=b"\x00" * 64, headers={"content-type": "video/mp4"})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    found = PexelsVideoFinder("k", client=c).search("ev charging station")
    assert [f.download_url for f in found] == ["https://v/1080.mp4"]  # bỏ clip quá dài và clip dọc
    fa = found[0]
    assert fa.media_type == "video" and fa.license_name == "Pexels License" and fa.cache_key == "pexels_video_11"
    p1 = download(fa, tmp_path / "a", client=c, cache_dir=tmp_path / "cache")
    p2 = download(fa, tmp_path / "b", client=c, cache_dir=tmp_path / "cache")
    assert p1 == p2 and p1.suffix == ".mp4" and sum("v/1080" in u for u in calls) == 1  # lần 2 lấy từ kho đệm
    assert pick_video_file([{"file_type": "video/mp4", "width": 1280, "height": 720, "link": "x"}])["link"] == "x"


def test_background_never_repeats_back_to_back():
    assets = [{"id": f"v{i}", "media_type": "video"} for i in range(3)]
    uses, seen, picked = {}, {}, []
    for i in range(30):
        a = storyboard.pick_background(i, "q", assets, {"q": ["v0"]}, uses, seen)
        picked.append(a["id"])
    assert all(picked[i] != picked[i + 1] for i in range(len(picked) - 1))
    assert picked[0] == "v0"  # đúng từ khoá được ưu tiên


def _tone(path: Path, seconds: float, freq: int, vol: float):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate=48000",
                    "-af", f"volume={vol}", "-ac", "2", str(path)], check=True)


def test_layered_render_with_music_ducking(tmp_path, db_ready):
    from ev_studio.services import media_qa

    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "gradients=s=1280x720:speed=0.03:duration=3:rate=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    ov = graphics.overlay_keypoint(tmp_path / "ov.png", "Trạm sạc quá tải giờ cao điểm", "Điểm chính", "Vựa Tin 365")
    bd = graphics.backdrop(tmp_path / "bd.png", 2)
    c1 = renderer.render_layered_clip({"kind": "video", "path": str(src), "offset": 1.0}, ov, 4.0, tmp_path / "c1.mp4", 30)
    c2 = renderer.render_layered_clip({"kind": "graphic", "path": str(bd), "motion": "pan_left", "dim": 0.4}, None, 2.0,
                                      tmp_path / "c2.mp4", 30)
    assert round(renderer.video_duration(c1), 1) == 4.0 and round(renderer.video_duration(c2), 1) == 2.0
    voice, music = tmp_path / "voice.wav", tmp_path / "music.wav"
    _tone(voice, 6, 440, 0.5)
    _tone(music, 2, 220, 0.9)
    out = renderer.compose([c1, c2], voice, None, tmp_path / "final.mp4", tmp_path / "w", music=music, music_db=-20)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
                                     capture_output=True, text=True).stdout)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert round(renderer.video_duration(out)) == 6
    res = media_qa.run_media_qa(out, exact_10min=False, dur_min=0, dur_max=1e9, assets=[], on_screen_facts_ok=True,
                                description="", sources=[], thumbnail=None, subtitle=None, music_present=True,
                                music_db=-20, hard_min_seconds=0)
    checks = {c["name"]: c for c in res["checks"]}
    assert checks["Nhạc không át giọng"]["ok"] and checks["Audio không clipping (max < -0.3 dB)"]["ok"]


def test_music_needs_license_sidecar(tmp_path, monkeypatch):
    from ev_studio.config import get_settings
    from ev_studio.workers import jobs

    root = tmp_path / "media"
    (root / "music").mkdir(parents=True)
    _tone(root / "music" / "no_license.wav", 1, 300, 0.3)
    _tone(root / "music" / "yt_audio_library.wav", 1, 300, 0.3)
    (root / "music" / "yt_audio_library.wav.license.json").write_text(json.dumps({
        "license_name": "YouTube Audio Library", "allowed_commercial": True, "attribution_required": False,
        "source_url": "https://studio.youtube.com/channel/UC/music", "attribution_text": "Bài nhạc A — YouTube Audio Library"}))
    monkeypatch.setattr(jobs, "get_settings", lambda: get_settings().model_copy(update={"media_library_dir": root}))
    m = jobs._pick_music(type("S", (), {"id": "story-1"})())
    assert m and m["path"].endswith("yt_audio_library.wav") and "YouTube Audio Library" in m["credit"]


def test_only_landscape_sources_for_16_9(tmp_path):
    """Video YouTube luôn 16:9: ảnh/video dọc hoặc vuông không được dùng làm nền (cắt sang 16:9 sẽ mất chủ thể)."""
    from PIL import Image

    Image.new("RGB", (1600, 900)).save(tmp_path / "wide.jpg")
    Image.new("RGB", (1000, 1000)).save(tmp_path / "square.jpg")
    vert = tmp_path / "vert.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "gradients=s=720x1280:duration=1:rate=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vert)], check=True)
    assert renderer.is_landscape_enough(tmp_path / "wide.jpg", "image")
    assert not renderer.is_landscape_enough(tmp_path / "square.jpg", "image")
    assert not renderer.is_landscape_enough(vert, "video")


def test_wikimedia_skips_portrait_and_svg():
    from ev_studio.services.media.finders import WikimediaFinder

    def ii(w, h, mime="image/jpeg", n=1):
        return {"title": f"File:{n}.jpg", "imageinfo": [{"width": w, "height": h, "mime": mime, "url": f"https://u/{n}",
                "descriptionurl": f"https://commons/{n}", "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"},
                                                                          "Artist": {"value": "A"}}}]}

    pages = {"1": ii(3000, 4000, n=1), "2": ii(1200, 1200, n=2), "3": ii(2000, 1000, "image/svg+xml", n=3), "4": ii(4000, 2250, n=4)}
    c = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"query": {"pages": pages}})))
    found = WikimediaFinder("ua", client=c).search("ev", limit=5)
    assert [f.source_url for f in found] == ["https://commons/4"]
