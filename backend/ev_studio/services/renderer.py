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


MOTIONS = ("zoom_in", "zoom_out", "pan_right", "pan_left")
FADE = 0.35  # giây: lớp chữ trượt + hiện dần


def _zoompan(motion: str, frames: int, fps: int) -> str:
    """Ken Burns có hướng: phóng to / thu nhỏ / lia phải / lia trái (đổi luân phiên giữa các cảnh)."""
    n = max(1, frames)
    if motion == "zoom_out":
        z, x, y = f"1.14-0.12*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == "pan_right":
        z, x, y = "1.12", f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"
    elif motion == "pan_left":
        z, x, y = "1.12", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"
    else:
        z, x, y = f"1.02+0.12*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    return (f"scale=2496:1404:force_original_aspect_ratio=increase,crop=2496:1404,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s=1920x1080:fps={fps}")


def render_layered_clip(bg: dict, overlay: Path | None, duration: float, out: Path, fps: int) -> Path:
    """Một cảnh = nền chuyển động (video B-roll / ảnh Ken Burns / đồ họa Ken Burns) + lớp phủ RGBA trượt vào.
    bg = {"kind": "video"|"image"|"graphic", "path": str, "offset": giây, "motion": ..., "dim": 0..1}"""
    frames = max(1, round(duration * fps))
    dim = float(bg.get("dim") or 0)
    grade = f",eq=brightness={-0.25 * dim:.3f}:saturation={1.08 - 0.3 * dim:.3f}" if dim else ",eq=saturation=1.06:contrast=1.03"
    if bg["kind"] == "video":
        inp = ["-ss", f"{float(bg.get('offset') or 0):.2f}", "-stream_loop", "-1", "-i", str(bg["path"])]
        vf = (f"[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,fps={fps}"
              f"{grade},format=yuv420p[bg]")
    else:
        inp = ["-i", str(bg["path"])]
        vf = f"[0:v]{_zoompan(bg.get('motion') or 'zoom_in', frames, fps)},setsar=1{grade},format=yuv420p[bg]"
    cmd = ["ffmpeg", "-y", "-v", "error", *inp]
    if overlay is not None:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(overlay)]
        vf += (f";[1:v]format=rgba,fade=t=in:st=0:d={FADE}:alpha=1[ov];"
               f"[bg][ov]overlay=x='if(lt(t,{FADE}),-70*(1-t/{FADE}),0)':y=0:shortest=0:format=auto,format=yuv420p[v]")
        outmap = "[v]"
    else:
        outmap = "[bg]"
    tmp = out.with_suffix(".tmp.mp4")
    _run(cmd + ["-filter_complex", vf, "-map", outmap, "-frames:v", str(frames), "-r", str(fps), "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", str(tmp)])
    os.replace(tmp, out)
    return out


ASPECT = 16 / 9  # video YouTube luôn 16:9 (1920×1080)
MIN_SOURCE_ASPECT = 1.3  # tư liệu nguồn hẹp hơn ~4:3 (dọc/vuông) cắt sang 16:9 sẽ mất chủ thể → không dùng làm nền


def media_aspect(path: Path, media_type: str) -> float | None:
    """Tỉ lệ rộng/cao của ảnh hoặc video nguồn (None nếu không đọc được)."""
    try:
        if media_type == "video":
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height:stream_side_data=rotation", "-of", "json", str(path)],
                               capture_output=True, text=True)
            import json as _json

            st = (_json.loads(r.stdout or "{}").get("streams") or [{}])[0]
            w, h = int(st.get("width") or 0), int(st.get("height") or 0)
            rot = abs(int(next((d.get("rotation", 0) for d in st.get("side_data_list") or [] if "rotation" in d), 0) or 0))
            if rot in (90, 270):
                w, h = h, w
        else:
            from PIL import Image

            with Image.open(path) as im:
                w, h = im.size
        return (w / h) if w and h else None
    except Exception:  # noqa: BLE001
        return None


def is_landscape_enough(path: Path, media_type: str) -> bool:
    a = media_aspect(path, media_type)
    return a is not None and a >= MIN_SOURCE_ASPECT


def video_duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def still_from_video(path: Path, at: float, out: Path) -> Path | None:
    """Khung hình đại diện (cho trang xem cảnh / thumbnail)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080", str(out)],
                       capture_output=True, text=True)
    return out if r.returncode == 0 and out.exists() else None


def compose(scene_clips: list[Path], audio: Path, subtitles_ass: Path | None, out: Path, work: Path,
            music: Path | None = None, music_db: float = -20.0) -> Path:
    s = get_settings()
    work.mkdir(parents=True, exist_ok=True)
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in scene_clips), encoding="utf-8")
    vf = ["fps=%d" % s.render_fps, "format=yuv420p"]
    if subtitles_ass and s.render_burn_subtitles:
        vf.append(f"subtitles='{_ff_escape(subtitles_ass)}':fontsdir='{_ff_escape(Path(s.font_path).parent)}'")
    tmp = out.with_suffix(".tmp.mp4")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio)]
    if music is not None:
        # nhạc nền lặp lại, nhỏ hơn giọng music_db dB, tự "nhún" xuống thêm khi có giọng đọc (sidechain), chặn đỉnh -1 dB
        gain = 10 ** (music_db / 20)
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fc = (f"[0:v]{','.join(vf)}[v];"
              "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][key];"
              f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={gain:.4f}[m];"
              "[m][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck];"
              "[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.89[a]")
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-vf", ",".join(vf)]
    _run(cmd + ["-c:v", "libx264", "-preset", s.render_preset, "-crf", "21", "-profile:v", "high", "-pix_fmt", "yuv420p",
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
