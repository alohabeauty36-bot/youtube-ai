"""Hình cho từng đoạn: tìm ảnh hợp quyền trên mạng TRƯỚC, không có mới gọi Gemini, cuối cùng là đồ họa nội bộ."""
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from ev_studio.db import SessionLocal
from ev_studio.errors import BlockedByCredentials, PermanentError, RetryableError
from ev_studio.models import Asset, NewsItem, Scene, Story, TTSSegment
from ev_studio.providers import Providers
from ev_studio.services.media.finders import FoundAsset, OpenverseFinder, PixabayFinder, detect_model
from ev_studio.services.media.generators import (DemoImageGenerator, GeminiImageGenerator, GeneratedImage, SAFETY_SUFFIX,
                                                 is_sensitive)
from ev_studio.services.media.license_gate import evaluate
from ev_studio.services.media.visual_plan import ParagraphVisual, heuristic_plan
from ev_studio.workers.jobs import media_job, run_pipeline, storyboard_job


def _png(w=1344, h=768) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), (10, 80, 160)).save(b, "PNG")
    return b.getvalue()


# ------------------------------------------------------------------ finders


def test_openverse_finder_maps_license_attribution_and_filters_small_images():
    seen = {}

    def h(req: httpx.Request):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={"results": [
            {"id": "a", "title": "VinFast VF8 at charger", "foreign_landing_url": "https://www.flickr.com/photos/x/1",
             "url": "https://live.staticflickr.com/1.jpg", "creator": "Lan", "license": "by", "license_version": "2.0",
             "license_url": "https://creativecommons.org/licenses/by/2.0/", "attribution": "\"VinFast VF8\" by Lan is licensed under CC BY 2.0.",
             "width": 2048, "height": 1365, "source": "flickr", "tags": [{"name": "ev"}]},
            {"id": "b", "title": "tiny", "foreign_landing_url": "https://x/2", "url": "https://x/2.jpg", "license": "cc0",
             "license_version": "1.0", "width": 320, "height": 200},
            {"id": "c", "title": "Charging", "foreign_landing_url": "https://commons.wikimedia.org/wiki/File:c.jpg",
             "url": "https://upload/c.jpg", "license": "cc0", "license_version": "1.0", "width": 1600, "height": 900},
        ]})

    f = OpenverseFinder("ua", client=httpx.Client(transport=httpx.MockTransport(h)))
    out = f.search("electric car charging", limit=5)
    assert seen["license_type"] == "commercial,modification" and seen["mature"] == "false"
    assert [x.license_name for x in out] == ["CC BY 2.0", "CC0 1.0"]  # ảnh nhỏ bị bỏ
    assert out[0].attribution_required and "Lan" in out[0].attribution_text
    assert detect_model(out[0].scene_description) == "VF 8"
    assert all(evaluate(x.__dict__).status == "approved" for x in out)


def test_openverse_nc_result_is_rejected_by_gate_and_errors_are_retryable():
    def h(req):
        return httpx.Response(200, json={"results": [{"id": "n", "title": "x", "foreign_landing_url": "u", "url": "u.jpg",
                                                      "license": "by-nc", "license_version": "4.0", "width": 2000}]})

    r = OpenverseFinder("ua", client=httpx.Client(transport=httpx.MockTransport(h))).search("q")[0]
    assert evaluate(r.__dict__).status == "rejected"
    f = OpenverseFinder("ua", client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))))
    with pytest.raises(RetryableError):
        f.search("q")


def test_pixabay_finder_and_license():
    def h(req):
        assert req.url.params["key"] == "k"
        return httpx.Response(200, json={"hits": [{"id": 9, "pageURL": "https://pixabay.com/photos/ev-9/", "user": "Minh",
                                                   "tags": "electric car, charging", "largeImageURL": "https://pixabay.com/get/9.jpg"}]})

    r = PixabayFinder("k", client=httpx.Client(transport=httpx.MockTransport(h))).search("ev")[0]
    assert r.license_name == "Pixabay Content License" and evaluate(r.__dict__).status == "approved"


