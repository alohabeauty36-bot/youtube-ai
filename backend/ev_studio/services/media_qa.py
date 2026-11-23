"""Chuỗi QA render (mục G): ffprobe + blackdetect + freezedetect + volumedetect + license + nguồn + thumbnail + SHA-256."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from PIL import Image

from .media.graphics import glyph_check
from .media.license_gate import file_sha256


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    return json.loads(r.stdout)


def _filter_log(path: Path, vf: str | None = None, af: str | None = None) -> str:
    cmd = ["ffmpeg", "-v", "info", "-nostats", "-i", str(path)]
    if vf:
        cmd += ["-vf", vf, "-an"]
    if af:
        cmd += ["-af", af, "-vn"]
    cmd += ["-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def run_media_qa(video: Path, *, exact_10min: bool, dur_min: float, dur_max: float, assets: list[dict],
                 on_screen_facts_ok: bool, description: str, sources: list[dict], thumbnail: Path | None,
                 subtitle: Path | None, music_present: bool = False) -> dict:
    checks: list[dict] = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    info = probe(video)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    add("Video H.264 yuv420p", v.get("codec_name") == "h264" and v.get("pix_fmt") == "yuv420p",
        f"{v.get('codec_name')} {v.get('pix_fmt')}")
    add("Độ phân giải 1920×1080 (16:9)", (v.get("width"), v.get("height")) == (1920, 1080), f"{v.get('width')}×{v.get('height')}")
    fr = v.get("r_frame_rate", "0/1").split("/")
    fps = float(fr[0]) / float(fr[1] or 1) if len(fr) == 2 else 0
    add("30 fps", abs(fps - 30) < 0.01, f"{fps:.2f}")
    add("Âm thanh AAC 48 kHz", a.get("codec_name") == "aac" and a.get("sample_rate") == "48000",
        f"{a.get('codec_name')} {a.get('sample_rate')}")
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    add("Thời lượng " + (f"{dur_min:.0f}–{dur_max:.0f}s" if exact_10min else "hợp lệ"),
        (dur_min <= dur <= dur_max) if exact_10min else dur > 0, f"{dur:.1f}s")

    log = _filter_log(video, vf="blackdetect=d=0.5:pix_th=0.06")
    blacks = re.findall(r"black_duration:([\d.]+)", log)
    add("Không đen hình > 0.5s", not blacks, f"{len(blacks)} đoạn đen")
    log = _filter_log(video, vf="freezedetect=n=0.001:d=12.5")
    freezes = re.findall(r"freeze_duration: ([\d.]+)", log)
    add("Không đứng hình > 12s", not freezes, f"{len(freezes)} đoạn")
    log = _filter_log(video, af="volumedetect")
    mx = re.search(r"max_volume: (-?[\d.]+) dB", log)
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", log)
    mxv = float(mx.group(1)) if mx else 0.0
    add("Audio không clipping (max < -0.3 dB)", mx is not None and mxv < -0.3, f"max {mxv} dB, mean {mean.group(1) if mean else '?'} dB")
    add("Nhạc không át giọng", True if not music_present else False,
        "Không có nhạc nền trong MVP" if not music_present else "Cần kiểm tra thủ công mức nhạc")
    unlicensed = [x["id"] for x in assets if x.get("review_status") != "approved"]
    add("Mọi asset đã qua license gate", not unlicensed, ", ".join(unlicensed))
    missing_src = [s["url"] for s in sources if s["url"] not in description]
    add("Nguồn hiển thị đúng (mô tả + dữ kiện có claim)", on_screen_facts_ok and not missing_src,
        f"thiếu {len(missing_src)} nguồn trong mô tả" if missing_src else "")
    th_ok, th_detail = False, "không có thumbnail"
    if thumbnail and thumbnail.exists():
        with Image.open(thumbnail) as im:
            th_ok = im.size == (1280, 720) and thumbnail.stat().st_size < 2 * 1024 * 1024
            th_detail = f"{im.size[0]}×{im.size[1]}, {thumbnail.stat().st_size // 1024} KB"
    add("Thumbnail 1280×720 < 2MB", th_ok, th_detail)
    add("Font tiếng Việt đủ dấu + phụ đề có", glyph_check() and bool(subtitle and subtitle.exists() and subtitle.stat().st_size > 0))
    sha = file_sha256(video) if video.exists() else None
    add("SHA-256 file upload", bool(sha), sha or "")
    passed = all(c["ok"] for c in checks)
    return {"passed": passed, "checks": checks, "summary": f"{sum(c['ok'] for c in checks)}/{len(checks)} đạt",
            "sha256": sha, "duration": dur, "width": v.get("width"), "height": v.get("height")}
