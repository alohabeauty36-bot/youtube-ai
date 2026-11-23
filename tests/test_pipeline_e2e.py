"""E2E DEMO + các tình huống lỗi: mất mạng khi upload, timeout sau khi YouTube nhận video, sai kênh,
privacy readback sai, Telegram offline, thiếu giấy phép, claim chưa xác minh, duyệt không công khai, yêu cầu sửa."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from ev_studio.config import get_settings
from ev_studio.db import SessionLocal
from ev_studio.models import Asset, Channel, Claim, Job, Render, Scene, Story, TelegramCallbackToken, TelegramMessage, YouTubeUpload
from ev_studio.providers import Providers
from ev_studio.services.telegram.bot import DemoTelegramClient
from ev_studio.services.youtube.client import DemoYouTubeClient
from ev_studio.workers.jobs import apply_review, run_pipeline, upload_gate


def demo_dir():
    return get_settings().artifacts_dir / "demo_youtube"


def demo_videos():
    p = demo_dir() / "videos.json"
    return json.loads(p.read_text()) if p.exists() else {}


def reset_to_ready(story_id: str):
    """Đưa story về READY_TO_UPLOAD và xoá dấu vết upload để test tình huống tiếp theo."""
    with SessionLocal() as s:
        s.execute(delete(YouTubeUpload).where(YouTubeUpload.story_id == story_id))
        s.execute(delete(TelegramMessage).where(TelegramMessage.story_id == story_id))
        s.execute(delete(TelegramCallbackToken).where(TelegramCallbackToken.story_id == story_id))
        s.execute(delete(Job).where(Job.story_id == story_id, Job.job_type.in_(["UploadPrivateJob", "NotifyTelegramJob"])))
        st = s.get(Story, story_id)
        st.state, st.resume_state, st.blockers, st.last_error = "READY_TO_UPLOAD", None, [], None
        s.commit()
    shutil.rmtree(demo_dir(), ignore_errors=True)


def test_autopilot_end_to_end_demo(autopilot_story):
    with SessionLocal() as s:
        st = s.get(Story, autopilot_story["story_id"])
        assert st.state == "EDITOR_REVIEW", (st.state, st.blockers, st.last_error)
        assert st.mode == "AUTOPILOT_PRIVATE" and st.is_demo
        r = s.get(Render, st.current_render_id)
        assert r.qa_passed and r.width == 1920 and r.height == 1080
        assert 120 <= r.duration <= 200
        info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", r.path],
                                         capture_output=True, text=True).stdout)
        kinds = {x["codec_type"]: x for x in info["streams"]}
        assert kinds["video"]["codec_name"] == "h264" and kinds["audio"]["sample_rate"] == "48000"
        assert Path(r.subtitle_srt_path).read_text().strip() and len(r.thumbnail_paths) == 2
        up = s.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id))
        assert up.is_demo and up.youtube_video_id.startswith("DEMO-")
        assert up.privacy_status_requested == up.privacy_status_observed == "private"
        assert demo_videos()[up.youtube_video_id]["body"]["status"]["privacyStatus"] == "private"
        scenes = list(s.scalars(select(Scene).where(Scene.story_id == st.id, Scene.script_version_id == r.script_version_id)))
        assert all(sc.asset_ids or sc.payload.get("internal_graphic") for sc in scenes)
        assert any(sc.visual_type in ("fact", "formula", "timeline", "pros_cons") for sc in scenes)  # đồ họa tự xây
        assert all(4 - 1e-6 <= sc.end_seconds - sc.start_seconds <= 12 + 1e-6 for sc in scenes[:-1])
        rejected = list(s.scalars(select(Asset).where(Asset.story_id == st.id, Asset.review_status == "rejected")))
        assert len(rejected) == 2  # ảnh không giấy phép + CC BY-NC
        assert not any(a.local_path for a in rejected)  # không tải asset bị loại
        msg = s.scalar(select(TelegramMessage).where(TelegramMessage.story_id == st.id))
        assert msg.status == "sent" and "RIÊNG TƯ" in msg.text and "KHÔNG công khai" in msg.text
        jt = {j.job_type for j in s.scalars(select(Job).where(Job.story_id == st.id))}
        assert {"FactCheckJob", "ResearchJob", "ScriptJob", "TTSJob", "MediaJob", "StoryboardJob", "RenderJob", "SEOJob",
                "MediaQAJob", "UploadPrivateJob", "NotifyTelegramJob"} <= jt


def test_rerun_is_idempotent_no_duplicate_upload(autopilot_story):
    before = len(demo_videos())
    res = run_pipeline(autopilot_story["story_id"])
    assert res["state"] == "EDITOR_REVIEW" and len(demo_videos()) == before


def test_telegram_callbacks_allowlist_replay_and_approve_keeps_private(autopilot_story):
    from ev_studio.services.telegram.callbacks import handle_callback

    sid = autopilot_story["story_id"]
    with SessionLocal() as s:
        toks = {t.action: t.token for t in s.scalars(select(TelegramCallbackToken).where(TelegramCallbackToken.story_id == sid))}
        bad = handle_callback(s, {"id": "1", "data": f"rv|{toks['approve']}", "message": {"chat": {"id": 999}}})
        assert not bad["ok"] and "quyền" in bad["message"]
        ok = handle_callback(s, {"id": "2", "data": f"rv|{toks['approve']}", "message": {"chat": {"id": "DEMO_CHAT"}}})
        assert ok["ok"] and "VẪN RIÊNG TƯ" in ok["message"] and ok["state"] == "APPROVED_PRIVATE"
        replay = handle_callback(s, {"id": "3", "data": f"rv|{toks['approve']}", "message": {"chat": {"id": "DEMO_CHAT"}}})
        assert not replay["ok"] and "replay" in replay["message"]
        up = s.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == sid))
    assert DemoYouTubeClient().get_privacy(up.youtube_video_id) == "private"


def test_owner_manual_publish_is_only_observed(autopilot_story):
    from ev_studio.workers.jobs import refresh_status_job

    sid = autopilot_story["story_id"]
    with SessionLocal() as s:
        st = s.get(Story, sid)
        up = s.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == sid))
        yt = DemoYouTubeClient()
        assert refresh_status_job(s, st, Providers(youtube=yt))["observed"] == "private"
        assert st.state == "APPROVED_PRIVATE"
        yt.simulate_owner_published(up.youtube_video_id)  # chủ kênh tự công khai trên Studio
        refresh_status_job(s, st, Providers(youtube=yt))
        s.commit()
        assert st.state == "PUBLISHED_MANUALLY_OBSERVED"


@pytest.fixture()
def ready_story(autopilot_story):
    reset_to_ready(autopilot_story["story_id"])
    return autopilot_story["story_id"]


def test_network_drop_during_upload_resumes_without_duplicate(ready_story):
    yt = DemoYouTubeClient(fail_network_times=1)
    res = run_pipeline(ready_story, Providers(youtube=yt))
    assert res["state"] == "EDITOR_REVIEW"
    assert yt.insert_calls == 2 and len(demo_videos()) == 1


def test_timeout_after_youtube_received_reconciles_by_tag(ready_story):
    yt = DemoYouTubeClient(fail_after_insert_times=1)
    res = run_pipeline(ready_story, Providers(youtube=yt))
    assert res["state"] == "EDITOR_REVIEW"
    assert yt.insert_calls == 1 and len(demo_videos()) == 1  # lần retry tìm thấy video theo tag, không đăng trùng
    with SessionLocal() as s:
        assert s.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == ready_story)).youtube_video_id in demo_videos()


def test_wrong_channel_is_blocked(ready_story):
    yt = DemoYouTubeClient(channel_id="UC_SOMEONE_ELSE")
    res = run_pipeline(ready_story, Providers(youtube=yt))
    assert res["state"] == "FAILED_PERMANENT" and "kênh" in res["error"]
    assert yt.insert_calls == 0 and not demo_videos()


def test_privacy_readback_mismatch_forces_private(ready_story):
    yt = DemoYouTubeClient(report_privacy="public")
    res = run_pipeline(ready_story, Providers(youtube=yt))
    assert res["state"] == "EDITOR_REVIEW" and yt.force_private_calls == 1
    assert list(demo_videos().values())[0]["privacyStatus"] == "private"


def test_telegram_offline_keeps_private_and_warns(ready_story):
    res = run_pipeline(ready_story, Providers(telegram=DemoTelegramClient(offline=True)))
    assert res["state"] == "UPLOADED_PRIVATE"
    assert any("Telegram" in b for b in res["blockers"])
    assert list(demo_videos().values())[0]["privacyStatus"] == "private"
    res2 = run_pipeline(ready_story)  # bot hoạt động lại → gửi bù
    assert res2["state"] == "EDITOR_REVIEW"


def test_gates_block_unlicensed_asset_unverified_claim_and_manual_without_request(ready_story):
    with SessionLocal() as s:
        st = s.get(Story, ready_story)
        r = s.get(Render, st.current_render_id)
        scene = s.scalar(select(Scene).where(Scene.story_id == st.id, Scene.script_version_id == r.script_version_id,
                                             Scene.visual_type == "photo"))
        a = s.get(Asset, scene.asset_ids[0])
        a.review_status = "rejected"
        c = s.scalar(select(Claim).where(Claim.news_item_id == st.news_item_id, Claim.claim_key == "c1"))
        c.status = "unverified"
        st.mode = "MANUAL"
        reasons = upload_gate(s, st)
        assert any("license" in x for x in reasons)
        assert any("c1" in x for x in reasons)
        assert any("MANUAL" in x for x in reasons)
        s.rollback()
    res = run_pipeline(ready_story, Providers(youtube=DemoYouTubeClient()))
    assert res["state"] == "EDITOR_REVIEW"


def test_review_request_revision_creates_new_version_keeps_old_private(autopilot_story):
    sid = autopilot_story["story_id"]
    with SessionLocal() as s:
        st = s.get(Story, sid)
        if st.state != "EDITOR_REVIEW":
            pytest.skip(f"state {st.state}")
        out = apply_review(s, st, "request_revision", "owner", "dashboard", "Rút gọn phần mở đầu")
        s.commit()
        new = s.get(Story, out["new_story_id"])
        assert st.state == "REVISION_REQUESTED" and new.parent_story_id == sid and new.revision == 2
        assert new.mode == "MANUAL" and "Rút gọn" in new.editorial_notes
        assert list(demo_videos().values())[0]["privacyStatus"] == "private"
