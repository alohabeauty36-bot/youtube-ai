"""Quét tin ở chế độ production: nguồn RSS mặc định, một feed lỗi không chặn feed khác, báo rõ lý do từng nguồn."""
import httpx
import pytest
from sqlalchemy import delete, select

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Xe</title>
<item><title>VinFast mở bán xe điện mới tại Việt Nam</title><link>https://vnexpress.net/vinfast-xe-dien-moi-1.html</link>
<description><![CDATA[<p>VinFast công bố giá bán mẫu xe điện mới, áp dụng đến hết tháng 12.</p>]]></description>
<pubDate>Mon, 23 Nov 2026 08:00:00 +0700</pubDate></item>
<item><title>Giá xăng hôm nay</title><link>https://vnexpress.net/gia-xang-2.html</link>
<description>Giá xăng điều chỉnh nhẹ.</description><pubDate>Mon, 23 Nov 2026 09:00:00 +0700</pubDate></item>
</channel></rss>"""


def handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/robots.txt":
        return httpx.Response(404)
    host = req.url.host
    if host == "vnexpress.net":
        return httpx.Response(200, content=RSS.encode(), headers={"content-type": "application/rss+xml"})
    if host == "tuoitre.vn":
        return httpx.Response(404)
    if host == "thanhnien.vn":
        return httpx.Response(200, content=b"<html><body>Trang chu</body></html>", headers={"content-type": "text/html"})
    raise httpx.ConnectError("mất mạng", request=req)


@pytest.fixture()
def production(db_ready, monkeypatch):
    from ev_studio.config import get_settings
    from ev_studio.services import ingest
    from ev_studio.services.sources import fetcher

    prod = get_settings().model_copy(update={"app_mode": "production"})
    monkeypatch.setattr(ingest, "get_settings", lambda: prod)

    class MockRSS(fetcher.RSSSourceAdapter):
        def __init__(self, *a, **kw):
            kw["client"] = httpx.Client(transport=httpx.MockTransport(handler))
            super().__init__(*a, **kw)

    monkeypatch.setattr(ingest, "RSSSourceAdapter", MockRSS)
    monkeypatch.setattr(fetcher, "_robots", fetcher.RobotsCache())
    fetcher._last_fetch.clear()
    yield ingest
    fetcher._last_fetch.clear()
    from ev_studio.db import SessionLocal
    from ev_studio.models import AppSetting, NewsItem, Source

    with SessionLocal() as s:
        ids = [d["feed_url"] for d in ingest.DEFAULT_RSS_SOURCES] + ["https://example.net/feed.xml"]
        sids = list(s.scalars(select(Source.id).where(Source.feed_url.in_(ids))))
        s.execute(delete(NewsItem).where(NewsItem.source_id.in_(sids)))
        s.execute(delete(Source).where(Source.id.in_(sids)))
        s.execute(delete(AppSetting).where(AppSetting.key == ingest.DEFAULT_SOURCES_FLAG))
        s.commit()


def test_production_scan_seeds_sources_and_reports_each_feed(production):
    from ev_studio.db import SessionLocal
    from ev_studio.models import NewsItem, Source

    with SessionLocal() as s:
        st = production.run_scan(s, writer=None, actor="test")
        s.commit()
        assert any("nguồn mặc định" in w for w in st["warnings"])
        by = {r["source"]: r for r in st["sources"]}
        assert by["VnExpress — Xe"]["ok"] and by["VnExpress — Xe"]["fetched"] == 2
        assert "404" in by["Tuổi Trẻ — Xe"]["error"]
        assert "không phải RSS" in by["Thanh Niên — Xe"]["error"]
        assert "Lỗi mạng" in by["Dân trí — Ô tô xe máy"]["error"]
        assert st["created"] == 2
        src = s.scalar(select(Source).where(Source.name == "VnExpress — Xe"))
        items = list(s.scalars(select(NewsItem).where(NewsItem.source_id == src.id)))
        assert {i.title for i in items} >= {"VinFast mở bán xe điện mới tại Việt Nam"}
        assert src.last_fetched_at is not None
        assert s.scalar(select(Source).where(Source.name == "Tuổi Trẻ — Xe")).last_fetched_at is None

        # bấm quét lại ngay: không lỗi, báo rõ đang giãn cách
        st2 = production.run_scan(s, writer=None, actor="test")
        assert all(not r["ok"] for r in st2["sources"])
        assert any("đợi thêm" in (r.get("error") or "") for r in st2["sources"])
        assert any("Không nguồn nào" in w for w in st2["warnings"])


def test_blocked_default_sources_are_not_re_added(production):
    from ev_studio.db import SessionLocal
    from ev_studio.models import Source

    with SessionLocal() as s:
        assert production.ensure_default_sources(s) == 4
        for src in s.scalars(select(Source).where(Source.feed_url.is_not(None), Source.is_demo.is_(False))):
            src.blocked = True
        s.commit()
        assert production.ensure_default_sources(s) == 0
        st = production.run_scan(s, writer=None, actor="test")
        assert st["sources"] == [] and any("Không có nguồn RSS nào đang bật" in w for w in st["warnings"])


def test_manual_source_with_feed_is_scanned(production):
    """Nguồn thêm tay rồi mới điền feed (kind='manual') vẫn được quét."""
    from ev_studio.db import SessionLocal
    from ev_studio.models import Source

    with SessionLocal() as s:
        production.ensure_default_sources(s)
        for src in s.scalars(select(Source).where(Source.feed_url.is_not(None), Source.is_demo.is_(False))):
            src.blocked = True
        s.add(Source(name="Nguồn tay", domain="example.net", kind="manual", feed_url="https://example.net/feed.xml"))
        s.commit()
        names = [src.name for src, _ in production.adapters_for(s)]
        assert names == ["Nguồn tay"]


def test_scan_writer_without_claude_key_still_scans(monkeypatch):
    from ev_studio import providers
    from ev_studio.errors import BlockedByCredentials
    from ev_studio.services.ingest import scan_writer

    def boom():
        raise BlockedByCredentials("thiếu ANTHROPIC_API_KEY")

    monkeypatch.setattr(providers, "get_writer", boom)
    w, warn = scan_writer()
    assert w is None and "Claude" in warn
