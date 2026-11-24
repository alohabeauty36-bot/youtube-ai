"""Tìm kiếm và tải trang web hợp lệ, miễn phí.

Nhà cung cấp tìm kiếm (cấu hình trong Thiết lập → Phòng Bài luận):
- wikipedia : API chính thức của Wikipedia (vi + en), không cần key, nội dung CC BY-SA → luôn ghi nguồn.
- searxng   : máy tìm kiếm mã nguồn mở tự cài (ví dụ http://localhost:8888), không cần key, gom kết quả nhiều nơi.
- brave     : Brave Search API (gói miễn phí ~2.000 lượt/tháng), cần key.
- fixture   : dữ liệu GIẢ LẬP trong fixtures/research_demo.json (DEMO, test).

Trang web thường được tải với User-Agent riêng, tôn trọng robots.txt, giãn cách giữa hai lần gọi cùng một máy chủ,
giới hạn dung lượng, chỉ đọc phần chữ công khai (không vượt paywall), không gọi địa chỉ nội bộ (localhost, 10.x, 192.168.x…).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from ...config import Settings, get_settings
from ..sources.fetcher import USER_AGENT, RobotsCache

log = logging.getLogger(__name__)
MAX_PAGE_BYTES = 2_000_000
MAX_TEXT_CHARS = 40_000


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    publisher: str = ""
    provider: str = ""
    text: str | None = None  # có sẵn toàn văn (Wikipedia, fixture) → khỏi tải trang
    license: str | None = None
    meta: dict = field(default_factory=dict)


class Searcher(Protocol):
    name: str

    def search(self, query: str, k: int = 5) -> list[SearchHit]: ...


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


# ------------------------------------------------------------------ Wikipedia


class WikipediaSearch:
    """API MediaWiki công khai. Chính sách của Wikimedia yêu cầu User-Agent có thông tin liên hệ."""
    name = "wikipedia"

    def __init__(self, langs: list[str], user_agent: str, http: httpx.Client | None = None, max_chars: int = 30_000):
        self.langs = [x.strip() for x in langs if x.strip()] or ["vi"]
        self.max_chars, self.user_agent = max_chars, user_agent
        self.http = http or httpx.Client(timeout=20, follow_redirects=True)

    def _api(self, lang: str, **params) -> dict:
        r = self.http.get(f"https://{lang}.wikipedia.org/w/api.php", params={"format": "json", "formatversion": 2, **params},
                          headers={"User-Agent": self.user_agent})
        r.raise_for_status()
        return r.json()

    def search(self, query: str, k: int = 3) -> list[SearchHit]:
        out: list[SearchHit] = []
        for lang in self.langs:
            try:
                res = self._api(lang, action="query", list="search", srsearch=query, srlimit=k, srprop="snippet")
            except (httpx.HTTPError, ValueError) as e:
                log.warning("Wikipedia %s lỗi khi tìm '%s': %s", lang, query, type(e).__name__)
                continue
            for item in (res.get("query") or {}).get("search", [])[:k]:
                try:
                    page = self._api(lang, action="query", prop="extracts|info|pageprops", inprop="url", explaintext=1,
                                     exsectionformat="plain", pageids=item["pageid"])
                except (httpx.HTTPError, ValueError):
                    continue
                pages = (page.get("query") or {}).get("pages") or []
                if not pages:
                    continue
                p = pages[0]
                if "disambiguation" in (p.get("pageprops") or {}):
                    continue  # trang định hướng: không có nội dung
                text = (p.get("extract") or "").strip()
                if len(text) < 200:
                    continue
                out.append(SearchHit(title=p.get("title", item.get("title", "")), url=p.get("fullurl") or
                                     f"https://{lang}.wikipedia.org/?curid={item['pageid']}",
                                     snippet=_strip_tags(item.get("snippet", "")), publisher=f"Wikipedia ({lang})",
                                     provider=self.name, text=text[: self.max_chars], license="CC BY-SA 4.0"))
        return out


# ------------------------------------------------------------------ SearXNG / Brave


class SearxngSearch:
    """SearXNG tự cài. Cần bật định dạng json trong settings.yml của SearXNG (search.formats: [html, json])."""
    name = "searxng"

    def __init__(self, base_url: str, language: str = "vi", http: httpx.Client | None = None):
        self.base, self.language = base_url.rstrip("/"), language
        self.http = http or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20)

    def search(self, query: str, k: int = 6) -> list[SearchHit]:
        try:
            r = self.http.get(f"{self.base}/search", params={"q": query, "format": "json", "language": self.language})
            r.raise_for_status()
            items = r.json().get("results", [])
        except (httpx.HTTPError, ValueError) as e:
            log.warning("SearXNG lỗi khi tìm '%s': %s", query, type(e).__name__)
            return []
        return [SearchHit(title=i.get("title", ""), url=i["url"], snippet=i.get("content", ""), publisher=_domain(i["url"]),
                          provider=self.name) for i in items[:k] if i.get("url")]


class BraveSearch:
    name = "brave"

    def __init__(self, api_key: str, language: str = "vi", http: httpx.Client | None = None):
        self.language = language
        self.http = http or httpx.Client(timeout=20, headers={"X-Subscription-Token": api_key, "Accept": "application/json"})

    def search(self, query: str, k: int = 6) -> list[SearchHit]:
        try:
            r = self.http.get("https://api.search.brave.com/res/v1/web/search",
                              params={"q": query, "count": k, "search_lang": self.language})
            r.raise_for_status()
            items = (r.json().get("web") or {}).get("results", [])
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Brave Search lỗi khi tìm '%s': %s", query, type(e).__name__)
            return []
        return [SearchHit(title=_strip_tags(i.get("title", "")), url=i["url"], snippet=_strip_tags(i.get("description", "")),
                          publisher=_domain(i["url"]), provider=self.name) for i in items[:k] if i.get("url")]


# ------------------------------------------------------------------ DEMO


class FixtureSearch:
    """Đọc fixtures/research_demo.json — tư liệu GIẢ LẬP gắn nhãn DEMO, để chạy thử khi không có mạng."""
    name = "fixture"

    def __init__(self, path: Path | None = None):
        self.path = path or (get_settings().fixtures_dir / "research_demo.json")

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        from ...agents.memory import tokens

        pages = json.loads(self.path.read_text(encoding="utf-8"))["pages"]
        q = set(tokens(query))
        scored = []
        for p in pages:
            pt = set(tokens(p["title"] + " " + " ".join(p.get("keywords", [])) + " " + p["text"][:3000]))
            s = len(q & pt)
            if s:
                scored.append((s, p))
        scored.sort(key=lambda x: -x[0])
        return [SearchHit(title=p["title"], url=p["url"], publisher=p.get("publisher", ""), provider=self.name,
                          text=p["text"], snippet=p["text"][:200], meta={"demo": True}) for _, p in scored[:k]]


# ------------------------------------------------------------------ tải trang


def _is_public_host(host: str) -> bool:
    if not host or host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True  # tên miền thường
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)


class PageFetcher:
    def __init__(self, http: httpx.Client | None = None, min_interval: float = 1.5, timeout: float = 20):
        self.http = http or httpx.Client(headers={"User-Agent": USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
                                         timeout=timeout, follow_redirects=True)
        self.robots = RobotsCache()
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            gap = self.min_interval - (time.monotonic() - self._last.get(host, 0))
            if gap > 0:
                time.sleep(gap)
            self._last[host] = time.monotonic()

    def fetch(self, url: str) -> tuple[str, str] | None:
        """Trả (tiêu đề, văn bản) hoặc None nếu không được phép / không đọc được."""
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not _is_public_host(p.hostname or ""):
            return None
        try:
            if not self.robots.allowed(self.http, url):
                log.info("robots.txt không cho phép đọc %s", url)
                return None
            self._wait_turn(p.hostname or "")
            with self.http.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "text/plain" not in ctype:
                    return None
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_PAGE_BYTES:
                        break
                enc = r.encoding or "utf-8"
        except httpx.HTTPError as e:
            log.info("Không tải được %s: %s", url, type(e).__name__)
            return None
        raw = bytes(buf).decode(enc, errors="replace")
        if "text/plain" in ctype:
            return "", raw[:MAX_TEXT_CHARS]
        title, text = extract_text(raw)
        return (title, text) if len(text) >= 300 else None


# ------------------------------------------------------------------ trích văn bản từ HTML


_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button", "select", "iframe",
         "figure", "template"}
_BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "td", "th", "dd", "pre", "article", "section", "div", "br", "tr"}
_NOISE = re.compile(r"(đăng nhập|đăng ký|cookie|subscribe|sign in|bình luận|chia sẻ|quảng cáo|advertisement|©|bản quyền)", re.I)


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title = ""
        self.lines: list[str] = []
        self.cur: list[str] = []

    def _flush(self):
        t = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if t:
            self.lines.append(t)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        if tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in _SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.cur.append(data)


def extract_text(html: str) -> tuple[str, str]:
    """Lấy phần chữ chính của trang: bỏ menu, chân trang, script; giữ đoạn văn đủ dài."""
    ex = _Extractor()
    try:
        ex.feed(html)
        ex.close()
    except Exception:  # noqa: BLE001 - HTML hỏng thì lấy được bao nhiêu hay bấy nhiêu
        pass
    ex._flush()
    keep = []
    for line in ex.lines:
        words = len(line.split())
        if words < 8 or (words < 25 and _NOISE.search(line)):
            continue
        if keep and keep[-1] == line:
            continue
        keep.append(line)
    return re.sub(r"\s+", " ", ex.title).strip(), "\n".join(keep)[:MAX_TEXT_CHARS]


def _strip_tags(s: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


# ------------------------------------------------------------------ cấu hình


def build_searchers(settings: Settings | None = None, http: Any = None) -> list:
    s = settings or get_settings()
    choice = s.research_search_provider
    if choice == "fixture" or (choice == "auto" and s.is_demo and not s.searxng_url and not s.brave_api_key
                               and not s.research_online_in_demo):
        return [FixtureSearch()]
    out: list = []
    lang = (s.essay_language or "vi").split("-")[0]
    if choice in ("auto", "wikipedia"):
        out.append(WikipediaSearch(s.research_wiki_langs.split(","), s.wikimedia_user_agent, http=http))
    if choice in ("auto", "searxng") and s.searxng_url:
        out.append(SearxngSearch(s.searxng_url, lang, http=http))
    brave = s.brave_api_key.get_secret_value() if s.brave_api_key else ""
    if choice in ("auto", "brave") and brave:
        out.append(BraveSearch(brave, lang, http=http))
    return out
