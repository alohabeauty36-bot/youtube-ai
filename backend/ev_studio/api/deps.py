from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@dataclass
class Actor:
    name: str
    role: str  # owner | editor


def _match(given: str, secret) -> bool:
    return bool(secret) and hmac.compare_digest(given, secret.get_secret_value())


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def is_trusted_local(request: Request | None) -> bool:
    """Truy cập ngay trên máy chạy ứng dụng (run.py chỉ nghe 127.0.0.1) → chủ kênh, không cần mật khẩu.
    Có header proxy (X-Forwarded-For…) thì KHÔNG tin, để tránh reverse proxy biến mọi request thành 'local'."""
    if request is None or not get_settings().trust_localhost:
        return False
    if any(h in request.headers for h in ("x-forwarded-for", "x-real-ip", "forwarded")):
        return False
    return bool(request.client and request.client.host in LOOPBACK)


def current_actor(request: Request, authorization: str | None = Header(default=None), x_api_token: str | None = Header(default=None),
                  token: str | None = Query(default=None, include_in_schema=False)) -> Actor:
    s = get_settings()
    given = (authorization or "").removeprefix("Bearer ").strip() or (x_api_token or "") or (token or "")
    if given and _match(given, s.admin_token):
        return Actor("owner", "owner")
    if given and _match(given, s.editor_token):
        return Actor("editor", "editor")
    if is_trusted_local(request):
        return Actor("owner-local", "owner")
    if s.is_demo and not s.admin_token:
        return Actor("demo-owner", "owner")  # chỉ chế độ demo, chưa đặt mật khẩu
    raise HTTPException(401, "Cần đăng nhập (mật khẩu chủ kênh/biên tập viên)")


def require_owner(actor: Actor = Depends(current_actor)) -> Actor:
    if actor.role != "owner":
        raise HTTPException(403, "Chỉ chủ kênh được thực hiện thao tác này")
    return actor
