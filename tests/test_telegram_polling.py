"""Telegram polling: mạng chập chờn không làm đầy Nhật ký lỗi; mất kết nối lâu hoặc sai cấu hình thì cảnh báo rõ ràng."""
import logging

import httpx
import pytest

from ev_studio.errors import PermanentError, RetryableError
from ev_studio.services.telegram import callbacks
from ev_studio.services.telegram.bot import RealTelegramClient


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING and "Telegram polling" in r.getMessage()]


def test_brief_network_blips_are_not_warnings(caplog):
    caplog.set_level(logging.INFO, logger="ev_studio.services.telegram.callbacks")
    clk = Clock()
    h = callbacks.PollHealth(clock=clk)
    for _ in range(5):  # 5 lần chập chờn rải rác như log 17:38–19:24
        assert h.failed(RetryableError("Telegram mất kết nối: ReadTimeout")) <= 60
        clk.t += 20
        h.ok()
        clk.t += 1200
    assert warnings(caplog) == []


def test_long_outage_warns_once_with_details(caplog):
    caplog.set_level(logging.INFO, logger="ev_studio.services.telegram.callbacks")
    clk = Clock()
    h = callbacks.PollHealth(clock=clk)
    waits = []
    for _ in range(20):
        waits.append(h.failed(RetryableError("Telegram mất kết nối: ConnectError")))
        clk.t += 40
    w = warnings(caplog)
    assert len(w) == 1 and "ConnectError" in w[0].getMessage() and "liên tục" in w[0].getMessage()
    assert waits[0] == 5 and max(waits) == 60  # backoff tăng dần, tối đa 60 giây
    h.ok()
    assert "kết nối lại" in caplog.records[-1].getMessage()


def test_config_error_warns_immediately(caplog):
    caplog.set_level(logging.INFO, logger="ev_studio.services.telegram.callbacks")
    h = callbacks.PollHealth(clock=Clock())
    h.failed(PermanentError("Telegram 409 Conflict: bot đang được một chương trình khác nhận tin"))
    h.failed(PermanentError("Telegram 409 Conflict: bot đang được một chương trình khác nhận tin"))
    w = warnings(caplog)
    assert len(w) == 1 and "409" in w[0].getMessage()


def test_long_poll_waits_longer_than_telegram_holds():
    seen = {}

    def handler(req: httpx.Request):
        seen["read"] = req.extensions["timeout"]["read"]
        return httpx.Response(200, json={"ok": True, "result": []})

    tg = RealTelegramClient("123:abc", client=httpx.Client(transport=httpx.MockTransport(handler), timeout=30))
    assert tg.get_updates(None) == []
    assert seen["read"] >= RealTelegramClient.POLL_SECONDS + 15


def test_409_conflict_explains_cause():
    def handler(req):
        return httpx.Response(409, json={"ok": False, "description": "Conflict: terminated by other getUpdates request"})

    tg = RealTelegramClient("123:abc", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PermanentError, match="chương trình khác"):
        tg.get_updates(None)


def test_analytics_api_disabled_gives_right_fix():
    from types import SimpleNamespace

    import httplib2
    from googleapiclient.errors import HttpError

    from ev_studio.errors import BlockedByCredentials
    from ev_studio.services.youtube.analytics import GoogleAnalyticsClient

    body = (b'{"error": {"code": 403, "message": "YouTube Analytics API has not been used in project 947709004118 before or '
            b'it is disabled.", "errors": [{"reason": "accessNotConfigured"}]}}')

    class R:
        def execute(self):
            raise HttpError(httplib2.Response({"status": 403}), body)

    ya = SimpleNamespace(reports=lambda: SimpleNamespace(query=lambda **kw: R()))
    c = GoogleAnalyticsClient(None, analytics=ya, data=SimpleNamespace())
    with pytest.raises(BlockedByCredentials, match="chưa bật YouTube Analytics API") as ei:
        from datetime import date
        c.daily(date(2026, 11, 1), date(2026, 11, 2))
    assert "Không cần kết nối lại" in str(ei.value)