# ------------------------------------------------------------------ gate cho ảnh AI


def test_gate_ai_generated_rules():
    ok = {"provider": "gemini", "license_name": "AI-generated", "is_ai_generated": True,
          "permission_proof": json.dumps({"model": "m", "prompt": "p"})}
    d = evaluate(ok, story_models=["VF 8"])
    assert d.status == "approved" and d.is_illustration
    assert evaluate(ok | {"permission_proof": ""}).status == "rejected"
    assert evaluate(ok | {"provider": "openverse"}).status == "rejected"  # nguồn web không được tự nhận là ảnh AI hợp lệ
    assert evaluate(ok | {"is_ai_generated": False}).status == "rejected"
    assert evaluate({"provider": "gemini", "license_name": "CC0", "is_ai_generated": True, "source_url": "u"}).status == "rejected"


# ------------------------------------------------------------------ Gemini


def test_gemini_generator_request_and_16x9_output(tmp_path):
    seen = {}

    def h(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["key"] = req.headers.get("x-goog-api-key")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(_png()).decode()}}]}}]})

    g = GeminiImageGenerator("secret-key", "gemini-3.1-flash-image", 0.04, client=httpx.Client(transport=httpx.MockTransport(h)))
    img = g.generate("electric car at a charging station", tmp_path / "a.png")
    assert seen["url"].endswith("/models/gemini-3.1-flash-image:generateContent") and seen["key"] == "secret-key"
    cfg = seen["body"]["generationConfig"]
    assert cfg["responseModalities"] == ["IMAGE"] and cfg["imageConfig"]["aspectRatio"] == "16:9"
    prompt = seen["body"]["contents"][0]["parts"][0]["text"]
    assert SAFETY_SUFFIX in prompt and "no license plates" in prompt and "no brand logo" in prompt
    w, h_ = Image.open(img.path).size
    assert abs(w / h_ - 16 / 9) < 0.02 and img.usage_usd == 0.04


@pytest.mark.parametrize("resp,exc", [
    (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), PermanentError),
    (httpx.Response(403, json={}), BlockedByCredentials),
    (httpx.Response(429, json={}), RetryableError),
    (httpx.Response(404, json={}), PermanentError),
])
def test_gemini_generator_errors(tmp_path, resp, exc):
    g = GeminiImageGenerator("k", "m", client=httpx.Client(transport=httpx.MockTransport(lambda req: resp)))
    with pytest.raises(exc):
        g.generate("x", tmp_path / "x.png")


def test_gemini_requires_key():
    with pytest.raises(BlockedByCredentials):
        GeminiImageGenerator("", "m")


# ------------------------------------------------------------------ kế hoạch theo đoạn


def test_heuristic_plan_queries_models_and_sensitive_paragraphs():
    paras = [{"paragraph_idx": 0, "text": "Mẫu VF 8 sạc tại trạm sạc nhanh ở Hà Nội mất bao lâu?"},
             {"paragraph_idx": 1, "text": "Vụ cháy xe hôm qua khiến nhiều người lo lắng."},
             {"paragraph_idx": 2, "text": "Điều kiện áp dụng khá rõ ràng với người mua."}]
    plan = heuristic_plan(paras, ["VF 8"], max_queries=3)
    assert plan[0].search_queries[0] == "VinFast VF 8" and "EV charging station" in plan[0].search_queries
    assert plan[0].subject_model == "VF 8" and not plan[0].skip_image and len(plan[0].search_queries) <= 3
    assert plan[1].skip_image and is_sensitive(paras[1]["text"])
    assert not plan[2].skip_image and plan[2].search_queries[-1] == "electric car"


# ------------------------------------------------------------------ thứ tự ưu tiên: web → Gemini → đồ họa


