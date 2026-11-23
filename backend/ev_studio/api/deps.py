from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Header, HTTPException, Query
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


def current_actor(authorization: str | None = Header(default=None), x_api_token: str | None = Header(default=None),
                  token: str | None = Query(default=None, include_in_schema=False)) -> Actor:
    s = get_settings()
    given = (authorization or "").removeprefix("Bearer ").strip() or (x_api_token or "") or (token or "")
    if given and _match(given, s.admin_token):
        return Actor("owner", "owner")
    if given and _match(given, s.editor_token):
        return Actor("editor", "editor")
    if s.is_demo and not s.admin_token:
        return Actor("demo-owner", "owner")  # chỉ chế độ demo local, không có token cấu hình
    raise HTTPException(401, "Cần đăng nhập (Bearer token)")


def require_owner(actor: Actor = Depends(current_actor)) -> Actor:
    if actor.role != "owner":
        raise HTTPException(403, "Chỉ chủ kênh được thực hiện thao tác này")
    return actor
