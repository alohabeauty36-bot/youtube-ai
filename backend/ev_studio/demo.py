"""Seed dữ liệu DEMO: kênh demo, thư viện ảnh demo (có/không giấy phép), nguồn tin fixture."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Channel, Source
from .security import audit


def _draw_illustration(path: Path, hue: tuple[int, int, int], label: str) -> None:
    img = Image.new("RGB", (1920, 1080), (18, 28, 44))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 820, 1920, 1080], fill=(40, 46, 56))
    for i in range(4):  # trụ sạc minh họa
        x = 260 + i * 420
        d.rounded_rectangle([x, 380, x + 160, 820], 24, fill=hue)
        d.rectangle([x + 30, 430, x + 130, 520], fill=(220, 240, 255))
        d.line([(x + 160, 600), (x + 260, 700), (x + 240, 820)], fill=(30, 30, 30), width=14)
    d.rounded_rectangle([600, 640, 1320, 860], 80, fill=(200, 210, 225))  # xe chung chung, không thương hiệu
    d.ellipse([680, 800, 820, 940], fill=(20, 20, 20))
    d.ellipse([1100, 800, 1240, 940], fill=(20, 20, 20))
    d.text((60, 60), label, fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=85)


def seed_media_library() -> Path:
    root = get_settings().media_library_dir / "demo"
    ok = root / "demo_tram_sac_minh_hoa.jpg"
    if not ok.exists():
        _draw_illustration(ok, (46, 160, 113), "DEMO illustration")
    (root / "demo_tram_sac_minh_hoa.jpg.license.json").write_text(json.dumps({
        "license_name": "Owner-provided", "allowed_commercial": True, "attribution_required": False,
        "permission_proof": "DEMO: hình do hệ thống tự vẽ để kiểm thử, chủ kênh sở hữu toàn quyền",
        "creator": "EV Creator Studio (DEMO)", "scene_description": "Trạm sạc minh họa (đồ họa tự vẽ)",
        "tags": ["tram", "sac", "charging", "xe", "dien"], "generic": True, "is_illustration": True, "is_demo": True,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    _seed_demo_broll(root)
    nolic = root / "anh_tai_tu_web_khong_ro_quyen.jpg"
    if not nolic.exists():
        _draw_illustration(nolic, (180, 60, 60), "DEMO: no license")
    nc = root / "anh_cc_by_nc.jpg"
    if not nc.exists():
        _draw_illustration(nc, (60, 60, 180), "DEMO: CC BY-NC")
    (root / "anh_cc_by_nc.jpg.license.json").write_text(json.dumps({
        "license_name": "CC BY-NC 4.0", "source_url": "https://example.org/demo/anh-nc", "creator": "Tác giả DEMO",
        "attribution_text": "Tác giả DEMO — CC BY-NC 4.0", "generic": True, "is_demo": True,
        "scene_description": "Ảnh giấy phép phi thương mại (phải bị loại)",
    }, ensure_ascii=False), encoding="utf-8")
    return root


DEMO_BROLL = [("demo_broll_duong_pho.mp4", "0x0b3d91", "0x2ecc71", "Cảnh đường phố (DEMO, chuyển động màu tự tạo)"),
              ("demo_broll_tram_sac.mp4", "0x1f4a70", "0xf1c40f", "Cảnh trạm sạc (DEMO, chuyển động màu tự tạo)"),
              ("demo_broll_cao_toc.mp4", "0x3a1d5c", "0xe74c3c", "Cảnh cao tốc (DEMO, chuyển động màu tự tạo)")]


def _seed_demo_broll(root: Path) -> None:
    """Video nền DEMO do FFmpeg tự tạo (dải màu chuyển động) để chạy thử luồng dựng bằng video B-roll khi chưa có
    Pexels key. Không có FFmpeg thì bỏ qua."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        return
    for name, c0, c1, desc in DEMO_BROLL:
        f = root / name
        if not f.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"gradients=s=1280x720:c0={c0}:c1={c1}:x0=0:y0=0:x1=1280:y1=720:speed=0.02:duration=12:rate=30",
                            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(f)],
                           capture_output=True, timeout=120)
        if f.exists():
            (root / (name + ".license.json")).write_text(json.dumps({
                "license_name": "Owner-provided", "allowed_commercial": True, "attribution_required": False,
                "permission_proof": "DEMO: video do hệ thống tự tạo để kiểm thử, chủ kênh sở hữu toàn quyền",
                "creator": "EV Creator Studio (DEMO)", "scene_description": desc, "tags": ["xe", "dien", "electric", "car"],
                "generic": True, "is_illustration": True, "is_demo": True, "duration": 12,
            }, ensure_ascii=False, indent=1), encoding="utf-8")


