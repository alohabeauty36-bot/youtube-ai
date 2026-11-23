"""Phụ đề SRT + ASS từ mốc thời gian segment TTS thật (không ước lượng theo cả bài)."""
from __future__ import annotations

from pathlib import Path

MAX_LINE = 42


def _fmt_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def split_lines(text: str, max_line: int = MAX_LINE) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > max_line and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def build_cues(segments: list[dict]) -> list[tuple[float, float, str]]:
    cues = []
    for s in segments:
        lines = split_lines(s["text"])
        blocks = [lines[i:i + 2] for i in range(0, len(lines), 2)]
        total_chars = sum(len(" ".join(b)) for b in blocks) or 1
        t = s["start"]
        span = s["end"] - s["start"]
        for b in blocks:
            d = span * len(" ".join(b)) / total_chars
            cues.append((t, t + d, "\n".join(b)))
            t += d
    return cues


def write_srt(cues, path: Path) -> Path:
    out = []
    for i, (a, b, txt) in enumerate(cues, 1):
        out.append(f"{i}\n{_fmt_srt(a)} --> {_fmt_srt(b)}\n{txt}\n")
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def write_ass(cues, path: Path, font_name: str = "DejaVu Sans") -> Path:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},46,&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,3,2,0,2,120,120,190,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    nl = "\\N"
    ev = ["Dialogue: 0,{},{},Default,,0,0,0,,{}".format(_fmt_ass(a), _fmt_ass(b), txt.replace("\n", nl)) for a, b, txt in cues]
    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return path
