"""Adapter VieNeu (SDK local / HTTP OpenAI-compatible) và DemoTTSAdapter (GẮN NHÃN DEMO)."""
from __future__ import annotations

import io
import logging
import re
import threading
import wave

import httpx
import numpy as np

from ...config import Settings
from ...errors import BlockedByCredentials, PermanentError, RetryableError

log = logging.getLogger(__name__)

# Danh sách preset v3 Turbo (README.vi.md, nhánh main, kiểm tra 11/2026). Dùng khi chưa nạp được model.
VIENEU_V3TURBO_PRESETS = [
    "Adam bựa", "Trúc Ly", "Anh Khôi", "Mai Anh", "Minh Quân Pro", "Thùy Dung", "Thiền Tâm Đức", "Ngọc Huyền",
    "Quang Sơn", "Ngọc Trân", "Minh Đức", "Phạm Tuyên", "Xuân Vĩnh", "Thanh Bình", "Ngọc Linh", "Đoan Trang",
    "Quỳnh Anh", "Mạnh Dũng", "Adam", "Thái Sơn", "Thục Đoan", "Minh Triết", "Mỹ Duyên", "Đức Trí", "Kim Thanh",
]


class VieNeuLocalAdapter:
    """Gọi Python SDK `vieneu` (pip install vieneu==3.8.5). Model khởi tạo MỘT lần mỗi worker."""
    name = "vieneu_local"
    is_demo = False
    _lock = threading.Lock()
    _engine = None

    def __init__(self, settings: Settings):
        try:
            import vieneu  # noqa: F401
        except ImportError as e:
            raise BlockedByCredentials(
                "Chưa cài VieNeu: chạy `pip install vieneu==3.8.5` trong worker TTS (CPU) "
                "hoặc `pip install \"vieneu[cuda]\"` (GPU Linux)") from e
        self.settings = settings
        self.sample_rate = 48000 if settings.vieneu_mode == "v3turbo" else 24000
        self.model_version = settings.vieneu_model_version

    def _get(self):
        with VieNeuLocalAdapter._lock:
            if VieNeuLocalAdapter._engine is None:
                from vieneu import Vieneu

                kw = {"mode": self.settings.vieneu_mode}
                if self.settings.vieneu_backend:
                    kw["backend"] = self.settings.vieneu_backend
                try:
                    VieNeuLocalAdapter._engine = Vieneu(**kw)
                except Exception as e:  # thiếu weights / không có mạng tới HuggingFace
                    raise BlockedByCredentials(f"Không khởi tạo được VieNeu (thiếu model weights?): {type(e).__name__}: {e}") from e
                self.sample_rate = int(getattr(VieNeuLocalAdapter._engine, "sample_rate", self.sample_rate))
            return VieNeuLocalAdapter._engine

    def synthesize_one(self, text: str, voice: str) -> np.ndarray:
        eng = self._get()
        try:
            wav = eng.infer(text, voice=voice)
        except Exception as e:
            raise RetryableError(f"VieNeu infer lỗi: {type(e).__name__}: {e}") from e
        return np.asarray(wav, dtype=np.float32).reshape(-1)

    def list_voices(self) -> list[dict]:
        try:
            return [{"label": lbl, "voice_id": vid} for lbl, vid in self._get().list_preset_voices()]
        except BlockedByCredentials:
            return [{"label": v, "voice_id": v} for v in VIENEU_V3TURBO_PRESETS]


class VieNeuHttpAdapter:
    """Gọi server `uv run python -m apps.openai_speech` → POST /v1/audio/speech (response_format=wav)."""
    name = "vieneu_http"
    is_demo = False

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.base = settings.vieneu_http_url.rstrip("/")
        self.client = client or httpx.Client(timeout=300)
        self.sample_rate = 48000
        self.model_version = settings.vieneu_model_version

    def synthesize_one(self, text: str, voice: str) -> np.ndarray:
        try:
            r = self.client.post(f"{self.base}/v1/audio/speech",
                                 json={"model": "vieneu-v3-turbo", "voice": voice, "input": text, "response_format": "wav"})
        except httpx.HTTPError as e:
            raise RetryableError(f"Không kết nối được VieNeu HTTP: {type(e).__name__}") from e
        if r.status_code in (429, 500, 502, 503, 504):
            raise RetryableError(f"VieNeu HTTP {r.status_code}")
        if r.status_code != 200:
            raise PermanentError(f"VieNeu HTTP {r.status_code}: {r.text[:200]}")
        with wave.open(io.BytesIO(r.content)) as w:
            self.sample_rate = w.getframerate()
            frames = w.readframes(w.getnframes())
            ch = w.getnchannels()
        a = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            a = a.reshape(-1, ch).mean(axis=1)
        return a

    def list_voices(self) -> list[dict]:
        try:
            r = self.client.get(f"{self.base}/v1/voices", timeout=10)
            r.raise_for_status()
            data = r.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            return [{"label": (v.get("name") or v.get("id")) if isinstance(v, dict) else v,
                     "voice_id": (v.get("id") or v.get("name")) if isinstance(v, dict) else v} for v in items]
        except (httpx.HTTPError, ValueError):
            return [{"label": v, "voice_id": v} for v in VIENEU_V3TURBO_PRESETS]


class DemoTTSAdapter:
    """KHÔNG PHẢI GIỌNG NÓI THẬT. Sinh âm 'tổng hợp kiểu âm tiết' (tone bursts) có thời lượng
    tỉ lệ với số âm tiết, để kiểm thử toàn bộ pipeline (đo duration, ghép, subtitle, render) khi chưa có VieNeu."""
    name = "demo"
    is_demo = True
    model_version = "demo-tone-v1"
    SYLLABLE_SECONDS = 0.335

    def __init__(self, settings: Settings | None = None, fail_on: str | None = None):
        self.sample_rate = 48000
        self.fail_on = fail_on  # test: lỗi TTS giả lập khi text chứa chuỗi này

    def synthesize_one(self, text: str, voice: str) -> np.ndarray:
        if self.fail_on and self.fail_on in text:
            raise RetryableError("DEMO: lỗi TTS giả lập")
        sr = self.sample_rate
        words = re.findall(r"\w+", text)
        seed = sum(ord(c) for c in voice) % 7
        out = []
        rng = np.random.default_rng(len(text) + seed)
        base_f0 = 110 + 8 * seed
        for i, w in enumerate(words):
            dur = self.SYLLABLE_SECONDS * (0.85 + 0.3 * rng.random())
            n = int(sr * dur)
            t = np.arange(n) / sr
            f0 = base_f0 * (1 + 0.12 * np.sin(i * 0.7))
            sig = sum((0.6 / k) * np.sin(2 * np.pi * f0 * k * t) for k in (1, 2, 3, 4))
            env = np.minimum(1, np.minimum(t / 0.02, (dur - t) / 0.04)).clip(0, 1)
            out.append((0.32 * sig * env).astype(np.float32))
            gap = 0.035
            if w and i + 1 < len(words):
                out.append(np.zeros(int(sr * gap), dtype=np.float32))
        punct_pauses = len(re.findall(r"[,;:]", text)) * 0.18 + len(re.findall(r"[.!?…]", text)) * 0.30
        out.append(np.zeros(int(sr * punct_pauses), dtype=np.float32))
        return np.concatenate(out) if out else np.zeros(sr // 2, dtype=np.float32)

    def list_voices(self) -> list[dict]:
        return [{"label": f"[DEMO] {v}", "voice_id": v} for v in VIENEU_V3TURBO_PRESETS]
