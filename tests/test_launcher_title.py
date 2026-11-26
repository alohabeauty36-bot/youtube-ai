"""Cửa sổ terminal của start.bat hiện tên phần mềm (thanh tiêu đề + banner)."""
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_py() -> dict:
    return runpy.run_path(str(ROOT / "run.py"), run_name="launcher_test")


def test_bat_files_set_window_title():
    for name in ("start.bat", "start-giong-that.bat"):
        lines = (ROOT / name).read_bytes().decode("ascii").splitlines()
        assert lines[0].strip().lower() == "@echo off"
        assert lines[1].lower().startswith("title ev creator studio"), name


def test_voice_bat_keeps_crlf():
    raw = (ROOT / "start-giong-that.bat").read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_banner_shows_name_and_version(capsys):
    g = _run_py()
    assert g["APP_NAME"] == "EV Creator Studio"
    assert g["app_version"]()  # đọc từ backend/pyproject.toml
    g["show_banner"]()
    out = capsys.readouterr().out
    assert "EV Creator Studio" in out and "v" + g["app_version"]() in out
    # vừa cửa sổ cmd mặc định 80 cột, chỉ ký tự ASCII trong phần chữ lớn
    for line in g["BANNER"].splitlines():
        assert len(line) <= 80
        assert line.isascii()


def test_title_never_breaks_startup(monkeypatch):
    g = _run_py()
    monkeypatch.setitem(g, "IS_WIN", True)  # không có ctypes.windll trên Linux → phải im lặng bỏ qua
    g["set_console_title"]("EV Creator Studio")