def seed_demo(session: Session) -> Channel:
    ch = session.scalar(select(Channel).where(Channel.youtube_channel_id == "UC_DEMO_CHANNEL_000000"))
    if ch is None:
        ch = Channel(youtube_channel_id="UC_DEMO_CHANNEL_000000", title="[DEMO] Kênh thử nghiệm EV", is_demo=True,
                     selected=True, scopes=["demo"])
        session.add(ch)
    if not session.scalar(select(Source).where(Source.domain == "example.org")):
        session.add(Source(name="DEMO fixtures", domain="example.org", kind="fixture", trust_tier=1, is_demo=True))
    seed_media_library()
    audit(session, "system", "demo.seed", "channel", None)
    session.commit()
    return ch


# ------------------------------------------------------------------ xoá dữ liệu DEMO khi chuyển sang chạy thật
#
# Chọn "production" = chỉ còn dữ liệu thật. Xoá: kênh/nguồn DEMO, tin fixture + claim, mọi video DEMO (kể cả các bản
# viết lại) cùng toàn bộ thứ thuộc về chúng (ResearchPack, kịch bản, TTS, cảnh, tư liệu, render, SEO, upload DEMO, Telegram
# DEMO, job, sự kiện duyệt, lỗi gắn với video DEMO), bài luận DEMO, chi phí ước tính DEMO, bộ nhớ AI học từ dữ liệu DEMO,
# và file (artifacts/<video>, YouTube/Telegram giả lập, thư viện ảnh DEMO, cache giọng giả lập).
# GIỮ: sổ cái ngân sách (tiền thật đã tiêu, kể cả khi thử Claude lúc DEMO), nhật ký audit, cấu hình, hồ sơ năng lực phòng ban.

DEMO_TTS_MODEL = "demo-tone-v1"


def _chunks(ids: list, n: int = 500):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def _demo_story_ids(session: Session) -> tuple[list[str], list[str], list[str], list[str]]:
    """(story_ids, news_ids, source_ids, channel_ids). Video DEMO = gắn nhãn DEMO, hoặc dựng từ tin/kênh DEMO, hoặc là bản
    viết lại của một video DEMO (lặp đến khi không thêm được)."""
    from .models import NewsItem

    channel_ids = list(session.scalars(select(Channel.id).where(Channel.is_demo.is_(True))))
    source_ids = list(session.scalars(select(Source.id).where(Source.is_demo.is_(True))))
    news_q = select(NewsItem.id).where(NewsItem.is_demo.is_(True))
    if source_ids:
        news_q = select(NewsItem.id).where((NewsItem.is_demo.is_(True)) | (NewsItem.source_id.in_(source_ids)))
    news_ids = list(session.scalars(news_q))
    from .models import Story

    cond = Story.is_demo.is_(True)
    if news_ids:
        cond = cond | Story.news_item_id.in_(news_ids)
    if channel_ids:
        cond = cond | Story.channel_id.in_(channel_ids)
    ids = set(session.scalars(select(Story.id).where(cond)))
    while ids:
        more = {i for chunk in _chunks(list(ids)) for i in session.scalars(
            select(Story.id).where(Story.parent_story_id.in_(chunk), Story.id.notin_(ids)))}
        if not more:
            break
        ids |= more
    return sorted(ids), news_ids, source_ids, channel_ids


def demo_data_summary(session: Session) -> dict:
    """Đếm dữ liệu DEMO sẽ bị xoá (cho hộp xác nhận ở trang Thiết lập)."""
    from .models import CostUsage, Essay, Story, now
    from .workers.orchestrator import WORKER_ID

    story_ids, news_ids, source_ids, channel_ids = _demo_story_ids(session)
    t = now()
    running = 0
    for chunk in _chunks(story_ids):
        q = select(Story.id).where(Story.id.in_(chunk), Story.lease_owner.is_not(None), Story.lease_expires_at > t)
        if get_settings().job_executor == "inline":
            # chạy job ngay trong tiến trình này: lease của tiến trình cũ (đã tắt/sập) là lease mồ côi, không chặn
            q = q.where(Story.lease_owner == WORKER_ID)
        running += len(list(session.scalars(q)))
    out = {
        "stories": len(story_ids), "news": len(news_ids), "sources": len(source_ids), "channels": len(channel_ids),
        "essays": len(list(session.scalars(select(Essay.id).where(Essay.is_demo.is_(True))))),
        "cost_rows": len(list(session.scalars(select(CostUsage.id).where(CostUsage.is_demo.is_(True))))),
        "growth_rows": _count_growth_demo(session),
        "running_stories": running,
        "has_files": any(p.exists() for p in _demo_dirs()),
    }
    out["total"] = sum(v for k, v in out.items() if k not in ("running_stories", "has_files")) + int(out["has_files"])
    return out


