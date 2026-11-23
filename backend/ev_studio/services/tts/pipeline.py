"""Pipeline TTS: chunk → synth từng segment (cache immutable) → QC → ghép có pause → đo bằng ffprobe."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import wave
from pathlib import Path

import numpy as np

from ...errors import PermanentError, RetryableError
from .base import AudioManifest, Chunk, TTSProvider

log = logging.getLogger(__name__)

PAUSE_SENTENCE = 0.15
PAUSE_PARAGRAPH = 0.35


def write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return a, sr


def ffprobe_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise PermanentError(f"ffprobe lỗi với {path.name}: {r.stderr[:200]}")
    return float(json.loads(r.stdout)["format"]["duration"])


def segment_qc(audio: np.ndarray, sr: int, text: str) -> dict:
    dur = len(audio) / sr if sr else 0
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    chars = max(1, len(text))
    cps = chars / dur if dur else 0
    issues = []
    if dur < 0.3:
        issues.append("đoạn rỗng/quá ngắn")
    if rms < 0.005:
        issues.append("gần như im lặng")
    if peak >= 0.999:
        issues.append("clipping")
    if dur and not (4 <= cps <= 35):
        issues.append(f"tốc độ bất thường {cps:.1f} ký tự/giây (có thể lỗi đọc)")
    return {"duration": round(dur, 3), "peak": round(peak, 4), "rms": round(rms, 4), "chars_per_sec": round(cps, 2),
            "issues": issues, "ok": not issues}


def cache_key(text: str, voice: str, model_version: str) -> str:
    return hashlib.sha256(f"{text}|{voice}|{model_version}".encode()).hexdigest()


def synthesize(chunks: list[Chunk], provider: TTSProvider, voice: str, out_dir: Path, cache_dir: Path,
               max_attempts: int = 3) -> AudioManifest:
    """Chỉ tổng hợp segment chưa có trong cache → sửa 1 đoạn chỉ tái tổng hợp đoạn đó."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    sr = provider.sample_rate
    pieces: list[np.ndarray] = []
    segs: list[dict] = []
    t = 0.0
    for i, ch in enumerate(chunks):
        key = cache_key(ch.tts_text, voice, provider.model_version)
        cpath = cache_dir / f"{key}.wav"
        hit = cpath.exists()
        if hit:
            audio, sr_c = read_wav(cpath)
            if sr_c != sr:
                hit = False
        if not hit:
            last: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    audio = provider.synthesize_one(ch.tts_text, voice)
                    sr = provider.sample_rate
                    q = segment_qc(audio, sr, ch.tts_text)
                    if "đoạn rỗng/quá ngắn" in q["issues"] or "gần như im lặng" in q["issues"]:
                        raise RetryableError(f"Segment {ch.key} QC lỗi: {q['issues']}")
                    write_wav(cpath, audio, sr)
                    last = None
                    break
                except RetryableError as e:
                    last = e
                    log.warning("TTS %s lần %d lỗi: %s", ch.key, attempt + 1, e)
            if last is not None:
                raise RetryableError(f"TTS thất bại ở {ch.key} sau {max_attempts} lần: {last}")
        qc = segment_qc(audio, sr, ch.tts_text)
        dur = len(audio) / sr
        segs.append({"idx": i, "segment_key": ch.key, "paragraph_idx": ch.paragraph_idx, "text": ch.text,
                     "tts_text": ch.tts_text, "text_hash": key, "path": str(cpath), "duration": round(dur, 3),
                     "start": round(t, 3), "end": round(t + dur, 3), "qc": qc, "cache_hit": hit})
        pieces.append(audio)
        t += dur
        if i + 1 < len(chunks):
            gap = PAUSE_PARAGRAPH if chunks[i + 1].paragraph_idx != ch.paragraph_idx else PAUSE_SENTENCE
            pieces.append(np.zeros(int(sr * gap), dtype=np.float32))
            t += gap
    full = np.concatenate(pieces) if pieces else np.zeros(sr, dtype=np.float32)
    path = out_dir / "narration.wav"
    write_wav(path, full, sr)
    measured = ffprobe_duration(path)
    return AudioManifest(path=path, duration=measured, sample_rate=sr, segments=segs)


def make_sample(chunks: list[Chunk], provider: TTSProvider, voice: str, out_path: Path, cache_dir: Path,
                seconds: float = 30) -> AudioManifest:
    """Mẫu nghe thử ~30s (dùng ở trang Biên tập)."""
    picked, est = [], 0.0
    for ch in chunks:
        picked.append(ch)
        est += len(ch.tts_text.split()) * 0.4
        if est >= seconds:
            break
    m = synthesize(picked, provider, voice, out_path.parent / "sample_tmp", cache_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(m.path, out_path)
    m.path = out_path
    return m
