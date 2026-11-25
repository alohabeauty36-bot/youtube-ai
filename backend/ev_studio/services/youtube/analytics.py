"""YouTube Analytics API v2 (CHỈ ĐỌC) cho Ban Tăng trưởng + bản DEMO giả lập có nhãn.

Báo cáo dùng (theo tài liệu Channel Reports):
- theo ngày: dimensions=day, metrics views, estimatedMinutesWatched, averageViewDuration, subscribersGained/Lost
  và (cần scope yt-analytics-monetary.readonly + kênh trong YPP) estimatedRevenue, estimatedAdRevenue, playbackBasedCpm.
- top video: dimensions=video, sort=-views, maxResults ≤ 200.
Doanh thu bị từ chối (403: chưa bật kiếm tiền / thiếu quyền) → trả None, không làm hỏng phần còn lại.
"""
from __future__ import annotations

import logging
import math
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from ...errors import BlockedByCredentials, PermanentError, RetryableError
from ...security import redact

log = logging.getLogger(__name__)
BASE = ["views", "estimatedMinutesWatched", "averageViewDuration", "subscribersGained", "subscribersLost"]
MONEY = ["estimatedRevenue", "estimatedAdRevenue", "playbackBasedCpm"]
VIDEO_BASE = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "subscribersGained"]
VIDEO_MONEY = ["estimatedRevenue", "estimatedAdRevenue"]


class AnalyticsClient(Protocol):
    is_demo: bool

    def channel_stats(self) -> dict: ...
    def daily(self, start: date, end: date) -> tuple[list[dict], str | None]: ...
    def top_videos(self, start: date, end: date, limit: int = 200) -> tuple[list[dict], str | None]: ...
    def total(self, start: date, end: date) -> dict: ...
    def video_details(self, ids: list[str]) -> dict[str, dict]: ...


def rows_to_dicts(resp: dict) -> list[dict]:
    cols = [h["name"] for h in resp.get("columnHeaders", [])]
    return [dict(zip(cols, r)) for r in resp.get("rows", []) or []]