class FakeWebFinder:
    """Như một nguồn web có giấy phép: chỉ trả ảnh cho đúng truy vấn 'EV charging station'."""
    name = "openverse"

    def __init__(self, img_dir: Path):
        self.calls: list[str] = []
        self.dir = img_dir

    def search(self, query, limit=5):
        self.calls.append(query)
        if query != "EV charging station":
            return []
        out = []
        for i in range(3):
            p = self.dir / f"web_{i}.jpg"
            if not p.exists():
                Image.new("RGB", (1600, 900), (40 * i, 90, 120)).save(p, "JPEG")
            out.append(FoundAsset(provider="openverse", source_url=f"https://www.flickr.com/photos/t/{i}", creator="T",
                                  license_name="CC BY 4.0", license_url="https://creativecommons.org/licenses/by/4.0/",
                                  allowed_commercial=True, attribution_required=True, attribution_text=f"Ảnh {i} — T — CC BY 4.0",
                                  permission_proof="{}", scene_description="Charging station", local_path=str(p)))
        return out


class CountingGenerator(DemoImageGenerator):
    def __init__(self):
        self.briefs: list[str] = []

    def generate(self, brief, dest) -> GeneratedImage:
        self.briefs.append(brief)
        return super().generate(brief, dest)


@pytest.fixture()
def tts_story(seeded):
    with SessionLocal() as s:
        n = s.scalar(select(NewsItem).where(NewsItem.title.contains("tổng chi phí")))
        st = Story(news_item_id=n.id, pillar="P1", topic="Ảnh theo đoạn", mode="MANUAL", script_approved_by="tester", is_demo=True)
        s.add(st)
        s.commit()
        sid = st.id
    res = run_pipeline(sid, Providers(), stop_at="TTS_DONE")
    assert res["state"] == "TTS_DONE", res
    return sid


def _paragraph_texts(s, st):
    from ev_studio.workers.jobs import current_script, script_paragraphs

    return script_paragraphs(s, current_script(s, st))[0]


def test_web_first_then_gemini_then_graphics(tts_story, tmp_path, monkeypatch):
    web, gen = FakeWebFinder(tmp_path), CountingGenerator()
    with SessionLocal() as s:
        st = s.get(Story, tts_story)
        paras = _paragraph_texts(s, st)
        n = len(paras)
        assert n >= 3
        # Kế hoạch cố định: đoạn 0 có ảnh web, đoạn 1 không có ảnh web, đoạn 2 là sự cố → không tạo ảnh AI
        plan = [ParagraphVisual(paragraph_idx=p["paragraph_idx"],
                                search_queries=["EV charging station"] if i == 0 else ["nothing here"],
                                image_prompt=f"scene {i}") for i, p in enumerate(paras)]
        monkeypatch.setattr("ev_studio.services.media.visual_plan.plan_visuals",
                            lambda paragraphs, models, writer=None, pillar="": (plan, {}))
        texts = {p["paragraph_idx"]: p["text"] for p in paras}
        texts[paras[2]["paragraph_idx"]] = "Một vụ cháy xe gần đây khiến nhiều người lo lắng."
        monkeypatch.setattr("ev_studio.workers.jobs.script_paragraphs",
                            lambda session, sv: ([{"paragraph_idx": k, "text": v} for k, v in texts.items()], {k: 1 for k in texts}))
        res = media_job(s, st, Providers(finders=[web], image_generator=gen))
        s.commit()
        reps = {r["paragraph_idx"]: r for r in res["paragraph_reports"]}
        p0, p1, p2 = (paras[i]["paragraph_idx"] for i in range(3))
        assert reps[p0]["source"] == "web" and reps[p0]["found"] == 1
        assert reps[p1]["source"] == "ai" and reps[p1]["found"] == 1
        assert reps[p2]["source"] == "graphics" and "sự cố" in reps[p2]["note"]
        # Gemini CHỈ được gọi cho đoạn không có ảnh web (không gọi cho đoạn 0, không gọi cho đoạn nhạy cảm)
        assert "scene 0" not in gen.briefs and "scene 2" not in gen.briefs and "scene 1" in gen.briefs
        assert len(gen.briefs) == n - 2
        a_web = s.scalar(select(Asset).where(Asset.story_id == st.id, Asset.paragraph_idx == p0))
        assert a_web.provider == "openverse" and a_web.local_path and a_web.content_hash and not a_web.is_ai_generated
        a_ai = s.scalar(select(Asset).where(Asset.story_id == st.id, Asset.paragraph_idx == p1))
        assert a_ai.is_ai_generated and a_ai.is_illustration and a_ai.license.license_name == "AI-generated"
        assert json.loads(a_ai.license.permission_proof)["prompt"].startswith("scene 1")

        # chạy lại: không tìm/tạo lại cho đoạn đã có ảnh
        web.calls.clear()
        gen.briefs.clear()
        media_job(s, st, Providers(finders=[web], image_generator=gen))
        assert not gen.briefs and "EV charging station" not in web.calls

        # storyboard đặt ảnh vào cảnh của CHÍNH đoạn đó, ảnh AI có cờ ai_generated
        storyboard_job(s, st, Providers())
        s.commit()
        scenes = list(s.scalars(select(Scene).where(Scene.story_id == st.id, Scene.script_version_id == st.current_script_version_id)))
        para_of = {t.segment_key: t.paragraph_idx for t in s.scalars(select(TTSSegment).where(
            TTSSegment.script_version_id == st.current_script_version_id))}
        for sc in scenes:
            for aid in sc.asset_ids:
                a = s.get(Asset, aid)
                if a.paragraph_idx is not None:
                    assert a.paragraph_idx == para_of[sc.narration_segment_ids[0]]
                    assert sc.payload["ai_generated"] == a.is_ai_generated
        used = {aid for sc in scenes for aid in sc.asset_ids}
        assert a_web.id in used


