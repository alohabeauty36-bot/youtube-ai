"""API không tồn tại phải trả JSON 404 ngắn gọn (không phải trang 404 HTML của Next.js); /api/health báo phiên bản mã."""
from fastapi.testclient import TestClient


def test_unknown_api_returns_json_404(db_ready):
    from ev_studio.api.main import app

    with TestClient(app) as c:
        for method in ("get", "post", "patch", "delete"):
            r = getattr(c, method)("/api/khong-ton-tai/123")
            assert r.status_code == 404
            assert r.headers["content-type"].startswith("application/json")
            assert "/api/khong-ton-tai/123" in r.json()["detail"]
        # route có thật vẫn hoạt động, không bị route bắt-tất-cả nuốt mất
        assert c.get("/api/essays").status_code == 200


def test_health_reports_code_build(db_ready):
    from ev_studio.api.main import app
    from ev_studio.build_info import code_fingerprint

    with TestClient(app) as c:
        assert c.get("/api/health").json()["build"] == code_fingerprint()
