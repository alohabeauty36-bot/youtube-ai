"""FFmpeg renderer: H.264/AAC MP4 1920×1080 30fps yuv420p 48 kHz; phụ đề burn-in (libass); preview 360p.
Mọi file ghi vào *.tmp rồi atomic rename; crash không xoá artifact bước trước."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from ..config import get_settings
from ..errors import PermanentError, RetryableError

log = logging.getLogger(__name__)


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    log.info("ffmpeg: %s", " ".join(cmd[:6]) + " …")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RetryableError("FFmpeg quá thời gian") from e
    if r.returncode != 0:
        raise PermanentError(f"FFmpeg lỗi: {r.stderr[-800:]}")
    return r


def render_scene_clip(image: Path, duration: float, out: Path, ken_burns: bool, fps: int) -> Path:
    """Mỗi cảnh → clip trung gian (không âm thanh)."""
    frames = max(1, round(duration * fps))
    if ken_burns:
        vf = (f"scale=2304:1296,zoompan=z='min(zoom+0.0006,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={frames}:s=1920x1080:fps={fps},format=yuv420p")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(image), "-vf", vf, "-frames:v", str(frames)]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(fps), "-i", str(image),
               "-vf", "format=yuv420p", "-frames:v", str(frames), "-tune", "stillimage"]
    tmp = out.with_suffix(".tmp.mp4")
    _run(cmd + ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-r", str(fps), "-an", str(tmp)])
    os.replace(tmp, out)
    return out


def compose(scene_clips: list[Path], audio: Path, subtitles_ass: Path | None, out: Path, work: Path) -> Path:
    s = get_settings()
    work.mkdir(parents=True, exist_ok=True)
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in scene_clips), encoding="utf-8")
    vf = ["fps=%d" % s.render_fps, "format=yuv420p"]
    if subtitles_ass and s.render_burn_subtitles:
        vf.append(f"subtitles='{_ff_escape(subtitles_ass)}':fontsdir='{_ff_escape(Path(s.font_path).parent)}'")
    tmp = out.with_suffix(".tmp.mp4")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
          "-map", "0:v:0", "-map", "1:a:0", "-vf", ",".join(vf),
          "-c:v", "libx264", "-preset", s.render_preset, "-crf", "21", "-profile:v", "high", "-pix_fmt", "yuv420p",
          "-r", str(s.render_fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
          "-shortest", "-movflags", "+faststart", str(tmp)])
    os.replace(tmp, out)
    return out


def _ff_escape(p: Path) -> str:
    """Đường dẫn trong filtergraph FFmpeg: dùng '/', escape ':' (ổ đĩa Windows C:) và dấu nháy."""
    return p.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")


def preview_360p(src: Path, out: Path) -> Path:
    tmp = out.with_suffix(".tmp.mp4")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)])
    os.replace(tmp, out)
    return out


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
