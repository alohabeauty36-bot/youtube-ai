"""TTSProvider interface + chuẩn hoá tiếng Việt + chia đoạn (không cắt giữa từ)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from ...config import get_settings


@dataclass
class TTSConfig:
    voice: str
    model_version: str
    sample_rate: int = 48000


@dataclass
class Chunk:
    key: str
    paragraph_idx: int
    text: str  # văn bản gốc (cho subtitle)
    tts_text: str  # văn bản đã chuẩn hoá (đưa vào TTS)


@dataclass
class AudioManifest:
    path: Path
    duration: float
    sample_rate: int
    segments: list[dict] = field(default_factory=list)


class TTSProvider(Protocol):
    name: str
    is_demo: bool
    sample_rate: int
    model_version: str

    def synthesize_one(self, text: str, voice: str) -> np.ndarray:
        """Trả waveform float32 mono [-1, 1] ở self.sample_rate."""

    def list_voices(self) -> list[dict]: ...


def load_pronunciation() -> dict[str, str]:
    p = get_settings().configs_dir / "pronunciation_vi.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def normalize_vi(text: str, extra: dict[str, str] | None = None) -> str:
    """Chuẩn hoá thuật ngữ cho TTS. Số/ngày để VieNeu (sea-g2p) tự đọc."""
    rules = {**load_pronunciation(), **(extra or {})}
    out = text
    for term in sorted(rules, key=len, reverse=True):
        out = re.sub(rf"(?<![\w-]){re.escape(term)}(?![\w-])", rules[term], out)
    out = re.sub(r"(\d)\s*%", r"\1 phần trăm", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def chunk_script(spoken: str, max_chars: int = 220, extra_rules: dict[str, str] | None = None) -> list[Chunk]:
    chunks: list[Chunk] = []
    paras = [p.strip() for p in re.split(r"\n\s*\n", spoken) if p.strip()]
    n = 0
    for pi, para in enumerate(paras):
        buf = ""
        sentences = [s for s in SENT_SPLIT.split(para) if s]
        pieces: list[str] = []
        for s in sentences:
            if len(s) > max_chars:  # câu quá dài: tách theo dấu phẩy, rồi theo từ
                parts, cur = [], ""
                for w in re.split(r"(?<=,)\s+|\s+", s):
                    if len(cur) + len(w) + 1 > max_chars and cur:
                        parts.append(cur.strip())
                        cur = ""
                    cur += w + " "
                if cur.strip():
                    parts.append(cur.strip())
                pieces.extend(parts)
            else:
                pieces.append(s)
        for s in pieces:
            if buf and len(buf) + len(s) + 1 > max_chars:
                n += 1
                chunks.append(Chunk(f"seg{n}", pi, buf.strip(), normalize_vi(buf.strip(), extra_rules)))
                buf = ""
            buf += s + " "
        if buf.strip():
            n += 1
            chunks.append(Chunk(f"seg{n}", pi, buf.strip(), normalize_vi(buf.strip(), extra_rules)))
    return chunks
