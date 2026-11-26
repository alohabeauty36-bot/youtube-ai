"""Kiểm tra định kỳ: mọi video app đã upload phải đọc lại là 'private', trừ khi đã được CHỦ KÊNH tự công khai
(PUBLISHED_MANUALLY_OBSERVED) hoặc app tự công khai theo chế độ Tự động hoàn toàn (PUBLISHED_AUTO).
Chạy: PYTHONPATH=backend python3 scripts/check_privacy.py"""
import sys

from sqlalchemy import select

from ev_studio.db import SessionLocal
from ev_studio.models import Channel, Story, YouTubeUpload
from ev_studio.providers import Providers
from ev_studio.workers.jobs import make_youtube_client

bad = 0
with SessionLocal() as s:
    for up in s.scalars(select(YouTubeUpload).where(YouTubeUpload.youtube_video_id.is_not(None))):
        st = s.get(Story, up.story_id)
        p = make_youtube_client(s, s.get(Channel, up.channel_id), Providers()).get_privacy(up.youtube_video_id)
        ok = p == "private" or (p == "public" and st.state in ("PUBLISHED_MANUALLY_OBSERVED", "PUBLISHED_AUTO"))
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {up.youtube_video_id} privacy={p} state={st.state} requested={up.privacy_status_requested}")
sys.exit(1 if bad else 0)
