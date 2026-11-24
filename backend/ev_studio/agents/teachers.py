"""Thầy (Claude, ChatGPT — tốn tiền) và trò (model chạy trên máy qua Ollama — miễn phí).

Mọi lớp có cùng giao diện: `complete_json(system, user, max_tokens) -> (dict, usage)`.
usage = {input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, model, provider}.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..errors import BlockedByCredentials, PermanentError, RetryableError
from ..security import redact
from ..services.llm import AnthropicJSONClient, parse_json_loose

log = logging.getLogger(__name__)


class AnthropicTeacher:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str, client: Any = None):
        self.model = model
        self._c = AnthropicJSONClient(api_key, model, 16000, client=client, cache_system=True)

    def complete_json(self, system: str, user: str, max_tokens: int) -> tuple[dict, dict]:
        return self._c.complete_json(system, user, max_tokens)


class OpenAITeacher:
    """ChatGPT qua Chat Completions API (JSON mode). Giá model phải được khai trong cấu hình."""
    provider = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", http: Any = None):
        if not api_key:
            raise BlockedByCredentials("Thiếu OPENAI_API_KEY")
        if not model:
            raise BlockedByCredentials("Chưa chọn model ChatGPT (openai_model)")
        self.model, self.base = model, base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=600, headers={"Authorization": f"Bearer {api_key}"})

    def complete_json(self, system: str, user: str, max_tokens: int) -> tuple[dict, dict]:
        body = {"model": self.model, "max_completion_tokens": max_tokens, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system + "\n\nChỉ trả về MỘT JSON object."},
                             {"role": "user", "content": user}]}
        try:
            r = self.http.post(f"{self.base}/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise RetryableError(f"OpenAI tạm lỗi mạng: {type(e).__name__}") from e
        if r.status_code in (401, 403):
            raise BlockedByCredentials(f"OpenAI từ chối key/model ({r.status_code})")
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"OpenAI {r.status_code}")
        if r.status_code >= 400:
            raise PermanentError(f"OpenAI lỗi {r.status_code}: {redact(r.text[:300])}")
        data = r.json()
        ch = data["choices"][0]
        if ch.get("finish_reason") == "length":
            raise RetryableError("ChatGPT bị cắt do max_tokens")
        u = data.get("usage", {})
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        usage = {"input_tokens": int(u.get("prompt_tokens", 0)) - cached, "output_tokens": int(u.get("completion_tokens", 0)),
                 "cache_write_tokens": 0, "cache_read_tokens": cached, "model": self.model, "provider": "openai"}
        return parse_json_loose(ch["message"]["content"] or ""), usage


class LocalStudent:
    """Model mã nguồn mở chạy trên máy của chủ kênh qua Ollama (http://localhost:11434). Không tốn tiền API."""
    provider = "local"
    _avail: dict[str, tuple[float, bool]] = {}

    def __init__(self, url: str, model: str, timeout: float = 1800, http: Any = None):
        self.url, self.model = url.rstrip("/"), model
        self.http = http or httpx.Client(timeout=timeout)

    def available(self) -> bool:
        if not self.url:
            return False
        key = f"{self.url}|{self.model}"
        hit = self._avail.get(key)
        if hit and time.monotonic() - hit[0] < 60:
            return hit[1]
        ok = False
        try:
            r = self.http.get(f"{self.url}/api/tags", timeout=5)
            names = {m.get("name") for m in r.json().get("models", [])} if r.status_code == 200 else set()
            ok = self.model in names or any(n and n.split(":")[0] == self.model.split(":")[0] for n in names)
        except Exception:  # noqa: BLE001 - Ollama tắt thì coi như chưa có trò
            ok = False
        self._avail[key] = (time.monotonic(), ok)
        return ok

    def complete_json(self, system: str, user: str, max_tokens: int) -> tuple[dict, dict]:
        body = {"model": self.model, "stream": False, "format": "json",
                "options": {"num_predict": max_tokens, "num_ctx": 16384, "temperature": 0.5},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = self.http.post(f"{self.url}/api/chat", json=body)
        except httpx.HTTPError as e:
            raise RetryableError(f"Model trên máy không phản hồi: {type(e).__name__}") from e
        if r.status_code != 200:
            raise RetryableError(f"Ollama {r.status_code}: {r.text[:200]}")
        data = r.json()
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0,
                 "model": self.model, "provider": "local",
                 "local_prompt_tokens": data.get("prompt_eval_count", 0), "local_output_tokens": data.get("eval_count", 0)}
        return parse_json_loose(data.get("message", {}).get("content", "")), usage
