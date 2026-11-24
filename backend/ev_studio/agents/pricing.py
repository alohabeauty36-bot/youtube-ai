"""Bảng giá model (USD / 1 triệu token) và hàm tính tiền.

Nguồn: https://platform.claude.com/docs/en/about-claude/pricing (đọc ngày 24/11/2026).
Model không có trong bảng (kể cả model ChatGPT) PHẢI được khai giá trong cấu hình `model_prices_json`,
nếu không Ban Tài chính từ chối gọi: không biết giá thì không kiểm soát được chi phí.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..errors import GateBlocked


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_write_mult: float = 1.25  # ghi cache 5 phút
    cache_read_mult: float = 0.1
    # Haiku 5.5: prompt > 100K token tính giá cao hơn
    long_threshold: int | None = None
    long_input: float | None = None
    long_output: float | None = None


BUILTIN: dict[str, Price] = {
    "claude-fable-5-2": Price(8.0, 40.0, cache_read_mult=0.025),
    "claude-opus-5-5": Price(4.0, 20.0, cache_read_mult=0.05),
    "claude-sonnet-5-5": Price(2.0, 10.0, cache_read_mult=0.05),
    "claude-haiku-5-5": Price(0.10, 0.50, cache_read_mult=0.1, long_threshold=100_000, long_input=0.50, long_output=2.50),
}


class UnknownPrice(GateBlocked):
    def __init__(self, model: str):
        super().__init__(f"Chưa biết giá model '{model}'. Khai giá trong Thiết lập → Ngân sách (model_prices_json) "
                         f"trước khi dùng, ví dụ {{\"{model}\": [2.0, 8.0]}}")


def price_table(overrides_json: str = "") -> dict[str, Price]:
    table = dict(BUILTIN)
    if overrides_json and overrides_json.strip():
        try:
            data = json.loads(overrides_json)
        except json.JSONDecodeError as e:
            raise GateBlocked(f"model_prices_json không phải JSON hợp lệ: {e}") from e
        for name, v in data.items():
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                table[name] = Price(float(v[0]), float(v[1]))
            elif isinstance(v, dict):
                table[name] = Price(float(v["input"]), float(v["output"]), float(v.get("cache_write_mult", 1.25)),
                                    float(v.get("cache_read_mult", 0.1)))
    return table


def lookup(model: str, overrides_json: str = "") -> Price:
    table = price_table(overrides_json)
    if model in table:
        return table[model]
    # cho phép model có hậu tố ngày, ví dụ claude-sonnet-5-5-20261101
    for name in sorted(table, key=len, reverse=True):
        if model.startswith(name):
            return table[name]
    raise UnknownPrice(model)


def cost_usd(model: str, input_tokens: int = 0, output_tokens: int = 0, cache_write_tokens: int = 0,
             cache_read_tokens: int = 0, overrides_json: str = "") -> float:
    p = lookup(model, overrides_json)
    pin, pout = p.input, p.output
    prompt_total = input_tokens + cache_write_tokens + cache_read_tokens
    if p.long_threshold and prompt_total > p.long_threshold:
        pin, pout = p.long_input or pin, p.long_output or pout
    usd = (input_tokens * pin + cache_write_tokens * pin * p.cache_write_mult + cache_read_tokens * pin * p.cache_read_mult
           + output_tokens * pout) / 1e6
    return round(usd, 6)


def estimate_tokens(text: str) -> int:
    """Ước tính DƯ số token (tiếng Việt có dấu tốn token hơn tiếng Anh): 1 token ≈ 2 ký tự."""
    return max(1, len(text) // 2 + 16)
