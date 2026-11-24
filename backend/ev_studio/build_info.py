"""Dấu vân tay mã nguồn backend.

Dùng để phát hiện tình huống: đã cập nhật code (git pull) nhưng cửa sổ máy chủ CŨ vẫn đang chạy.
Khi đó giao diện web (đọc trực tiếp từ đĩa) đã là bản mới, còn API trong bộ nhớ vẫn là bản cũ
→ các trang mới gọi API chưa có và nhận về trang 404.
Module này không phụ thuộc thư viện ngoài để run.py có thể import trước khi khởi động máy chủ.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


def code_fingerprint() -> str:
    """Băm toàn bộ file .py của gói ev_studio (theo thứ tự đường dẫn) → chuỗi 12 ký tự."""
    h = hashlib.sha256()
    for p in sorted(PKG_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        h.update(p.relative_to(PKG_DIR).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()[:12]
