import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TMP = Path(tempfile.mkdtemp(prefix="evcs-test-"))
os.environ.update({
    "APP_MODE": "demo",
    "DATABASE_URL": f"sqlite:///{TMP / 'test.db'}",
    "ARTIFACTS_DIR": str(TMP / "artifacts"),
    "MEDIA_LIBRARY_DIR": str(TMP / "media"),
    # thời lượng rút gọn để test nhanh; luồng 10 phút thật chạy bằng scripts/smoke_demo.sh
    "DURATION_TARGET_SECONDS": "160", "DURATION_MIN_SECONDS": "120", "DURATION_MAX_SECONDS": "200",
    "JOB_BACKOFF_BASE_SECONDS": "0",
    "SECRET_KEY": "test-secret-key-0123456789", "TELEGRAM_ALLOWED_CHAT_IDS": "",
})
for k in ("ADMIN_TOKEN", "EDITOR_TOKEN", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "PEXELS_API_KEY", "GOOGLE_CLIENT_SECRETS_FILE"):
    os.environ.pop(k, None)


@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def db_ready():
    from ev_studio.db import init_db

    init_db()
    return True


@pytest.fixture()
def db(db_ready):
    from ev_studio.db import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="session")
def seeded(db_ready):
    """Seed demo + quét fixture một lần."""
    from ev_studio.db import SessionLocal
    from ev_studio.demo import seed_demo
    from ev_studio.providers import get_writer
    from ev_studio.services.ingest import run_scan

    with SessionLocal() as s:
        seed_demo(s)
        stats = run_scan(s, writer=get_writer(), actor="test")
        s.commit()
    return stats


@pytest.fixture(scope="session")
def autopilot_story(seeded):
    """Chạy Autopilot Private toàn tuyến (DEMO) một lần: tin → script → TTS → asset → render → QA → upload DEMO → Telegram DEMO."""
    from ev_studio.db import SessionLocal
    from ev_studio.workers.jobs import autopilot_tick, set_setting

    with SessionLocal() as s:
        set_setting(s, "autopilot_enabled", True)
        s.commit()
        res = autopilot_tick(s)
    assert res.get("ran"), res
    return res
