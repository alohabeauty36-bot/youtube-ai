"""Giọng đọc: demo phải nói rõ là âm giả lập; có VieNeu thì dùng giọng thật kể cả ở chế độ demo; nút Kiểm tra đọc thử thật."""
import sys
import types

import numpy as np
import pytest


class FakeEngine:
    sample_rate = 48000

    def __init__(self, fail=None):
        self.fail, self.calls = fail, []

    def infer(self, text, voice=None):
        self.calls.append(voice)
        if self.fail:
            raise ValueError(self.fail)
        t = np.arange(int(len(text) / 14 * 48000)) / 48000  # ~14 ký tự/giây như giọng thật
        return (0.3 * np.sin(2 * np.pi * 180 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)

    def list_preset_voices(self):
        return [("⭐ Quang Sơn", "Quang Sơn")]


@pytest.fixture()
def fake_vieneu(monkeypatch):
    from ev_studio import providers
    from ev_studio.services.tts.adapters import VieNeuLocalAdapter

    monkeypatch.setitem(sys.modules, "vieneu", types.ModuleType("vieneu"))
    monkeypatch.setattr(providers, "vieneu_installed", lambda: True)
    eng = FakeEngine()
    monkeypatch.setattr(VieNeuLocalAdapter, "_engine", eng)
    return eng


def test_demo_without_vieneu_is_labelled(db_ready):
    from ev_studio.config import get_settings
    from ev_studio.providers import get_tts
    from ev_studio.services.connection_tests import test_tts

    s = get_settings()
    assert get_tts(s).is_demo
    r = test_tts(s)
    assert not r["ok"] and r["demo"] and "KHÔNG phải giọng nói" in r["message"] and "--with-tts" in r["message"]


def test_auto_uses_real_vieneu_even_in_demo_mode(fake_vieneu):
    from ev_studio.config import get_settings
    from ev_studio.providers import get_tts

    t = get_tts(get_settings())
    assert t.name == "vieneu_local" and not t.is_demo
    import unicodedata

    t.synthesize_one("Xin chào", unicodedata.normalize("NFD", "Quang Sơn"))
    assert fake_vieneu.calls[-1] == "Quang Sơn"  # tên giọng NFD (gõ trên macOS) được chuẩn hoá


def test_check_button_reads_a_real_sentence_and_serves_audio(fake_vieneu, seeded):
    from fastapi.testclient import TestClient

    from ev_studio.api.main import app

    with TestClient(app) as c:
        r = c.post("/api/config/test/tts").json()
        assert r["ok"], r
        assert r["engine"] == "vieneu_local" and r["qc"]["rms"] > 0.05
        a = c.get(r["audio_url"])
        assert a.status_code == 200 and a.headers["content-type"].startswith("audio/") and a.content[:4] == b"RIFF"
        assert c.get("/api/voices/test-audio?t=1.bad").status_code == 403


def test_check_button_reports_real_error(monkeypatch, fake_vieneu):
    from ev_studio.config import get_settings
    from ev_studio.services.connection_tests import test_tts

    fake_vieneu.fail = "Voice 'Hải Đăng' not found. Available: ['Quang Sơn']"
    r = test_tts(get_settings().model_copy(update={"vieneu_voice": "Hải Đăng"}))
    assert not r["ok"] and "Giọng mặc định" in r["message"] and "Quang Sơn" in r["message"]