def _count_growth_demo(session: Session) -> int:
    from sqlalchemy import func

    from .models import ChannelMetricDay, GrowthPlan, VideoMetric

    return sum(int(session.scalar(select(func.count()).select_from(m).where(m.is_demo.is_(True))) or 0)
               for m in (ChannelMetricDay, VideoMetric, GrowthPlan))


def _demo_dirs() -> list[Path]:
    s = get_settings()
    return [s.artifacts_dir / "demo_youtube", s.artifacts_dir / "demo_telegram", s.media_library_dir / "demo"]


class DemoPurgeBlocked(ValueError):
    """Còn video DEMO đang chạy pipeline: xoá lúc này sẽ làm job đó lỗi giữa chừng."""


def purge_demo_data(session: Session, actor: str, *, force: bool = False) -> dict:
    """Xoá mọi dữ liệu DEMO trong session (CHƯA commit). Trả về số dòng đã xoá + danh sách file/thư mục cần xoá sau khi
    commit thành công (gọi `remove_demo_files`). `force=False`: từ chối nếu còn video DEMO đang giữ lease (đang chạy)."""
    from sqlalchemy import delete, or_, update

    from .agents.memory import key_hash
    from .models import (
        AgentMemory, Asset, AssetLicense, Claim, CostUsage, ErrorLog, Essay, Job, NewsItem, Render, ResearchPack,
        ReviewEvent, Scene, ScriptVersion, SEOVariant, Story, TelegramCallbackToken, TelegramMessage, TTSSegment,
        YouTubeUpload,
    )
    from .services.tts.pipeline import cache_key

    summary = demo_data_summary(session)
    if summary["running_stories"] and not force:
        raise DemoPurgeBlocked(
            f"Đang có {summary['running_stories']} video DEMO chạy dở. Đợi chạy xong (thường vài phút) rồi chọn lại chế độ production.")
    story_ids, news_ids, source_ids, channel_ids = _demo_story_ids(session)
    s = get_settings()
    counts: dict[str, int] = {}
    files: list[Path] = []

    def rm(model, *conds, key: str | None = None) -> None:
        n = session.execute(delete(model).where(*conds).execution_options(synchronize_session=False)).rowcount or 0
        k = key or model.__tablename__
        counts[k] = counts.get(k, 0) + n

    for ch in _chunks(story_ids):
        sv_ids = list(session.scalars(select(ScriptVersion.id).where(ScriptVersion.story_id.in_(ch))))
        for sv in _chunks(sv_ids):
            for text, voice, mv in session.execute(select(TTSSegment.tts_text, TTSSegment.voice, TTSSegment.model_version)
                                                   .where(TTSSegment.script_version_id.in_(sv))):
                if mv == DEMO_TTS_MODEL:
                    files.append(s.artifacts_dir / "_tts_cache" / f"{cache_key(text, voice, mv)}.wav")
            rm(TTSSegment, TTSSegment.script_version_id.in_(sv))
            rm(Scene, Scene.script_version_id.in_(sv))
        rm(Scene, Scene.story_id.in_(ch))
        asset_ids = list(session.scalars(select(Asset.id).where(Asset.story_id.in_(ch))))
        for a in _chunks(asset_ids):
            rm(AssetLicense, AssetLicense.asset_id.in_(a))
            rm(Asset, Asset.id.in_(a))
        rm(YouTubeUpload, YouTubeUpload.story_id.in_(ch))
        rm(Render, Render.story_id.in_(ch))
        for model in (SEOVariant, ResearchPack, ReviewEvent, TelegramMessage, TelegramCallbackToken, Job):
            rm(model, model.story_id.in_(ch))
        rm(ScriptVersion, ScriptVersion.story_id.in_(ch))
        rm(CostUsage, CostUsage.story_id.in_(ch))
        rm(ErrorLog, ErrorLog.story_id.in_(ch))
        rm(AgentMemory, AgentMemory.story_id.in_(ch))
        files += [s.artifacts_dir / sid for sid in ch]
    # bản viết lại trỏ tới bản gốc: gỡ liên kết trước rồi xoá cùng lúc
    for ch in _chunks(story_ids):
        session.execute(update(Story).where(Story.id.in_(ch)).values(parent_story_id=None)
                        .execution_options(synchronize_session=False))
    for ch in _chunks(story_ids):
        rm(Story, Story.id.in_(ch))
    # tư liệu DEMO không gắn video (thư viện ảnh DEMO)
    demo_assets = list(session.scalars(select(Asset.id).where(Asset.is_demo.is_(True))))
    for a in _chunks(demo_assets):
        rm(AssetLicense, AssetLicense.asset_id.in_(a))
        rm(Asset, Asset.id.in_(a))
    for ch in _chunks(channel_ids):
        rm(YouTubeUpload, YouTubeUpload.channel_id.in_(ch))
    rm(YouTubeUpload, YouTubeUpload.is_demo.is_(True))
    for ch in _chunks(news_ids):
        rm(Claim, Claim.news_item_id.in_(ch))
        rm(NewsItem, NewsItem.id.in_(ch), key="news_items")
    for ch in _chunks(source_ids):
        rm(Source, Source.id.in_(ch))
    for ch in _chunks(channel_ids):
        rm(Channel, Channel.id.in_(ch))
    # bài luận DEMO + bộ nhớ AI sinh ra từ chúng (câu trả lời của thầy trên tư liệu giả lập, bản chủ kênh sửa)
    essays = list(session.execute(select(Essay.id, Essay.memory_id).where(Essay.is_demo.is_(True))))
    mem_ids = [m for _, m in essays if m]
    owner_keys = [key_hash("owner-essay", eid) for eid, _ in essays]
    for ch in _chunks(mem_ids):
        rm(AgentMemory, AgentMemory.id.in_(ch))
    for ch in _chunks(owner_keys):
        rm(AgentMemory, AgentMemory.department == "bai_luan", AgentMemory.key_hash.in_(ch))
    for ch in _chunks([e for e, _ in essays]):
        rm(Essay, Essay.id.in_(ch))
    # câu trả lời AI cho tin fixture (vd. tách claim bằng Claude thật khi đang DEMO): đề bài mang nhãn [DEMO]
    rm(AgentMemory, AgentMemory.kind == "answer", or_(AgentMemory.request.like("%[DEMO]%"), AgentMemory.source == "demo"))
    rm(CostUsage, CostUsage.is_demo.is_(True))
    # Ban Tăng trưởng: số liệu Analytics giả lập + kế hoạch dựng trên số liệu đó (kèm kế hoạch đang áp dụng)
    from .agents.growth import S_GUIDANCE, S_TARGET, S_WEIGHTS
    from .models import ChannelMetricDay, GrowthPlan, VideoMetric

    had_demo_plan = session.scalar(select(GrowthPlan.id).where(GrowthPlan.is_demo.is_(True)).limit(1)) is not None
    rm(ChannelMetricDay, ChannelMetricDay.is_demo.is_(True))
    rm(VideoMetric, VideoMetric.is_demo.is_(True))
    rm(GrowthPlan, GrowthPlan.is_demo.is_(True))
    if had_demo_plan:
        from .models import AppSetting as _AS

        rm(_AS, _AS.key.in_([S_TARGET, S_WEIGHTS, S_GUIDANCE]), key="growth_settings")
    # Autopilot thường được bật trong lúc chạy thử DEMO → tắt để chủ kênh tự bật lại có chủ đích ở chế độ thật
    from .models import AppSetting

    ap = session.get(AppSetting, "autopilot_enabled")
    autopilot_reset = bool(ap and ap.value)
    if autopilot_reset:
        ap.value = False
    files += _demo_dirs()
    counts = {k: v for k, v in counts.items() if v}
    audit(session, actor, "demo.purge", "system", None, **{k: v for k, v in counts.items()}, autopilot_reset=autopilot_reset)
    return {"deleted": counts, "total": sum(counts.values()), "autopilot_reset": autopilot_reset,
            "files": [str(p) for p in files]}


