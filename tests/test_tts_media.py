"""TTS (chunk, cache, retry, VieNeu HTTP adapter), license gate, finders (mock HTTP), subtitles, graphics."""
import io
import json
import wave

import httpx
import numpy as np
import pytest

from ev_studio.errors import RetryableError
from ev_studio.services.media.license_gate import evaluate
from ev_studio.services.tts.adapters import DemoTTSAdapter, VieNeuHttpAdapter
from ev_studio.services.tts.base import chunk_script, normalize_vi
from ev_studio.services.tts.pipeline import synthesize

SCRIPT = ("Câu mở đầu ngắn. Đây là câu thứ hai có số 30% và 15 kWh.\n\n"
          "Đoạn hai nói về VF 3 và V-Green. " + "Một câu rất dài nối tiếp nhau để thử cắt, " * 12 + "kết thúc.")


def test_chunking_never_splits_words():
    chunks = chunk_script(SCRIPT, max_chars=120)
    words = " ".join(c.text for c in chunks).split()
    assert words == SCRIPT.split()
    assert all(len(c.text) <= 120 for c in chunks)
    assert {c.paragraph_idx for c in chunks} == {0, 1}


def test_normalization_terms():
    t = normalize_vi("Sạc 15 kWh cho VF 3 đạt 80% qua V-Green")
    assert "ki-lô-oát giờ" in t and "Vê Ép ba" in t and "80 phần trăm" in t and "Vi Grin" in t


def test_synthesize_cache_and_partial_resynthesis(tmp_path):
    tts = DemoTTSAdapter()
    c1 = chunk_script(SCRIPT)
    m1 = synthesize(c1, tts, "Quang Sơn", tmp_path / "a", tmp_path / "cache")
    assert m1.duration > 5 and not any(s["cache_hit"] for s in m1.segments)
    assert abs(m1.segments[-1]["end"] - m1.duration) < 0.2
    edited = SCRIPT.replace("Câu mở đầu ngắn.", "Câu mở đầu đã sửa.")
    m2 = synthesize(chunk_script(edited), tts, "Quang Sơn", tmp_path / "b", tmp_path / "cache")
    hits = [s["cache_hit"] for s in m2.segments]
    assert hits[0] is False and all(hits[1:])  # chỉ đoạn sửa được tổng hợp lại


def test_tts_failure_retries_then_raises(tmp_path):
    tts = DemoTTSAdapter(fail_on="thứ hai")
    with pytest.raises(RetryableError):
        synthesize(chunk_script(SCRIPT), tts, "v", tmp_path / "x", tmp_path / "c2", max_attempts=2)


def _wav_bytes(sec=1.0, sr=48000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        t = np.arange(int(sr * sec)) / sr
        w.writeframes((0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def test_vieneu_http_adapter_contract():
    seen = {}

    def handler(req: httpx.Request):
        if req.url.path == "/v1/audio/speech":
            seen.update(json.loads(req.content))
            return httpx.Response(200, content=_wav_bytes(), headers={"content-type": "audio/wav"})
        return httpx.Response(503)

    from ev_studio.config import get_settings

    ad = VieNeuHttpAdapter(get_settings(), client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x"))
    a = ad.synthesize_one("Xin chào", "Quang Sơn")
    assert seen == {"model": "vieneu-v3-turbo", "voice": "Quang Sơn", "input": "Xin chào", "response_format": "wav"}
    assert abs(len(a) / ad.sample_rate - 1.0) < 0.01


def test_vieneu_local_adapter_reports_missing_package():
    from ev_studio.config import get_settings
    from ev_studio.errors import BlockedByCredentials
    from ev_studio.services.tts.adapters import VieNeuLocalAdapter

    try:
        import vieneu  # noqa: F401
        pytest.skip("vieneu đã cài")
    except ImportError:
        with pytest.raises(BlockedByCredentials, match="pip install vieneu"):
            VieNeuLocalAdapter(get_settings())


@pytest.mark.parametrize("asset,status", [
    ({"provider": "web_reference", "license_name": "CC0", "source_url": "https://google.com/img"}, "rejected"),
    ({"provider": "own_library", "license_name": None}, "rejected"),
    ({"provider": "wikimedia", "license_name": "CC BY-NC 4.0", "source_url": "u", "attribution_text": "a"}, "rejected"),
    ({"provider": "wikimedia", "license_name": "CC BY-ND 4.0", "source_url": "u", "attribution_text": "a"}, "rejected"),
    ({"provider": "wikimedia", "license_name": "CC BY 4.0", "source_url": "u"}, "rejected"),  # thiếu ghi công
    ({"provider": "wikimedia", "license_name": "CC BY 4.0", "source_url": "u", "attribution_text": "Tác giả — CC BY 4.0"}, "approved"),
    ({"provider": "own_library", "license_name": "Owner-provided"}, "rejected"),  # thiếu bằng chứng
    ({"provider": "own_library", "license_name": "Owner-provided", "permission_proof": "Hợp đồng số 1"}, "approved"),
    ({"provider": "pexels", "license_name": "Pexels License", "source_url": "https://pexels.com/p/1", "allowed_commercial": True}, "approved"),
    ({"provider": "pexels", "license_name": "Editorial use only", "source_url": "u"}, "rejected"),
])
def test_license_gate(asset, status):
    assert evaluate(asset).status == status


def test_wrong_model_becomes_illustration():
    d = evaluate({"provider": "pexels", "license_name": "Pexels License", "source_url": "u", "model_name": "VF 3"},
                 story_models=["VF 8"])
    assert d.status == "approved" and d.is_illustration and "minh họa" in d.reason


def test_pexels_and_wikimedia_finders_with_mock_http():
    from ev_studio.services.media.finders import PexelsFinder, WikimediaFinder

    def pex(req):
        assert req.headers["Authorization"] == "k"
        return httpx.Response(200, json={"photos": [{"id": 1, "url": "https://www.pexels.com/photo/1", "photographer": "An",
                                                     "alt": "charging", "src": {"large2x": "https://images.pexels.com/1.jpg"}}]})

    f = PexelsFinder("k", client=httpx.Client(transport=httpx.MockTransport(pex)))
    r = f.search("ev")[0]
    assert r.license_name == "Pexels License" and evaluate(r.__dict__).status == "approved"

    def wm(req):
        return httpx.Response(200, json={"query": {"pages": {"1": {"title": "File:x.jpg", "imageinfo": [{
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:x.jpg", "thumburl": "https://upload/x.jpg",
            "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 2.0"}, "Artist": {"value": "<a>B</a>"}}}]}}}})

    w = WikimediaFinder("ua", client=httpx.Client(transport=httpx.MockTransport(wm)))
    r = w.search("ev")[0]
    assert r.creator == "B" and evaluate(r.__dict__).status == "rejected"


def test_subtitles_from_segment_timestamps(tmp_path):
    from ev_studio.services.subtitles import build_cues, write_ass, write_srt

    cues = build_cues([{"text": "Một hai ba bốn năm sáu bảy tám chín mười " * 3, "start": 1.0, "end": 7.0}])
    assert cues[0][0] == 1.0 and abs(cues[-1][1] - 7.0) < 1e-6
    assert all(len(l) <= 42 for _, _, t in cues for l in t.split("\n"))
    assert "00:00:01,000 -->" in write_srt(cues, tmp_path / "a.srt").read_text()
    assert "Dialogue: 0,0:00:01.00" in write_ass(cues, tmp_path / "a.ass").read_text()


def test_vietnamese_glyphs_available():
    from ev_studio.services.media.graphics import glyph_check

    assert glyph_check()
