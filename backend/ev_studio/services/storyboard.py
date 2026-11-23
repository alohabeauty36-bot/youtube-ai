"""Storyboard tự động từ audio timeline: cảnh 4–12 giây, mỗi cảnh có asset_id hoặc đồ họa nội bộ."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .ranker import strip_accents

MIN_S, TARGET_S, MAX_S = 4.0, 7.5, 12.0


@dataclass
class SceneSpec:
    idx: int
    start: float
    end: float
    segment_keys: list[str]
    text: str
    visual_type: str = "narration"
    asset_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


def group_segments(segments: list[dict], total: float) -> list[SceneSpec]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for s in segments:
        dur_cur = (cur[-1]["end"] - cur[0]["start"]) if cur else 0
        seg_d = s["end"] - s["start"]
        new_par = bool(cur) and s["paragraph_idx"] != cur[-1]["paragraph_idx"]
        if cur and (dur_cur + seg_d > MAX_S or (dur_cur >= TARGET_S) or (new_par and dur_cur >= MIN_S)):
            groups.append(cur)
            cur = []
        cur.append(s)
    if cur:
        groups.append(cur)
    scenes: list[SceneSpec] = []
    for g in groups:
        scenes.append(SceneSpec(0, g[0]["start"], g[-1]["end"], [x["segment_key"] for x in g], " ".join(x["text"] for x in g)))
    # phủ kín timeline: start cảnh sau = end cảnh trước (bao cả khoảng nghỉ)
    for i, sc in enumerate(scenes):
        sc.start = 0.0 if i == 0 else scenes[i - 1].end
        sc.end = scenes[i + 1].start if i + 1 < len(scenes) else total
    # tách cảnh > 12s (segment dài) thành nhiều cảnh ≤ 10s
    out: list[SceneSpec] = []
    for sc in scenes:
        d = sc.end - sc.start
        if d > MAX_S:
            n = math.ceil(d / 10.0)
            step = d / n
            for k in range(n):
                part = SceneSpec(0, sc.start + k * step, sc.start + (k + 1) * step, sc.segment_keys, sc.text)
                part.payload["part"] = f"{k + 1}/{n}"
                out.append(part)
        else:
            out.append(sc)
    # gộp cảnh < 4s vào cảnh trước nếu không vượt 12s
    merged: list[SceneSpec] = []
    for sc in out:
        if merged and (sc.end - sc.start) < MIN_S and (sc.end - merged[-1].start) <= MAX_S:
            merged[-1].end = sc.end
            merged[-1].segment_keys += [k for k in sc.segment_keys if k not in merged[-1].segment_keys]
            merged[-1].text += " " + sc.text
        else:
            merged.append(sc)
    for i, sc in enumerate(merged):
        sc.idx = i
    return merged


def chapter_title(text: str, max_words: int = 7) -> str:
    first = re.split(r"(?<=[.!?…:])\s+", text.strip())[0]
    w = first.split()[:max_words]
    return " ".join(w).rstrip(" ,;:.") + ("…" if len(first.split()) > max_words else "")


def key_sentence(text: str, max_words: int = 26) -> str:
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    best = max(sents, key=lambda s: (len(s.split()) <= max_words, -abs(len(s.split()) - 16))) if sents else text
    w = best.split()
    return " ".join(w[:max_words]) + ("…" if len(w) > max_words else "")


def assign_visuals(scenes: list[SceneSpec], *, claim_map: list[dict], claims: dict[str, dict], title: str,
                   photo_assets: list[dict], has_timeline: bool, pillar: str,
                   paragraph_photos: dict[int, list[dict]] | None = None,
                   scene_paragraph: dict[int, int] | None = None) -> list[SceneSpec]:
    """Gán kiểu cảnh. Mỗi cảnh: asset_id (ảnh hợp quyền / ảnh AI minh họa) hoặc đồ họa nội bộ.

    `paragraph_photos`: ảnh đã tìm riêng cho từng đoạn lời đọc → được đặt vào cảnh của CHÍNH đoạn đó (mỗi ảnh một lần),
    ưu tiên hơn ảnh dùng chung `photo_assets`. Đồ họa dữ kiện/công thức/timeline vẫn được ưu tiên trước ảnh."""
    paragraph_photos = paragraph_photos or {}
    scene_paragraph = scene_paragraph or {}
    para_used: set[str] = set()
    excerpt_to_claim = [(strip_accents(e["relevant_script_excerpt"])[:80], e["claim_id"]) for e in claim_map]
    used_claims: set[str] = set()
    photo_i = 0
    photo_uses: dict[str, int] = {}
    last_photo_idx = -99
    max_uses = 3
    used_types = {"formula": False, "timeline": False, "pros_cons": False}
    last_type = None
    for sc in scenes:
        t = strip_accents(sc.text)
        claim_here = next((cid for ex, cid in excerpt_to_claim if ex and ex[:50] in t and cid not in used_claims), None)
        if sc.idx == 0:
            sc.visual_type, sc.payload["title"] = "title", title
        elif claim_here and claim_here in claims:
            sc.visual_type = "fact"
            sc.claim_ids = [claim_here]
            used_claims.add(claim_here)
        elif "phep tinh vi du" in t and not used_types["formula"]:
            sc.visual_type = "formula"
            used_types["formula"] = True
        elif has_timeline and not used_types["timeline"] and ("ba moc" in t or "thoi han" in t or "ngay ket thuc" in t):
            sc.visual_type = "timeline"
            used_types["timeline"] = True
        elif not used_types["pros_cons"] and ("phe ung ho" in t or "kich ban" in t):
            sc.visual_type = "pros_cons"
            used_types["pros_cons"] = True
        elif sc.text.strip().endswith("?") and last_type != "question":
            sc.visual_type = "question"
        elif (pp := next((x for x in paragraph_photos.get(scene_paragraph.get(sc.idx, -1), []) if x["id"] not in para_used), None)):
            para_used.add(pp["id"])
            last_photo_idx = sc.idx
            sc.visual_type = "photo"
            sc.asset_ids = [pp["id"]]
        elif (photo_assets and sc.idx - last_photo_idx >= max(2, 8 // len(photo_assets))
              and any(photo_uses.get(x["id"], 0) < max_uses for x in photo_assets)):
            # luân phiên ảnh, mỗi ảnh tối đa 3 lần, giãn cách để tránh lặp khung hình
            a = min(photo_assets, key=lambda x: photo_uses.get(x["id"], 0))
            photo_uses[a["id"]] = photo_uses.get(a["id"], 0) + 1
            last_photo_idx = sc.idx
            sc.visual_type = "photo"
            sc.asset_ids = [a["id"]]
        else:
            sc.visual_type = "narration"
        last_type = sc.visual_type
        sc.payload.setdefault("caption", key_sentence(sc.text))
    if len(scenes) > 2:
        scenes[-1].visual_type = "credits"
    return scenes