class GoogleAnalyticsClient:
    is_demo = False

    def __init__(self, credentials: Any, on_refresh: Callable[[str], None] | None = None, analytics: Any = None,
                 data: Any = None, max_retries: int = 5):
        from google.auth.transport.requests import Request

        if analytics is None or data is None:
            from googleapiclient.discovery import build

            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                except Exception as e:
                    raise BlockedByCredentials(f"Không làm mới được OAuth token, cần kết nối lại kênh: {redact(e)}") from e
                if on_refresh:
                    on_refresh(credentials.to_json())
            analytics = analytics or build("youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False)
            data = data or build("youtube", "v3", credentials=credentials, cache_discovery=False)
        self.ya, self.yt, self.max_retries = analytics, data, max_retries

    def _exec(self, req, allow_forbidden: bool = False):
        from googleapiclient.errors import HttpError

        for attempt in range(self.max_retries):
            try:
                return req.execute()
            except HttpError as e:
                st = e.resp.status
                if st in (500, 502, 503, 504) and attempt + 1 < self.max_retries:
                    time.sleep(min(30, 2 ** attempt + random.random()))
                    continue
                if st in (401, 403):
                    if allow_forbidden:
                        return None
                    raise BlockedByCredentials(
                        f"YouTube Analytics từ chối quyền ({st}). Bấm 'Kết nối kênh YouTube' lại để cấp quyền đọc Analytics: "
                        f"{redact(e)}") from e
                raise PermanentError(f"YouTube Analytics {st}: {redact(e)}") from e
            except (OSError, ConnectionError) as e:
                if attempt + 1 < self.max_retries:
                    time.sleep(min(30, 2 ** attempt + random.random()))
                    continue
                raise RetryableError(f"Mạng lỗi khi gọi YouTube Analytics: {type(e).__name__}") from e
        raise RetryableError("YouTube Analytics không phản hồi")

    def _query(self, start: date, end: date, metrics: list[str], allow_forbidden: bool = False, **kw):
        req = self.ya.reports().query(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                                      metrics=",".join(metrics), currency="USD", **kw)
        return self._exec(req, allow_forbidden=allow_forbidden)

    def channel_stats(self) -> dict:
        r = self._exec(self.yt.channels().list(part="snippet,statistics", mine=True))
        it = (r.get("items") or [{}])[0]
        st = it.get("statistics", {})
        return {"id": it.get("id"), "title": it.get("snippet", {}).get("title"),
                "subscribers": int(st.get("subscriberCount", 0) or 0), "videos": int(st.get("videoCount", 0) or 0),
                "views": int(st.get("viewCount", 0) or 0), "hidden_subscribers": bool(st.get("hiddenSubscriberCount"))}

    def daily(self, start: date, end: date) -> tuple[list[dict], str | None]:
        base = {r["day"]: r for r in rows_to_dicts(self._query(start, end, BASE, dimensions="day", sort="day"))}
        money_err = None
        money = self._query(start, end, MONEY, allow_forbidden=True, dimensions="day", sort="day")
        if money is None:
            money_err = "Chưa đọc được doanh thu: kênh chưa bật kiếm tiền (YPP) hoặc chưa cấp quyền doanh thu."
        else:
            for r in rows_to_dicts(money):
                base.setdefault(r["day"], {"day": r["day"]}).update(r)
        return [base[k] for k in sorted(base)], money_err

    def top_videos(self, start: date, end: date, limit: int = 200) -> tuple[list[dict], str | None]:
        kw = {"dimensions": "video", "sort": "-views", "maxResults": min(200, limit)}
        r = self._query(start, end, VIDEO_BASE + VIDEO_MONEY, allow_forbidden=True, **kw)
        if r is not None:
            return rows_to_dicts(r), None
        return rows_to_dicts(self._query(start, end, VIDEO_BASE, **kw)), "Chưa đọc được doanh thu theo video."

    def total(self, start: date, end: date) -> dict:
        rows = rows_to_dicts(self._query(start, end, ["views", "estimatedMinutesWatched"]))
        return rows[0] if rows else {"views": 0, "estimatedMinutesWatched": 0}

    def video_details(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 50):
            r = self._exec(self.yt.videos().list(part="snippet,status,contentDetails", id=",".join(ids[i:i + 50])))
            for it in r.get("items", []):
                out[it["id"]] = {"title": it["snippet"].get("title"), "published_at": it["snippet"].get("publishedAt"),
                                 "privacy": it.get("status", {}).get("privacyStatus"),
                                 "duration": it.get("contentDetails", {}).get("duration")}
        return out


class DemoAnalyticsClient:
    """Số liệu GIẢ LẬP (nhãn DEMO) để thử Ban Tăng trưởng khi chưa kết nối kênh thật. Không phải dữ liệu YouTube."""
    is_demo = True

    def __init__(self, channel_id: str = "UC_DEMO", video_ids: list[str] | None = None, seed: int = 7,
                 monetized: bool = True):
        self.channel_id, self.video_ids, self.seed, self.monetized = channel_id, video_ids or [], seed, monetized

    def _views(self, d: date) -> int:
        rng = random.Random(self.seed * 100003 + d.toordinal())
        trend = 900 + 250 * math.sin(d.toordinal() / 45)
        weekly = 1.0 + 0.15 * math.sin(d.toordinal() / 7 * 2 * math.pi)
        return int(trend * weekly * rng.uniform(0.85, 1.15))

    def channel_stats(self) -> dict:
        return {"id": self.channel_id, "title": "Kênh DEMO", "subscribers": 2350, "videos": 48 + len(self.video_ids),
                "views": 410000, "hidden_subscribers": False}

    def daily(self, start: date, end: date) -> tuple[list[dict], str | None]:
        out, d = [], start
        while d <= end:
            v = self._views(d)
            row = {"day": d.isoformat(), "views": v, "estimatedMinutesWatched": round(v * 4.6, 1),
                   "averageViewDuration": 276, "subscribersGained": v // 90, "subscribersLost": v // 600}
            if self.monetized:
                rpm = 0.55 + 0.1 * math.sin(d.toordinal() / 9)
                row.update(estimatedRevenue=round(v * rpm / 1000 * 1.08, 4), estimatedAdRevenue=round(v * rpm / 1000, 4),
                           playbackBasedCpm=round(rpm * 2.4, 3))
            out.append(row)
            d += timedelta(days=1)
        return out, None if self.monetized else "DEMO: kênh giả lập chưa bật kiếm tiền."

    def top_videos(self, start: date, end: date, limit: int = 200) -> tuple[list[dict], str | None]:
        ids = self.video_ids + [f"DEMO-old-{i:02d}" for i in range(12)]
        rows = []
        for i, vid in enumerate(ids[:limit]):
            rng = random.Random(f"{self.seed}-{vid}")
            v = int(rng.uniform(400, 6000) * (1.4 if i < len(self.video_ids) else 1.0))
            r = {"video": vid, "views": v, "estimatedMinutesWatched": round(v * rng.uniform(3.5, 6.0), 1),
                 "averageViewDuration": int(rng.uniform(200, 380)), "averageViewPercentage": round(rng.uniform(28, 55), 1),
                 "subscribersGained": v // 80}
            if self.monetized:
                r.update(estimatedRevenue=round(v * 0.62 / 1000, 4), estimatedAdRevenue=round(v * 0.57 / 1000, 4))
            rows.append(r)
        rows.sort(key=lambda r: -r["views"])
        return rows, None if self.monetized else "DEMO: chưa bật kiếm tiền."

    def total(self, start: date, end: date) -> dict:
        days = (end - start).days + 1
        v = sum(self._views(start + timedelta(days=i)) for i in range(0, days, 7)) * 7
        return {"views": v, "estimatedMinutesWatched": round(v * 4.6, 1)}

    def video_details(self, ids: list[str]) -> dict[str, dict]:
        base = datetime.now(timezone.utc) - timedelta(days=20)
        return {i: {"title": f"[DEMO] Video {i[-4:]}", "published_at": (base + timedelta(days=n % 20)).isoformat(),
                    "privacy": "public", "duration": "PT11M"} for n, i in enumerate(ids)}
