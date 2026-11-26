#!/usr/bin/env python3
"""EV Creator Studio — chạy bằng MỘT lệnh:

    python run.py            (Windows: py run.py hoặc bấm đúp start.bat)

Lần đầu: tự tạo môi trường .venv, cài thư viện, tạo database, rồi mở http://localhost:8010.
Các lần sau: khởi động ngay. Mọi key/API cấu hình trong trang Thiết lập của ứng dụng (không cần sửa file).

Tuỳ chọn:
  --port 8010         cổng (mặc định 8010)
  --host 127.0.0.1    địa chỉ nghe. Mặc định chỉ máy này truy cập được (không cần mật khẩu).
                      Dùng 0.0.0.0 để mở cho máy khác trong mạng — khi đó BẮT BUỘC đặt mật khẩu chủ kênh.
  --with-tts          cài thêm VieNeu-TTS (giọng đọc thật, chạy CPU)
  --build-web         build lại giao diện web từ frontend/ (cần Node.js)
  --no-browser        không tự mở trình duyệt
  --reinstall         cài lại thư viện
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
BACKEND = ROOT / "backend"
WEB_DIST = BACKEND / "ev_studio" / "web_dist"
IS_WIN = platform.system() == "Windows"


APP_NAME = "EV Creator Studio"
BANNER = r"""
  _____   __   ___              _             ___ _           _ _
 | __\ \ / /  / __|_ _ ___ __ _| |_ ___ _ _  / __| |_ _  _ __| (_)___
 | _| \ V /  | (__| '_/ -_) _` |  _/ _ \ '_| \__ \  _| || / _` | / _ \
 |___| \_/    \___|_| \___\__,_|\__\___/_|   |___/\__|\_,_\__,_|_\___/
"""


def say(msg: str) -> None:
    print(f"[EV Studio] {msg}", flush=True)


def app_version() -> str:
    try:
        for line in (BACKEND / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def set_console_title(title: str) -> None:
    """Tên phần mềm trên thanh tiêu đề cửa sổ terminal (start.bat / cmd / PowerShell / Terminal)."""
    try:
        if IS_WIN:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        elif sys.stdout.isatty():
            sys.stdout.write(f"\033]0;{title}\007")
            sys.stdout.flush()
    except Exception:  # noqa: BLE001 — tiêu đề chỉ để trang trí, không được làm hỏng khởi động
        pass


def show_banner() -> None:
    ver = app_version()
    set_console_title(f"{APP_NAME} {ver}".strip())
    print(BANNER.rstrip("\n"), flush=True)
    print(f"   {APP_NAME}{'  v' + ver if ver else ''}  ·  xưởng làm video YouTube tự động", flush=True)
    print(flush=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")


def in_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV.resolve()


def ensure_venv(args: argparse.Namespace) -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"Cần Python 3.11 trở lên (đang dùng {platform.python_version()}). Tải tại https://www.python.org/downloads/")
    if in_venv():
        return
    if not venv_python().exists():
        say("Lần chạy đầu: tạo môi trường Python riêng (.venv)…")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    # chạy lại chính script này bằng Python của .venv
    cmd = [str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]]
    if IS_WIN:  # os.execv trên Windows không giữ cửa sổ console ổn định → chạy tiến trình con
        try:
            sys.exit(subprocess.call(cmd))
        except KeyboardInterrupt:
            sys.exit(0)
    os.execv(cmd[0], cmd)


def ensure_packages(args: argparse.Namespace) -> None:
    extra = "[tts]" if args.with_tts else ""
    sig = hashlib.sha256(((BACKEND / "pyproject.toml").read_bytes() + extra.encode())).hexdigest()
    marker = VENV / ".evcs_installed"
    installed = marker.read_text().split() if marker.exists() else []
    if not args.reinstall and sig in installed:
        return
    say("Cài thư viện (chỉ lần đầu hoặc khi có cập nhật, mất vài phút)…")
    subprocess.check_call([str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    subprocess.check_call([str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e", f"{BACKEND}{extra}"])
    keep = [x for x in installed if x != sig][-3:]
    marker.write_text(" ".join(keep + [sig]))


def ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    say("⚠️  Chưa có FFmpeg — cần để dựng video.")
    if IS_WIN and shutil.which("winget"):
        ans = input("    Cài FFmpeg tự động bằng winget? [Y/n] ").strip().lower()
        if ans in ("", "y", "yes", "c", "co", "có"):
            subprocess.call(["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements", "--accept-package-agreements"])
            say("Đã cài FFmpeg. Hãy ĐÓNG cửa sổ này và chạy lại lệnh để Windows nhận PATH mới.")
            sys.exit(0)
    elif platform.system() == "Darwin":
        say("    Cài bằng: brew install ffmpeg")
    else:
        say("    Cài bằng: sudo apt install ffmpeg   (hoặc trình quản lý gói của bạn)")
    say("    Ứng dụng vẫn chạy, nhưng bước dựng video sẽ báo lỗi cho tới khi có FFmpeg.")


def ensure_web(args: argparse.Namespace) -> None:
    if (WEB_DIST / "index.html").exists() and not args.build_web:
        return
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        say("⚠️  Không thấy giao diện đã build và không có Node.js để build. Cài Node.js 20+ rồi chạy: python run.py --build-web")
        return
    fe = ROOT / "frontend"
    say("Build giao diện web…")
    subprocess.check_call([npm, "ci" if (fe / "package-lock.json").exists() else "install", "--no-audit", "--no-fund"], cwd=fe)
    env = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1", "NEXT_PUBLIC_API_BASE": ""}
    subprocess.check_call([npm, "run", "build"], cwd=fe, env=env)
    if WEB_DIST.exists():
        shutil.rmtree(WEB_DIST)
    shutil.copytree(fe / "out", WEB_DIST)


def migrate() -> None:
    """Tạo/nâng cấp database bằng Alembic (DB cũ tạo bằng create_all → stamp trước)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from ev_studio.db import get_engine

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["configure_logger"] = False  # giữ log gọn, không nhân đôi
    insp = inspect(get_engine())
    tables = set(insp.get_table_names())
    if "stories" in tables and "alembic_version" not in tables:
        command.stamp(cfg, "0001")  # các migration sau 0001 tự bỏ qua bảng đã có
    command.upgrade(cfg, "head")


def port_busy(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1" if host in ("0.0.0.0", "") else host, port)) == 0


def running_server_is_stale(url: str) -> bool:
    """True nếu máy chủ đang chạy ở url là EV Studio nhưng mã trong bộ nhớ khác mã trên đĩa."""
    import json

    try:
        with urllib.request.urlopen(url + "/api/health", timeout=2) as r:
            info = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return False  # không phải EV Studio hoặc không trả lời → giữ thông báo cũ
    sys.path.insert(0, str(BACKEND))
    from ev_studio.build_info import code_fingerprint

    # máy chủ quá cũ chưa có trường "build" cũng tính là bản cũ
    return info.get("build") != code_fingerprint()


def open_when_ready(url: str) -> None:
    for _ in range(120):
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as r:
                if r.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            time.sleep(0.5)


def main() -> None:
    ap = argparse.ArgumentParser(description="Chạy EV Creator Studio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--with-tts", action="store_true")
    ap.add_argument("--build-web", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reinstall", action="store_true")
    args = ap.parse_args()

    os.chdir(ROOT)
    ensure_venv(args)
    show_banner()  # sau ensure_venv: tiến trình chạy trong .venv mới in, nên banner không bị in hai lần
    ensure_packages(args)
    ensure_ffmpeg()
    ensure_web(args)

    local_only = args.host in ("127.0.0.1", "localhost", "::1")
    os.environ.setdefault("PUBLIC_BASE_URL", f"http://localhost:{args.port}")
    os.environ["EVCS_TRUST_LOCALHOST"] = "1" if local_only else "0"
    url = f"http://localhost:{args.port}"
    if port_busy(args.host, args.port):
        if running_server_is_stale(url):
            say("=" * 64)
            say(f"⚠️  Cổng {args.port} đang chạy máy chủ BẢN CŨ (trước khi cập nhật code).")
            say("    Giao diện đã là bản mới nhưng API vẫn là bản cũ → các trang mới sẽ báo lỗi 404.")
            say("    Cách sửa: tìm cửa sổ EV Studio đang chạy, bấm Ctrl+C (hoặc đóng cửa sổ), rồi mở lại start.bat.")
            say("=" * 64)
            return
        say(f"Cổng {args.port} đang được dùng — có thể ứng dụng đã chạy. Mở {url} hoặc dùng --port 8020.")
        if not args.no_browser:
            webbrowser.open(url)
        return

    sys.path.insert(0, str(BACKEND))
    migrate()
    from ev_studio.config import get_settings

    s = get_settings()
    say("=" * 64)
    say(f"Đang chạy tại {url}   (Ctrl+C để dừng)")
    say(f"Chế độ: {s.app_mode.upper()}  — đổi chế độ và nhập key API trong Thiết lập (mỗi dịch vụ một tab)")
    say("Video tải lên YouTube ở trạng thái RIÊNG TƯ; chỉ công khai khi bạn duyệt hoặc khi bật Tự động hoàn toàn.")
    if not local_only:
        say("⚠️  Đang mở cho máy khác truy cập: hãy đặt 'Mật khẩu chủ kênh' trong Thiết lập → Bảo mật dashboard.")
    say("=" * 64)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()

    import uvicorn

    uvicorn.run("ev_studio.api.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("Đã dừng.")
    except subprocess.CalledProcessError as e:
        say(f"Lệnh cài đặt thất bại ({e}). Kiểm tra kết nối mạng rồi chạy lại.")
        sys.exit(1)
