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


PRODUCTION_SOURCES = [
    # Đầu mối gợi ý trong đặc tả. Feed URL cần chủ kênh kiểm tra (RSS chính thức nếu có) trước khi bật.
    {"name": "VinFast — Tin tức", "domain": "vinfastauto.com", "feed_url": None, "trust_tier": 1},
    {"name": "V-Green — Tin tức", "domain": "vgreen.net", "feed_url": None, "trust_tier": 1},
    {"name": "V-Green — Chính sách", "domain": "vgreen.net", "feed_url": None, "trust_tier": 1},
]
