"""Mã hoá token, ký link, che secrets trong log, audit."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

# ---------- mã hoá ----------

def _fernet() -> Fernet:
    key = hashlib.sha256(b"evcs-fernet|" + get_settings().secret_bytes()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_str(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_str(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:  # pragma: no cover
        raise ValueError("Không giải mã được token (SECRET_KEY đã đổi?)") from e


# ---------- ký link (review preview / media) ----------

def _sig(payload: str) -> str:
    mac = hmac.new(hashlib.sha256(b"evcs-link|" + get_settings().secret_bytes()).digest(), payload.encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()[:18]).decode().rstrip("=")


def sign_link(purpose: str, subject: str, ttl_seconds: int | None = None) -> str:
    exp = int(time.time()) + (ttl_seconds or get_settings().signed_link_ttl_seconds)
    payload = f"{purpose}:{subject}:{exp}"
    return f"{exp}.{_sig(payload)}"


def verify_link(purpose: str, subject: str, token: str) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(sig, _sig(f"{purpose}:{subject}:{exp}"))


def random_token(n: int = 18) -> str:
    return secrets.token_urlsafe(n)[:24]


# ---------- che secrets ----------

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b"),  # Telegram bot token
    re.compile(r"ya29\.[A-Za-z0-9_\-\.]+"),  # Google access token
    re.compile(r"1//[A-Za-z0-9_\-]{20,}"),  # Google refresh token
    re.compile(r"(?i)(refresh_token|access_token|client_secret|api[_-]?key|authorization)(\s*[\"']?\s*[:=]\s*[\"']?\s*(?:bearer\s+)?)([^\s\"',}]+)"),
    re.compile(r"bot\d{6,12}:[A-Za-z0-9_\-]+"),
]


def _known_secrets() -> list[str]:
    s = get_settings()
    vals = []
    for f in (s.anthropic_api_key, s.telegram_bot_token, s.pexels_api_key, s.pixabay_api_key, s.cloudflare_api_token,
              s.fal_api_key, s.admin_token, s.editor_token, s.secret_key, s.telegram_webhook_secret):
        if f is not None:
            v = f.get_secret_value()
            if len(v) >= 6:
                vals.append(v)
    return vals


def redact(text: Any) -> str:
    out = str(text)
    for v in _known_secrets():
        out = out.replace(v, "***REDACTED***")
    for p in _PATTERNS:
        if p.groups >= 3:
            out = p.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", out)
        else:
            out = p.sub("***REDACTED***", out)
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.setLevel(level)
    from .error_log import install_handler

    install_handler()  # WARNING+ của ev_studio.* → bảng error_logs (màn hình Nhật ký lỗi)
    for name in ("httpx", "googleapiclient.discovery", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------- audit ----------

def audit(session, actor: str, action: str, entity_type: str, entity_id: str | None = None, **details: Any) -> None:
    from .models import AuditLog

    safe = {k: (redact(v) if isinstance(v, str) else v) for k, v in details.items()}
    session.add(AuditLog(actor=actor, action=action, entity_type=entity_type, entity_id=entity_id, details=safe))
