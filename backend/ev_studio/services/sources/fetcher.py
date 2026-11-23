"""Nguồn tin: adapter fixture (DEMO) và RSS hợp lệ (tôn trọng robots.txt, rate limit, không vượt paywall)."""
from __future__ import annotations

import json
import logging
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from ...config import get_settings
from ...errors import RetryableError

log = logging.getLogger(__name__)
USER_AGENT = "EVCreatorStudioBot/1.0 (+independent channel; respects robots.txt)"


@dataclass
class RawArticle:
    title: str
    url: str
    publisher: str | None
    published_at: datetime | None
    raw_excerpt: str
    trust_tier: int = 3
    event_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    model_names: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    supporting_sources: list[dict] = field(default_factory=list)
    claims: list[dict] | None = None  # fixture có sẵn claim; production → trích xuất bằng Claude
    is_demo: bool = False
    source_name: str | None = None


class SourceAdapter(Protocol):
    def fetch(self) -> list[RawArticle]: ...


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v)
    import re
    from datetime import timedelta

    m = re.fullmatch(r"now(?:([+-])(\d+)([hd]))?", s)
    if m:  # mốc tương đối cho fixture: now-6h, now+38d
        base = datetime.now(timezone.utc)
        if m.group(1):
            delta = timedelta(hours=int(m.group(2))) if m.group(3) == "h" else timedelta(days=int(m.group(2)))
            base = base + delta if m.group(1) == "+" else base - delta
        return base
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class FixtureSourceAdapter:
    """Đọc fixtures/news_demo.json — dữ liệu GIẢ LẬP gắn nhãn DEMO."""

    def __init__(self, path: Path | None = None):
        self.path = path or (get_settings().fixtures_dir / "news_demo.json")

    def fetch(self) -> list[RawArticle]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        out = []
        for a in data["articles"]:
            out.append(RawArticle(
                title=a["title"], url=a.get("url", ""), publisher=a.get("publisher"),
                published_at=_dt(a.get("published_at")), raw_excerpt=a.get("raw_excerpt", ""),
                trust_tier=a.get("trust_tier", 3), event_at=_dt(a.get("event_at")),
                effective_from=_dt(a.get("effective_from")), effective_to=_dt(a.get("effective_to")),
                model_names=a.get("model_names", []), entities=a.get("entities", []), flags=a.get("flags", []),
                supporting_sources=a.get("supporting_sources", []), claims=a.get("claims"), is_demo=True,
                source_name=a.get("publisher"),
            ))
        return out


class RobotsCache:
    def __init__(self) -> None:
        self._cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, client: httpx.Client, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._cache.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = client.get(base + "/robots.txt", timeout=10)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except httpx.HTTPError:
                rp.parse([])
            self._cache[base] = rp
        return rp.can_fetch(USER_AGENT, url)


_robots = RobotsCache()
_last_fetch: dict[str, float] = {}


class RSSSourceAdapter:
    """Lấy tin qua RSS/Atom. Chỉ dùng tiêu đề + tóm tắt do nguồn phát hành công khai trong feed."""

    def __init__(self, feed_url: str, publisher: str, trust_tier: int, rate_limit_seconds: int = 60,
                 client: httpx.Client | None = None):
        self.feed_url, self.publisher, self.trust_tier = feed_url, publisher, trust_tier
        self.rate_limit_seconds = rate_limit_seconds
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=20)

    def fetch(self) -> list[RawArticle]:
        import feedparser

        last = _last_fetch.get(self.feed_url, 0)
        if time.time() - last < self.rate_limit_seconds:
            log.info("Bỏ qua %s: chưa hết rate limit", self.feed_url)
            return []
        if not _robots.allowed(self.client, self.feed_url):
            log.warning("robots.txt không cho phép: %s", self.feed_url)
            return []
        try:
            r = self.client.get(self.feed_url)
        except httpx.HTTPError as e:
            raise RetryableError(f"Lỗi mạng khi đọc feed {self.feed_url}: {e}") from e
        _last_fetch[self.feed_url] = time.time()
        if r.status_code >= 500 or r.status_code == 429:
            raise RetryableError(f"Feed {self.feed_url} trả {r.status_code}")
        if r.status_code != 200:
            log.warning("Feed %s trả %s", self.feed_url, r.status_code)
            return []
        parsed = feedparser.parse(r.content)
        out = []
        for e in parsed.entries[:50]:
            link = e.get("link") or ""
            if not link:
                continue
            excerpt = e.get("summary") or e.get("description") or ""
            out.append(RawArticle(
                title=e.get("title", "").strip(), url=link, publisher=self.publisher,
                published_at=_dt(e.get("published") or e.get("updated")), raw_excerpt=_strip_html(excerpt)[:4000],
                trust_tier=self.trust_tier, source_name=self.publisher,
            ))
        return out


def _strip_html(s: str) -> str:
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").strip()