def test_no_generator_means_internal_graphics(tts_story, tmp_path, monkeypatch):
    class Empty:
        name = "openverse"

        def search(self, q, limit=5):
            return []

    with SessionLocal() as s:
        st = s.get(Story, tts_story)
        res = media_job(s, st, Providers(finders=[Empty()], image_generator=False))
        assert res["ai_images"] == 0
        assert all(r["source"] in ("graphics", "skipped") for r in res["paragraph_reports"])
        assert not s.scalars(select(Asset).where(Asset.story_id == st.id, Asset.is_ai_generated.is_(True))).all()


def test_ai_budget_cap(tts_story, monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_IMAGES_PER_VIDEO", "1")
    from ev_studio.config import reset_settings_cache

    reset_settings_cache(base_too=True)
    try:
        gen = CountingGenerator()
        with SessionLocal() as s:
            st = s.get(Story, tts_story)
            res = media_job(s, st, Providers(finders=[], image_generator=gen))
            assert len(gen.briefs) == 1 and res["ai_images"] == 1
            assert any("trần" in r["note"] for r in res["paragraph_reports"])
    finally:
        reset_settings_cache(base_too=True)


def test_finder_network_errors_do_not_block_and_fall_back(tts_story):
    class Flaky:
        name = "openverse"

        def __init__(self):
            self.n = 0

        def search(self, q, limit=5):
            self.n += 1
            raise RetryableError("Openverse 503")

    f, gen = Flaky(), CountingGenerator()
    with SessionLocal() as s:
        st = s.get(Story, tts_story)
        res = media_job(s, st, Providers(finders=[f], image_generator=gen))
        assert f.n == 2  # bỏ qua nguồn sau 2 lỗi, không treo cả job
        assert any("openverse" in w for w in res["warnings"]) and gen.briefs


def test_demo_autopilot_uses_ai_fallback_and_declares_synthetic(autopilot_story):
    from ev_studio.config import get_settings
    from ev_studio.models import YouTubeUpload

    def demo_videos():
        p = get_settings().artifacts_dir / "demo_youtube" / "videos.json"
        return json.loads(p.read_text()) if p.exists() else {}

    with SessionLocal() as s:
        st = s.get(Story, autopilot_story["story_id"])
        ai = list(s.scalars(select(Asset).where(Asset.story_id == st.id, Asset.provider == "demo_ai")))
        assert ai and all(a.is_ai_generated and a.is_illustration and a.is_demo for a in ai)
        from ev_studio.workers.jobs import uses_ai_images

        used = {i for sc in s.scalars(select(Scene).where(Scene.story_id == st.id)) for i in sc.asset_ids}
        assert used & {a.id for a in ai}  # ảnh AI thực sự lên hình
        assert uses_ai_images(s, st)
        up = s.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id))
        vids = demo_videos()
        if up and up.youtube_video_id in vids:  # các test khác có thể đã reset upload
            body = vids[up.youtube_video_id]["body"]
            assert body["status"]["privacyStatus"] == "private" and body["status"]["containsSyntheticMedia"] is True