def remove_demo_files(paths: list[str]) -> int:
    """Xoá file/thư mục DEMO sau khi DB đã commit. Chỉ xoá trong artifacts/ và media_library/; lỗi từng file → bỏ qua."""
    import shutil

    s = get_settings()
    roots = [s.artifacts_dir.resolve(), s.media_library_dir.resolve()]
    n = 0
    for raw in paths:
        p = Path(raw)
        try:
            rp = p.resolve()
            if not any(rp != r and rp.is_relative_to(r) for r in roots) or not p.exists():
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            n += 1
        except OSError:
            continue
    return n


def purge_demo_if_production(actor: str = "system") -> dict | None:
    """Lúc khởi động: APP_MODE=production (vd. đặt trong .env) mà DB còn dữ liệu DEMO → xoá. Không làm sập app nếu lỗi."""
    import logging

    from .db import SessionLocal

    if get_settings().is_demo:
        return None
    log = logging.getLogger(__name__)
    try:
        with SessionLocal() as db:
            if not demo_data_summary(db)["total"]:
                return None
            res = purge_demo_data(db, actor, force=True)  # lúc khởi động không có job nào đang chạy
            db.commit()
        res["files_removed"] = remove_demo_files(res.pop("files"))
        log.info("Chế độ production: đã xoá %s dòng dữ liệu DEMO", res["total"])
        return res
    except Exception as ex:  # noqa: BLE001
        log.warning("Không xoá được dữ liệu DEMO lúc khởi động: %s", ex, extra={"evcs_source": "config"})
        return None

# Nguồn RSS mặc định cho production: xem services/ingest.py (DEFAULT_RSS_SOURCES).
