"""Video không bao giờ được dưới 10 phút: sàn cứng ở cấu hình, trang Thiết lập, prompt và Media QA."""
from pathlib import Path

import pytest

from ev_studio.config import MIN_VIDEO_SECONDS, Settings, reset_settings_cache


def test_defaults_are_at_least_10_minutes():
    s = Settings(_env_file=None, allow_short_duration_for_tests=False)
    assert MIN_VIDEO_SECONDS == 600
    assert s.duration_min_seconds == 600
    assert s.duration_min_seconds < s.duration_target_seconds < s.duration_max_seconds
    assert s.duration_target_seconds >= 630  # có biên an toàn trên 10 phút


def test_old_env_values_are_raised_to_floor():
    # .env cũ: 570–630, mục tiêu 600 → nâng lên, không làm sập app
    s = Settings(_env_file=None, allow_short_duration_for_tests=False,
                 duration_min_seconds=570, duration_target_seconds=600, duration_max_seconds=630)
    assert s.duration_min_seconds == 600
    assert s.duration_target_seconds == 660
    assert s.duration_max_seconds == 720


def test_longer_values_are_kept():
    s = Settings(_env_file=None, allow_short_duration_for_tests=False,
                 duration_min_seconds=720, duration_target_seconds=840, duration_max_seconds=960)
    assert (s.duration_min_seconds, s.duration_target_seconds, s.duration_max_seconds) == (720, 840, 960)


@pytest.fixture()
def no_test_shortcut(monkeypatch, db_ready):
    monkeypatch.delenv("EVCS_ALLOW_SHORT_DURATION", raising=False)
    reset_settings_cache(base_too=True)
    yield
    monkeypatch.undo()
    reset_settings_cache(base_too=True)


@pytest.mark.parametrize("key,value", [("duration_min_seconds", 540), ("duration_target_seconds", 590)])
def test_settings_page_rejects_under_10_minutes(no_test_shortcut, key, value):
    from ev_studio.db import SessionLocal
    from ev_studio.runtime_config import save

    with SessionLocal() as s, pytest.raises(ValueError, match="10 phút"):
        save(s, {key: value}, [], "t")


def test_media_qa_blocks_video_under_10_minutes(tmp_path):
    from ev_studio.services.media_qa import run_media_qa

    res = run_media_qa(tmp_path / "khong-co.mp4", exact_10min=False, dur_min=0, dur_max=1e9, assets=[],
                       on_screen_facts_ok=True, description="", sources=[], thumbnail=None, subtitle=None)
    chk = next(c for c in res["checks"] if c["name"] == "Không dưới 10 phút")
    assert chk["ok"] is False and res["passed"] is False


def test_prompt_tells_writer_the_minimum():
    from ev_studio.services.claude_writer import load_prompts, render_developer_message

    ctx = {"pillar": "P1", "research_pack": {}, "target_duration_seconds": 660, "min_duration_seconds": 600,
           "max_duration_seconds": 720}
    msg = render_developer_message(load_prompts(), ctx)
    assert "TARGET_DURATION_SECONDS: 660" in msg
    assert "MIN_DURATION_SECONDS: 600" in msg and "MAX_DURATION_SECONDS: 720" in msg
    assert "không được dưới 10 phút" in load_prompts().system