# ------------------------------------------------------------------ nút "Kiểm tra" trong Thiết lập


def test_connection_tests_media_and_gemini():
    from pydantic import SecretStr

    from ev_studio.config import get_settings
    from ev_studio.services.connection_tests import TESTS

    def h(req: httpx.Request):
        if "openverse" in req.url.host:
            return httpx.Response(200, json={"results": [{"id": "1", "title": "ev", "foreign_landing_url": "https://f/1",
                                                          "url": "https://f/1.jpg", "license": "cc0", "license_version": "1.0",
                                                          "width": 2000}]})
        if "wikimedia" in req.url.host:
            return httpx.Response(503)
        if "generativelanguage" in req.url.host:
            assert req.headers["x-goog-api-key"] == "gk"
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-3.1-flash-image", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]})
        return httpx.Response(404)

    c = httpx.Client(transport=httpx.MockTransport(h))
    s = get_settings().model_copy(update={"openverse_enabled": True, "wikimedia_enabled": True,
                                          "gemini_api_key": SecretStr("gk"), "image_gen_provider": "auto"})
    r = TESTS["media"](s, client=c)
    assert r["ok"] and {x["provider"]: x["ok"] for x in r["sources"]} == {"openverse": True, "wikimedia": False}
    g = TESTS["gemini"](s, client=c)
    assert g["ok"] and g["models"] == ["gemini-3.1-flash-image"]
    bad = TESTS["gemini"](s.model_copy(update={"gemini_image_model": "khong-co"}), client=c)
    assert not bad["ok"] and "gk" not in json.dumps(bad)
    assert not TESTS["gemini"](s.model_copy(update={"gemini_api_key": None}))["ok"]


def test_gemini_key_saved_encrypted_and_masked(db_ready):
    from sqlalchemy import delete

    from ev_studio.config import get_settings, reset_settings_cache
    from ev_studio.models import AppSetting
    from ev_studio.runtime_config import PREFIX, describe, save

    try:
        with SessionLocal() as s:
            save(s, {"gemini_api_key": "AIza-secret-gemini-7777", "image_gen_provider": "gemini"}, [], "t")
            raw = s.get(AppSetting, PREFIX + "gemini_api_key").value
            assert "secret-gemini" not in json.dumps(raw)
            f = next(x for x in describe(s)["fields"] if x["key"] == "gemini_api_key")
            assert f["masked"] == "••••7777" and f["group"] == "imagegen"
            assert get_settings().gemini_api_key.get_secret_value().endswith("7777")
    finally:
        with SessionLocal() as s:
            s.execute(delete(AppSetting).where(AppSetting.key.like(PREFIX + "%")))
            s.commit()
        reset_settings_cache()


def test_spread_distributes_ai_budget_across_video():
    from ev_studio.services.media.sourcing import spread

    assert spread(list(range(27)), 12)[0] > 0 and spread(list(range(27)), 12)[-1] >= 24
    assert spread([1, 2, 3], 5) == [1, 2, 3] and spread([1, 2], 0) == []
    picked = spread(list(range(10)), 3)
    assert len(picked) == 3 and len(set(picked)) == 3
