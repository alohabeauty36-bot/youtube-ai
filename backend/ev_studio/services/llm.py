"""Client Anthropic (API chính thức) trả JSON đã parse + usage. Không log nội dung key."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..errors import BlockedByCredentials, PermanentError, RetryableError
from ..security import redact

log = logging.getLogger(__name__)

# Ước tính chi phí; chỉnh theo bảng giá hiện hành trong .env nếu cần (USD / 1M token)
DEFAULT_PRICE_IN = 3.0
DEFAULT_PRICE_OUT = 15.0


def parse_json_loose(text: str) -> dict:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    if not t.startswith("{"):
        i, j = t.find("{"), t.rfind("}")
        if i == -1 or j == -1:
            raise PermanentError("Claude không trả về JSON object")
        t = t[i:j + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise RetryableError(f"JSON không hợp lệ từ Claude: {e}") from e


class AnthropicJSONClient:
    def __init__(self, api_key: str, model: str, max_tokens: int = 16000, client: Any = None):
        if not api_key:
            raise BlockedByCredentials("Thiếu ANTHROPIC_API_KEY")
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=600)
        self.model = model
        self.max_tokens = max_tokens

    def complete_json(self, system: str, user: str, max_tokens: int | None = None) -> tuple[dict, dict]:
        a = self._anthropic
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=max_tokens or self.max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except a.AuthenticationError as e:
            raise BlockedByCredentials("ANTHROPIC_API_KEY không hợp lệ") from e
        except a.PermissionDeniedError as e:
            raise BlockedByCredentials(f"Model {self.model} không được cấp quyền: {redact(e)}") from e
        except (a.APIConnectionError, a.RateLimitError, a.InternalServerError, a.APITimeoutError) as e:
            raise RetryableError(f"Anthropic tạm lỗi: {type(e).__name__}") from e
        except a.BadRequestError as e:
            raise PermanentError(f"Yêu cầu Anthropic không hợp lệ: {redact(e)}") from e
        except a.APIStatusError as e:
            if getattr(e, "status_code", 0) >= 500 or getattr(e, "status_code", 0) == 529:
                raise RetryableError(f"Anthropic {e.status_code}") from e
            raise PermanentError(f"Anthropic lỗi {getattr(e, 'status_code', '?')}") from e
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise RetryableError("Claude bị cắt do max_tokens; tăng ANTHROPIC_MAX_TOKENS")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens, "model": self.model}
        return parse_json_loose(text), usage


def estimate_usd(usage: dict) -> float:
    return round(usage.get("input_tokens", 0) / 1e6 * DEFAULT_PRICE_IN + usage.get("output_tokens", 0) / 1e6 * DEFAULT_PRICE_OUT, 5)
