"""CLI: `python -m ev_studio.cli <lệnh>`.

  init-db            tạo bảng (demo/test; production dùng alembic upgrade head)
  demo               chạy TOÀN TUYẾN DEMO: seed → quét tin → Autopilot Private → upload DEMO → Telegram DEMO
  scan               quét tin một lần
  autopilot-tick     chạy một vòng Autopilot (nếu đã bật)
  run <story_id>     chạy tiếp pipeline của một story
  verify <story_id>  kiểm tra integrity + privacy readback (ffprobe, sha256, YouTube)
"""
from __future__ import annotations

import json
import sys
import time

from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, init_db
from .security import setup_logging


def _p(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_demo() -> int:
    from .demo import seed_demo
    from .models import NewsItem, Render, Story, YouTubeUpload
    from .providers import get_writer
    from .services.ingest import run_scan
    from .workers.jobs import autopilot_tick, set_setting

    s = get_settings()
    if not s.is_demo:
        print("Lệnh demo chỉ chạy khi APP_MODE=demo")
        return 2
    init_db()
    t0 = time.time()
    with SessionLocal() as db:
        seed_demo(db)
        stats = run_scan(db, writer=get_writer(), actor="cli-demo")
        db.commit()
        print("== Quét tin (DEMO):")
        _p(stats)
        for n in db.scalars(select(NewsItem).order_by(NewsItem.score.desc())):
            print(f"  [{n.status:9}] {n.score:5.1f} {n.pillar or '--'} {n.title[:70]}")
        set_setting(db, "autopilot_enabled", True)
        db.commit()
        print("== Autopilot Private (DEMO) …")
        res = autopilot_tick(db)
        _p(res)
        if res.get("story_id"):
            st = db.get(Story, res["story_id"])
            db.refresh(st)
            r = db.get(Render, st.current_render_id) if st.current_render_id else None
            up = db.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id))
            print("== Kết quả:")
            _p({"state": st.state, "blockers": st.blockers, "render": r.path if r else None,
                "qa": r.qa.get("summary") if r else None, "duration": r.duration if r else None,
                "youtube_video_id": up.youtube_video_id if up else None,
                "privacy_readback": up.privacy_status_observed if up else None, "is_demo": True,
                "elapsed_seconds": round(time.time() - t0, 1)})
            return 0 if st.state == "EDITOR_REVIEW" else 1
    return 1


def cmd_verify(story_id: str) -> int:
    from pathlib import Path

    from .models import Channel, Render, Story, YouTubeUpload
    from .providers import Providers
    from .services.media.license_gate import file_sha256
    from .services.media_qa import probe
    from .workers.jobs import make_youtube_client

    with SessionLocal() as db:
        st = db.get(Story, story_id)
        r = db.get(Render, st.current_render_id)
        info = probe(Path(r.path))
        sha = file_sha256(Path(r.path))
        up = db.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id))
        privacy = None
        if up and up.youtube_video_id:
            privacy = make_youtube_client(db, db.get(Channel, up.channel_id), Providers()).get_privacy(up.youtube_video_id)
        ok = sha == r.sha256 and (privacy in (None, "private"))
        _p({"sha256_match": sha == r.sha256, "duration": info.get("format", {}).get("duration"),
            "streams": [(x["codec_type"], x["codec_name"]) for x in info.get("streams", [])],
            "youtube_video_id": up.youtube_video_id if up else None, "privacy_readback": privacy, "ok": ok})
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "init-db":
        init_db()
        print("OK")
        return 0
    if cmd == "demo":
        return cmd_demo()
    if cmd == "scan":
        from .providers import get_writer
        from .services.ingest import run_scan

        init_db()
        with SessionLocal() as db:
            _p(run_scan(db, writer=get_writer()))
            db.commit()
        return 0
    if cmd == "autopilot-tick":
        from .workers.jobs import autopilot_tick

        with SessionLocal() as db:
            _p(autopilot_tick(db))
        return 0
    if cmd == "run" and len(argv) > 1:
        from .workers.jobs import run_pipeline

        _p(run_pipeline(argv[1]))
        return 0
    if cmd == "verify" and len(argv) > 1:
        return cmd_verify(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
