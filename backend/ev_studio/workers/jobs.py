"""12 worker jobs + pipeline đầu-cuối. Mỗi job: idempotent, có checkpoint, cập nhật state machine."""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import MIN_VIDEO_SECONDS, get_settings
from ..error_log import was_logged
from ..errors import BlockedByCredentials, ChannelMismatch, GateBlocked, PermanentError, PrivacyViolation, RetryableError
from ..models import (
    AppSetting, Asset, AssetLicense, Channel, Claim, CostUsage, NewsItem, Render, ResearchPack, ReviewEvent, Scene,
    ScriptVersion, SEOVariant, Story, TelegramCallbackToken, TelegramMessage, TTSSegment, YouTubeUpload, now,
)
from ..providers import Providers, youtube_mode
from ..security import audit, random_token, redact, sign_link
from ..services import fact_checker as fc
from ..services import media_qa, renderer, seo, storyboard, subtitles, thumbnail
from ..services.claude_writer import load_prompts
from ..services.llm import estimate_usd
from ..services.media import graphics
from ..services.media.finders import download
from ..services.media.license_gate import evaluate, file_sha256
from ..services.script_qa import run_script_qa
from ..services.telegram.bot import build_buttons, build_review_message
from ..services.tts.base import chunk_script
from ..services.tts.pipeline import synthesize
from ..services.youtube.privacy import PRIVATE, build_insert_body
from ..state_machine import HAS_PRIVATE_UPLOAD, StoryState as S
from .orchestrator import acquire_lease, fail_story, input_hash, release_lease, run_job, transition

log = logging.getLogger(__name__)


class DurationOutOfRange(GateBlocked):
    auto_handled = True  # pipeline tự viết lại kịch bản → không ghi vào Nhật ký lỗi

    def __init__(self, seconds: float):
        self.seconds = seconds
        super().__init__(f"Thời lượng {seconds:.1f}s ngoài khoảng mục tiêu")


# ------------------------------------------------------------------ helpers

def story_dir(story: Story) -> Path:
    d = get_settings().artifacts_dir / story.id
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_setting(session: Session, key: str, default=None):
    row = session.get(AppSetting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value) -> None:
    row = session.get(AppSetting, key)
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=key, value=value))


def record_cost(session: Session, story: Story | None, job_type: str, usage: dict, is_demo: bool) -> None:
    if not usage or not (usage.get("input_tokens") or usage.get("output_tokens")):
        return
    usd = estimate_usd(usage)
    sid = story.id if story else None
    svc = usage.get("provider") or "anthropic"
    session.add(CostUsage(story_id=sid, job_type=job_type, service=svc, units=usage.get("input_tokens", 0),
                          unit_type="input_tokens", estimated_usd=usd, is_demo=is_demo))
    session.add(CostUsage(story_id=sid, job_type=job_type, service=svc, units=usage.get("output_tokens", 0),
                          unit_type="output_tokens", estimated_usd=0, is_demo=is_demo))


def story_claims(session: Session, story: Story) -> list[Claim]:
    return list(session.scalars(select(Claim).where(Claim.news_item_id == story.news_item_id, Claim.status != "removed")))


def story_sources(news: NewsItem) -> list[dict]:
    out = []
    for i, s in enumerate(news.supporting_sources or []):
        out.append({"source_id": s["source_id"], "url": s["url"], "publisher": s.get("publisher"),  # không gửi full text
                    "published_at": s.get("published_at"), "trust_tier": s.get("trust_tier"),
                    "type": "primary" if s.get("trust_tier") == 1 else "secondary"})
    return out


def current_script(session: Session, story: Story) -> ScriptVersion:
    sv = session.get(ScriptVersion, story.current_script_version_id) if story.current_script_version_id else None
    if sv is None:
        raise PermanentError("Story chưa có kịch bản")
    return sv


def latest_pack(session: Session, story: Story) -> ResearchPack:
    rp = session.scalar(select(ResearchPack).where(ResearchPack.story_id == story.id).order_by(ResearchPack.version.desc()))
    if rp is None:
        raise PermanentError("Chưa có ResearchPack")
    return rp


def claim_dicts(claims: list[Claim]) -> list[dict]:
    return [{"claim_key": c.claim_key, "text": c.text, "status": c.status, "source_ids": c.source_ids,
             "claim_type": c.claim_type, "value": c.value, "unit": c.unit, "date_scope": c.date_scope,
             "is_key": c.is_key, "subject": c.subject} for c in claims]


# ------------------------------------------------------------------ 1–2. tin & fact-check

def fact_check_job(session: Session, story: Story, p: Providers) -> dict:
    """Lớp xác minh lại theo story (sau khi biên tập viên sửa/loại claim) + freshness."""
    claims = story_claims(session, story)
    news = story.news_item
    refs = {s["source_id"]: fc.SourceRef(s["source_id"], s["url"], s.get("publisher") or "", int(s.get("trust_tier") or 3),
                                         s.get("published_at"), s.get("text") or (news.raw_excerpt if s["source_id"] == "s1" else None))
            for s in (news.supporting_sources or [])}
    rechecked = fc.check_claims([{**d, "source_url": c.source_url, "evidence_excerpt": c.evidence_excerpt,
                                  "status": "inference" if c.status == "inference" else d["status"]}
                                 for c, d in zip(claims, claim_dicts(claims))], refs)
    for c, r in zip(claims, rechecked):
        if c.status in ("contested",) and "Mâu thuẫn với bài khác" in (c.verification_reason or ""):
            continue  # giữ trạng thái tranh cãi liên bài cho tới khi biên tập viên xử lý
        if c.editor_note and c.status == "verified" and c.verification_reason and c.verification_reason.startswith("Biên tập viên"):
            continue  # đã được biên tập viên xác minh thủ công với nguồn
        c.status, c.verification_reason = r["status"], r["verification_reason"]
    blockers = fc.blocking_claims(claims)
    if not any(c.status == "verified" and c.is_key for c in claims):
        blockers.append("Không có claim trọng yếu nào đã xác minh")
    if blockers:
        raise GateBlocked(blockers)
    probs = fc.freshness_check(claims, is_demo=story.is_demo)
    if probs:
        raise GateBlocked(probs)
    story.freshness_checked_at = now()
    return {"verified": sum(c.status == "verified" for c in claims), "claims": len(claims)}


# ------------------------------------------------------------------ 3. research

def research_job(session: Session, story: Story, p: Providers) -> dict:
    news = story.news_item
    claims = story_claims(session, story)
    usable = [d for d in claim_dicts(claims) if d["status"] in ("verified", "inference")]
    avoid = [d for d in claim_dicts(claims) if d["status"] in ("unverified", "contested")]
    sources = story_sources(news)
    recent = [t for (t,) in session.execute(select(Story.topic).where(Story.id != story.id).order_by(Story.created_at.desc()).limit(10))]
    ctx = {"news": {"title": news.title, "url": news.url, "publisher": news.publisher,
                    "published_at": news.published_at.isoformat() if news.published_at else None,
                    "effective_from": news.effective_from.isoformat() if news.effective_from else None,
                    "effective_to": news.effective_to.isoformat() if news.effective_to else None},
           "sources": sources, "claims": usable, "recent_topics": recent, "pillar": story.pillar, "story_id": story.id}
    w = p.get_writer()
    llm, usage = w.research_pack(ctx)
    record_cost(session, story, "ResearchJob", usage, w.is_demo)
    known = {s["source_id"] for s in sources}
    unknown = set(llm.get("source_ids", [])) - known
    if unknown:
        raise RetryableError(f"ResearchPack nhắc source_id không tồn tại: {sorted(unknown)}")
    if llm.get("blocking_issues"):
        raise GateBlocked([f"ResearchPack: {b}" for b in llm["blocking_issues"]])
    version = (session.scalar(select(func.max(ResearchPack.version)).where(ResearchPack.story_id == story.id)) or 0) + 1
    content = {**llm, "topic": story.topic, "pillar": story.pillar, "news": ctx["news"], "sources": sources,
               "claims": usable, "claims_to_avoid": avoid}
    if not story.core_question:
        story.core_question = llm.get("core_question") or story.topic
    rp = ResearchPack(story_id=story.id, version=version, content=content, model=getattr(w, "model", w.name), is_demo=w.is_demo)
    session.add(rp)
    session.flush()
    return {"research_pack_id": rp.id, "version": version}


# ------------------------------------------------------------------ 4. script

def _special_requests(session: Session, story: Story) -> str:
    """Ghi chú biên tập của chủ kênh + định hướng nội dung của Ban Tăng trưởng (định hướng, không phải dữ kiện)."""
    from ..agents.growth import content_guidance

    parts = [story.editorial_notes or ""]
    g = content_guidance(session)
    if g:
        parts.append(f"Định hướng kênh từ Ban Tăng trưởng (chỉ là định hướng biên tập, không phải dữ kiện): {g}")
    return "\n".join(p for p in parts if p).strip()


def script_job(session: Session, story: Story, p: Providers, actual_audio_seconds: float | None = None) -> dict:
    s = get_settings()
    rp = latest_pack(session, story)
    prev = session.get(ScriptVersion, story.current_script_version_id) if story.current_script_version_id else None
    recent_scripts = [t for (t,) in session.execute(
        select(ScriptVersion.spoken_script).where(ScriptVersion.story_id != story.id).order_by(ScriptVersion.created_at.desc()).limit(10))]
    recent_topics = [{"title": t} for (t,) in session.execute(
        select(Story.topic).where(Story.id != story.id).order_by(Story.created_at.desc()).limit(10))]
    ctx = {"pillar": story.pillar, "research_pack": rp.content, "claims_to_avoid": rp.content.get("claims_to_avoid", []),
           "recent_topics": recent_topics, "core_question": story.core_question or story.topic,
           "special_requests": _special_requests(session, story), "target_duration_seconds": s.duration_target_seconds,
           "min_duration_seconds": s.duration_min_seconds, "max_duration_seconds": s.duration_max_seconds,
           "actual_audio_seconds": actual_audio_seconds,
           "previous_spoken_script": prev.spoken_script if (prev and actual_audio_seconds) else None, "story_id": story.id}
    w = p.get_writer()
    out, usage = w.write_script(ctx)
    record_cost(session, story, "ScriptJob", usage, w.is_demo)
    by = w.name if usage.get("source") in (None, "anthropic", "openai") else f"{w.name}:{usage['source']}"
    try:
        return save_script_version(session, story, out, created_by=by, model=usage.get("model") or getattr(w, "model", w.name),
                                   is_demo=w.is_demo, change_note=f"Viết lại theo ACTUAL_AUDIO_SECONDS={actual_audio_seconds}" if actual_audio_seconds else "Bản đầu")
    except GateBlocked as e:
        if not w.is_demo:
            from ..agents import learning

            learning.on_script_qa_failed(session, story.id, e.reasons)
            session.commit()
        raise


def save_script_version(session: Session, story: Story, out: dict, *, created_by: str, model: str | None, is_demo: bool,
                        change_note: str) -> dict:
    rp = latest_pack(session, story)
    news = story.news_item
    pack_claims = rp.content["claims"] + rp.content.get("claims_to_avoid", [])
    source_texts = [news.raw_excerpt] + [x.get("text") or "" for x in (news.supporting_sources or [])]
    supported = " ".join(c["text"] for c in rp.content["claims"] if c["status"] == "verified") + " " + news.raw_excerpt
    recent_scripts = [t for (t,) in session.execute(
        select(ScriptVersion.spoken_script).where(ScriptVersion.story_id != story.id).order_by(ScriptVersion.created_at.desc()).limit(10))]
    qa = run_script_qa(out, pack_claims=pack_claims, source_texts=source_texts, recent_scripts=recent_scripts,
                       supported_phrases_text=supported, is_demo=is_demo)
    version = (session.scalar(select(func.max(ScriptVersion.version)).where(ScriptVersion.story_id == story.id)) or 0) + 1
    sv = ScriptVersion(story_id=story.id, version=version, parent_version_id=story.current_script_version_id, content=out,
                       spoken_script=out["spoken_script"], word_count=len(out["spoken_script"].split()), qa=qa.as_dict(),
                       qa_passed=qa.passed, created_by=created_by, change_note=change_note, prompt_hash=load_prompts().sha256,
                       model=model, is_demo=is_demo)
    session.add(sv)
    session.flush()
    story.current_script_version_id = sv.id
    session.commit()  # giữ bản lỗi QA để biên tập viên xem/sửa
    if not qa.passed:
        raise GateBlocked([f"Script QA: {c['name']} — {c['detail']}" for c in qa.checks if c["blocking"] and not c["ok"]])
    return {"script_version_id": sv.id, "version": version, "words": sv.word_count}


# ------------------------------------------------------------------ 5. TTS

def tts_job(session: Session, story: Story, p: Providers) -> dict:
    s = get_settings()
    sv = current_script(session, story)
    tts = p.get_tts()
    hints = {h["term"]: h["suggested_reading"] for h in sv.content.get("production_notes", {}).get("pronunciation_hints", [])}
    chunks = chunk_script(sv.spoken_script, extra_rules=hints)  # CHỈ spoken_script được đọc
    voice = get_setting(session, "voice", s.vieneu_voice)
    d = story_dir(story) / f"audio_v{sv.version}"
    m = synthesize(chunks, tts, voice, d, s.artifacts_dir / "_tts_cache")
    for old in session.scalars(select(TTSSegment).where(TTSSegment.script_version_id == sv.id)):
        session.delete(old)
    for seg in m.segments:
        session.add(TTSSegment(script_version_id=sv.id, idx=seg["idx"], segment_key=seg["segment_key"],
                               paragraph_idx=seg["paragraph_idx"], text=seg["text"], tts_text=seg["tts_text"],
                               text_hash=seg["text_hash"], voice=voice, model_version=tts.model_version, path=seg["path"],
                               duration=seg["duration"], start=seg["start"], end=seg["end"], qc=seg["qc"], cache_hit=seg["cache_hit"]))
    sv.actual_audio_seconds = m.duration
    bad = [x["segment_key"] for x in m.segments if not x["qc"]["ok"]]
    out = {"audio_path": str(m.path), "duration": m.duration, "segments": len(m.segments), "voice": voice,
           "cache_hits": sum(x["cache_hit"] for x in m.segments), "qc_issues": bad, "is_demo": tts.is_demo}
    if bad and not tts.is_demo:
        raise GateBlocked([f"TTS QC lỗi ở {', '.join(bad[:10])} — nghe lại/sửa đoạn"])
    if not (s.duration_min_seconds <= m.duration <= s.duration_max_seconds):
        session.commit()  # lưu duration đo thật để hiển thị
        raise DurationOutOfRange(m.duration)
    if not tts.is_demo:
        from ..agents import learning

        learning.on_audio_measured(session, sv.word_count, m.duration)  # Phòng Biên kịch học tốc độ đọc thật
    return out


# ------------------------------------------------------------------ 6. media

def media_job(session: Session, story: Story, p: Providers) -> dict:
    news = story.news_item
    models = news.model_names or []
    q = " ".join(models + ["xe điện", "electric car", "trạm sạc", "charging"])
    adir = story_dir(story) / "assets"
    approved, rejected = 0, 0
    for finder in p.get_finders():
        try:
            found = finder.search(q)
        except RetryableError:
            raise
        for fa in found:
            exists = session.scalar(select(Asset).where(Asset.story_id == story.id, Asset.source_url == fa.source_url,
                                                        Asset.local_path == fa.local_path))
            if exists:
                continue
            dec = evaluate(fa.__dict__, story_models=models)
            a = Asset(story_id=story.id, provider=fa.provider, source_url=fa.source_url, creator=fa.creator,
                      model_name=fa.model_name, year=fa.year, scene_description=fa.scene_description,
                      review_status=dec.status, rejection_reason=None if dec.status == "approved" else dec.reason,
                      is_ai_generated=fa.is_ai_generated, is_illustration=dec.is_illustration, is_demo=fa.is_demo)
            a.license = AssetLicense(license_name=fa.license_name, license_url=fa.license_url,
                                     allowed_commercial=fa.allowed_commercial, attribution_required=fa.attribution_required,
                                     attribution_text=fa.attribution_text, permission_proof=fa.permission_proof)
            if dec.status == "approved":
                path = download(fa, adir)  # CHỈ tải sau khi gate cho phép
                a.local_path, a.content_hash, a.downloaded_at = str(path), file_sha256(path), now()
                approved += 1
            else:
                rejected += 1
            session.add(a)
            audit(session, "system", f"asset.{dec.status}", "asset", None, reason=dec.reason, provider=fa.provider)
    session.flush()
    return {"approved": approved, "rejected": rejected, "fallback_internal_graphics": approved == 0}


# ------------------------------------------------------------------ 7. storyboard

def storyboard_job(session: Session, story: Story, p: Providers) -> dict:
    sv = current_script(session, story)
    segs = [{"segment_key": t.segment_key, "paragraph_idx": t.paragraph_idx, "text": t.text, "start": t.start, "end": t.end}
            for t in session.scalars(select(TTSSegment).where(TTSSegment.script_version_id == sv.id).order_by(TTSSegment.idx))]
    if not segs:
        raise PermanentError("Chưa có audio segment")
    total = sv.actual_audio_seconds
    news = story.news_item
    claims = {c.claim_key: c for c in story_claims(session, story) if c.status == "verified"}
    photos = [{"id": a.id, "path": a.local_path, "caption": a.scene_description, "illustration": a.is_illustration,
               "credit": a.license.attribution_text if a.license else None}
              for a in session.scalars(select(Asset).where(Asset.story_id == story.id, Asset.review_status == "approved",
                                                           Asset.media_type == "image"))]
    title = sv.content["title_options"][sv.content["selected_title_index"]]["title"]
    scenes = storyboard.group_segments(segs, total)
    scenes = storyboard.assign_visuals(scenes, claim_map=sv.content["editorial_claim_map"],
                                       claims={k: {"text": c.text} for k, c in claims.items()}, title=title,
                                       photo_assets=photos, has_timeline=bool(news.effective_to or news.effective_from),
                                       pillar=story.pillar)
    for old in session.scalars(select(Scene).where(Scene.story_id == story.id, Scene.script_version_id == sv.id)):
        session.delete(old)
    gdir = story_dir(story) / f"scenes_v{sv.version}"
    demo = story.is_demo
    by_id = {x["id"]: x for x in photos}
    src_by_id = {x["source_id"]: x for x in (news.supporting_sources or [])}
    for sc in scenes:
        path = gdir / f"scene_{sc.idx:03d}.png"
        vt = sc.visual_type
        if vt == "title":
            graphics.title_card(path, title, sv.content.get("main_angle", ""), demo, kicker=f"{story.pillar} · Phân tích")
        elif vt == "fact":
            c = claims[sc.claim_ids[0]]
            src = src_by_id.get((c.source_ids or ["s1"])[0], {})
            graphics.fact_card(path, c.text, src.get("publisher") or "", (src.get("published_at") or "")[:10], c.claim_key, demo)
        elif vt == "formula":
            graphics.formula_card(path, "Chi phí năng lượng mỗi tháng (ví dụ)",
                                  ["Quãng đường tháng × mức tiêu thụ ÷ 100 = số kWh cần",
                                   "Số kWh × đơn giá bạn thực trả = chi phí năng lượng",
                                   "So sánh với chi phí nhiên liệu hiện tại của bạn"],
                                  "Đây là phép tính minh họa. Giả định: quãng đường, mức tiêu thụ và đơn giá do người xem tự thay.", demo)
        elif vt == "timeline":
            marks = [(d, lbl) for d, lbl in ((news.published_at, "Ngày công bố"), (news.effective_from, "Bắt đầu áp dụng"),
                                             (news.effective_to, "Hết hạn")) if d]
            marks.sort(key=lambda x: x[0])
            items = [(d.strftime("%d/%m"), lbl) for d, lbl in marks]
            graphics.timeline_card(path, items or [("—", "Chưa rõ mốc thời gian")], demo)
        elif vt == "pros_cons":
            rp = latest_pack(session, story).content
            graphics.pros_cons_card(path, [rp.get("video_unique_value", "Hiểu rõ điều kiện")] + [c.text for c in list(claims.values())[:1]],
                                    rp.get("counterarguments", [])[:3], demo)
        elif vt == "question":
            graphics.question_card(path, sc.payload["caption"], demo)
        elif vt == "photo":
            a = by_id[sc.asset_ids[0]]
            graphics.photo_card(path, Path(a["path"]), sc.payload["caption"], a["credit"], a["illustration"], demo)
        elif vt == "credits":
            srcs = [f"{x.get('publisher')} — {x['url']}" for x in (news.supporting_sources or [])]
            graphics.credits_card(path, srcs, [x["credit"] for x in photos if x["credit"]], demo)
        else:
            graphics.narration_card(path, sc.payload["caption"], demo)
        session.add(Scene(story_id=story.id, script_version_id=sv.id, idx=sc.idx, start_seconds=sc.start, end_seconds=sc.end,
                          narration_segment_ids=sc.segment_keys, visual_type=vt, asset_ids=sc.asset_ids,
                          claim_ids=sc.claim_ids, payload=sc.payload | {"internal_graphic": vt != "photo"}, image_path=str(path)))
    session.flush()
    types = {}
    for sc in scenes:
        types[sc.visual_type] = types.get(sc.visual_type, 0) + 1
    return {"scenes": len(scenes), "types": types}


# ------------------------------------------------------------------ 8. render

def render_job(session: Session, story: Story, p: Providers) -> dict:
    s = get_settings()
    sv = current_script(session, story)
    scenes = list(session.scalars(select(Scene).where(Scene.story_id == story.id, Scene.script_version_id == sv.id).order_by(Scene.idx)))
    segs = [{"text": t.text, "start": t.start, "end": t.end}
            for t in session.scalars(select(TTSSegment).where(TTSSegment.script_version_id == sv.id).order_by(TTSSegment.idx))]
    d = story_dir(story) / f"render_v{sv.version}"
    clips_dir = d / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for sc in scenes:
        clip = clips_dir / f"clip_{sc.idx:03d}.mp4"
        if not clip.exists():
            renderer.render_scene_clip(Path(sc.image_path), sc.end_seconds - sc.start_seconds, clip,
                                       ken_burns=sc.visual_type == "photo", fps=s.render_fps)
        clips.append(clip)
    cues = subtitles.build_cues(segs)
    srt = subtitles.write_srt(cues, d / "subtitles.vi.srt")
    ass = subtitles.write_ass(cues, d / "subtitles.vi.ass")
    audio = Path(story_dir(story) / f"audio_v{sv.version}" / "narration.wav")
    final = renderer.compose(clips, audio, ass, d / "final_1080p.mp4", d)
    prev = renderer.preview_360p(final, d / "preview_360p.mp4")
    r = Render(story_id=story.id, script_version_id=sv.id, path=str(final), preview_path=str(prev),
               subtitle_srt_path=str(srt), subtitle_ass_path=str(ass), audio_path=str(audio), sha256=file_sha256(final),
               is_demo=story.is_demo)
    session.add(r)
    session.flush()
    story.current_render_id = r.id
    return {"render_id": r.id, "path": str(final)}


# ------------------------------------------------------------------ 9. SEO + thumbnail

def seo_job(session: Session, story: Story, p: Providers) -> dict:
    sv = current_script(session, story)
    r = session.get(Render, story.current_render_id)
    news = story.news_item
    content = sv.content
    title = content["title_options"][content["selected_title_index"]]["title"]
    photos = list(session.scalars(select(Asset).where(Asset.story_id == story.id, Asset.review_status == "approved")))
    photo = Path(photos[0].local_path) if photos else None
    d = Path(r.path).parent
    t1 = thumbnail.make_thumbnail(d / "thumbnail_A.jpg", title, photo, "A", story.is_demo, bool(photos and photos[0].is_illustration))
    t2 = thumbnail.make_thumbnail(d / "thumbnail_B.jpg", title, photo, "B", story.is_demo, bool(photos and photos[0].is_illustration))
    r.thumbnail_paths = [str(t1), str(t2)]
    # chương theo đoạn văn
    paras = {}
    for t in session.scalars(select(TTSSegment).where(TTSSegment.script_version_id == sv.id).order_by(TTSSegment.idx)):
        paras.setdefault(t.paragraph_idx, (t.start, t.text))
    marks = [(start, storyboard.chapter_title(txt)) for _, (start, txt) in sorted(paras.items()) if start > 0]
    chapters = seo.chapters_from_scenes([], marks)
    sources = [x for x in (news.supporting_sources or [])]
    credits = [a.license.attribution_text for a in photos if a.license and a.license.attribution_text]
    idem_tag = "evcs-" + input_hash(story.id, r.sha256)[:12]
    desc = seo.build_description(main_angle=content["main_angle"], summary_line=content["title_options"][0]["title"],
                                 chapters=chapters, sources=sources, asset_credits=credits,
                                 has_estimates="phép tính ví dụ" in sv.spoken_script or "ví dụ" in sv.spoken_script,
                                 pillar=story.pillar, is_demo=story.is_demo)
    tags = seo.build_tags(story.pillar, news.model_names or [], news.entities or [], idem_tag)
    v = SEOVariant(story_id=story.id, render_id=r.id, title_options=content["title_options"],
                   selected_index=content["selected_title_index"], description=desc, tags=tags,
                   chapters=[{"t": c["t"], "title": c["title"]} for c in chapters], hashtags=desc.splitlines()[-1].split())
    session.add(v)
    session.flush()
    return {"seo_id": v.id, "title": title, "thumbnails": r.thumbnail_paths}


# ------------------------------------------------------------------ 10. media QA

def qa_job(session: Session, story: Story, p: Providers) -> dict:
    s = get_settings()
    r = session.get(Render, story.current_render_id)
    sv = current_script(session, story)
    v = session.scalar(select(SEOVariant).where(SEOVariant.render_id == r.id).order_by(SEOVariant.created_at.desc()))
    scenes = list(session.scalars(select(Scene).where(Scene.story_id == story.id, Scene.script_version_id == sv.id)))
    used_ids = {a for sc in scenes for a in sc.asset_ids}
    assets = [{"id": a.id, "review_status": a.review_status if a.license and a.license.license_name else "missing_license"}
              for a in session.scalars(select(Asset).where(Asset.id.in_(used_ids)))] if used_ids else []
    verified = {c.claim_key for c in story_claims(session, story) if c.status == "verified"}
    facts_ok = all(cid in verified for sc in scenes for cid in sc.claim_ids) and all(
        f["claim_id"] in verified for f in sv.content.get("production_notes", {}).get("key_on_screen_facts", []))
    res = media_qa.run_media_qa(Path(r.path), exact_10min=True, dur_min=s.duration_min_seconds, dur_max=s.duration_max_seconds,
                                assets=assets, on_screen_facts_ok=facts_ok, description=v.description if v else "",
                                sources=story.news_item.supporting_sources or [],
                                thumbnail=Path(r.thumbnail_paths[0]) if r.thumbnail_paths else None,
                                subtitle=Path(r.subtitle_srt_path) if r.subtitle_srt_path else None,
                                hard_min_seconds=0 if s.allow_short_duration_for_tests else MIN_VIDEO_SECONDS)
    r.qa, r.qa_passed, r.duration, r.width, r.height = res, res["passed"], res["duration"], res["width"], res["height"]
    if res["sha256"] != r.sha256:
        r.qa_passed = False
        res["checks"].append({"name": "Checksum khớp file render", "ok": False, "detail": "file đã thay đổi"})
    session.commit()
    if not r.qa_passed:
        raise GateBlocked([f"Media QA: {c['name']} — {c['detail']}" for c in res["checks"] if not c["ok"]])
    return {"qa": res["summary"]}


# ------------------------------------------------------------------ 11. upload private

def upload_gate(session: Session, story: Story) -> list[str]:
    """Gate backend chặn MỌI đường đi vào upload. Trả danh sách lý do (rỗng = được phép)."""
    s = get_settings()
    reasons = []
    if story.state not in (S.READY_TO_UPLOAD.value, S.UPLOADING.value):
        reasons.append(f"Trạng thái {story.state} chưa phải READY_TO_UPLOAD")
    r = session.get(Render, story.current_render_id) if story.current_render_id else None
    if not r or not r.qa_passed:
        reasons.append("QA render chưa PASS")
    elif not Path(r.path).exists() or file_sha256(Path(r.path)) != r.sha256:
        reasons.append("File render không khớp checksum đã QA")
    claims = story_claims(session, story)
    reasons += fc.blocking_claims(claims)
    if any(c.status == "verified" and not c.source_url for c in claims):
        reasons.append("Có claim verified thiếu source_url")
    if not fc.freshness_ok(story.freshness_checked_at):
        reasons.append("freshness_check quá hạn — cần chạy lại FactCheckJob")
    if r:
        scenes = session.scalars(select(Scene).where(Scene.story_id == story.id, Scene.script_version_id == r.script_version_id))
        ids = {a for sc in scenes for a in sc.asset_ids}
        for a in session.scalars(select(Asset).where(Asset.id.in_(ids))) if ids else []:
            if a.review_status != "approved" or not (a.license and a.license.license_name):
                reasons.append(f"Asset {a.id[:8]} thiếu license/được duyệt")
    ch = session.get(Channel, story.channel_id) if story.channel_id else None
    if ch is None:
        reasons.append("Chưa chọn kênh đích")
    elif s.youtube_expected_channel_id and ch.youtube_channel_id != s.youtube_expected_channel_id:
        reasons.append("Kênh đã chọn khác YOUTUBE_EXPECTED_CHANNEL_ID")
    elif ch.is_demo != (youtube_mode() == "demo"):
        reasons.append("Kênh DEMO không dùng được ở production (và ngược lại)")
    if story.mode == "AUTOPILOT_PRIVATE":
        if not get_setting(session, "autopilot_enabled", False) or not story.autopilot_authorized:
            reasons.append("Autopilot Private chưa được chủ kênh cho phép")
    elif not story.upload_requested_by:
        reasons.append("MANUAL: chủ kênh chưa bấm 'Tải lên RIÊNG TƯ'")
    return reasons


def make_youtube_client(session: Session, channel: Channel, p: Providers):
    if p.youtube is not None:
        return p.youtube
    from ..security import decrypt_str, encrypt_str
    from ..services.youtube.client import DemoYouTubeClient, GoogleYouTubeClient, credentials_from_json

    if youtube_mode() == "demo":
        return DemoYouTubeClient(channel_id=channel.youtube_channel_id, channel_title=channel.title)
    if not channel.oauth_token_encrypted:
        from ..errors import BlockedByCredentials
        raise BlockedByCredentials("Kênh chưa kết nối OAuth")

    def on_refresh(tok: str):
        channel.oauth_token_encrypted = encrypt_str(tok)
        channel.token_refreshed_at = now()

    return GoogleYouTubeClient(credentials_from_json(decrypt_str(channel.oauth_token_encrypted)), on_refresh=on_refresh)


def upload_private_job(session: Session, story: Story, p: Providers) -> dict:
    s = get_settings()
    reasons = upload_gate(session, story)
    if reasons:
        raise GateBlocked(reasons)
    r = session.get(Render, story.current_render_id)
    ch = session.get(Channel, story.channel_id)
    v = session.scalar(select(SEOVariant).where(SEOVariant.render_id == r.id).order_by(SEOVariant.created_at.desc()))
    idem = f"{story.id}:{r.sha256}:{ch.youtube_channel_id}"
    up = session.scalar(select(YouTubeUpload).where(YouTubeUpload.idempotency_key == idem))
    if up is None:
        up = YouTubeUpload(story_id=story.id, render_id=r.id, channel_id=ch.id, idempotency_key=idem,
                           privacy_status_requested=PRIVATE, is_demo=ch.is_demo)
        session.add(up)
        session.commit()
    client = make_youtube_client(session, ch, p)
    me = client.get_my_channel()
    if me["id"] != ch.youtube_channel_id:
        up.status, up.error = "failed", "Kênh OAuth không khớp kênh đích"
        session.commit()
        raise ChannelMismatch(f"OAuth đang ở kênh {me['id']} nhưng kênh đích là {ch.youtube_channel_id}")
    up.attempts += 1
    if not up.youtube_video_id:
        tag = v.tags[0]  # evcs-<hash> — tag idempotency
        existing = client.find_existing_by_tag(tag)
        if existing:
            up.youtube_video_id = existing
            audit(session, "system", "youtube.reconciled_existing", "youtube_upload", up.id, video_id=existing)
        else:
            body = build_insert_body(title=v.selected_title, description=v.description, tags=v.tags,
                                     category_id=s.youtube_category_id,
                                     made_for_kids_confirmed_false=s.youtube_made_for_kids_confirmed_false,
                                     contains_synthetic_media=s.youtube_contains_synthetic_media)
            up.status = "uploading"
            session.commit()
            vid = client.insert_video(body, Path(r.path))
            up.youtube_video_id, up.status = vid, "uploaded"
        session.commit()  # lưu video id NGAY để retry không đăng trùng
    if r.thumbnail_paths and not up.thumbnail_set:
        try:
            client.set_thumbnail(up.youtube_video_id, Path(r.thumbnail_paths[0]))
            up.thumbnail_set = True
        except Exception as e:  # thumbnail lỗi không chặn private
            up.error = f"Thumbnail chưa đặt được: {redact(e)}"
    privacy = client.get_privacy(up.youtube_video_id)
    if privacy != PRIVATE:
        audit(session, "system", "youtube.privacy_mismatch", "youtube_upload", up.id, observed=privacy)
        client.force_private(up.youtube_video_id)
        privacy = client.get_privacy(up.youtube_video_id)
        if privacy != PRIVATE:
            up.privacy_status_observed = privacy
            session.commit()
            raise PrivacyViolation(f"Video {up.youtube_video_id} đọc lại privacy={privacy}, không phải private")
    up.privacy_status_observed, up.verified_at, up.status = PRIVATE, now(), "verified"
    audit(session, "system", "youtube.upload_private_verified", "youtube_upload", up.id, video_id=up.youtube_video_id,
          is_demo=up.is_demo)
    session.flush()
    return {"video_id": up.youtube_video_id, "privacy": PRIVATE, "is_demo": up.is_demo}


# ------------------------------------------------------------------ 12. Telegram

def notify_telegram_job(session: Session, story: Story, p: Providers) -> dict:
    s = get_settings()
    up = session.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == story.id, YouTubeUpload.status == "verified"))
    if not up or up.privacy_status_observed != PRIVATE:
        raise GateBlocked("Chỉ gửi Telegram sau khi YouTube xác nhận PRIVATE")
    chats = s.allowed_chat_ids or ({"DEMO_CHAT"} if story.is_demo else set())
    if not chats:
        from ..errors import BlockedByCredentials
        raise BlockedByCredentials("Thiếu TELEGRAM_ALLOWED_CHAT_IDS")
    r = session.get(Render, story.current_render_id)
    v = session.scalar(select(SEOVariant).where(SEOVariant.render_id == r.id).order_by(SEOVariant.created_at.desc()))
    news = story.news_item
    unverified = sum(c.status in ("unverified", "contested") and c.is_key for c in story_claims(session, story))
    review_url = f"{s.public_base_url.rstrip('/')}/api/review/{story.id}?t={sign_link('review', story.id)}"
    studio = f"https://studio.youtube.com/video/{up.youtube_video_id}/edit"
    pub_date = news.published_at.strftime("%d/%m/%Y") if news.published_at else "không rõ ngày"
    text = build_review_message(
        topic=story.topic, pillar=story.pillar, title=v.selected_title, duration_s=r.duration or 0,
        source_line=f"{news.publisher} — {pub_date}", qa_summary=r.qa.get("summary", "?"), unverified=unverified,
        review_url=review_url, studio_url=studio, video_id=up.youtube_video_id, is_demo=story.is_demo)
    tg = p.get_telegram()
    sent = 0
    for chat in sorted(chats):
        msg = session.scalar(select(TelegramMessage).where(TelegramMessage.story_id == story.id, TelegramMessage.chat_id == chat))
        if msg and msg.status == "sent":
            sent += 1
            continue
        if msg is None:
            msg = TelegramMessage(story_id=story.id, chat_id=chat, text=text, is_demo=tg.is_demo, attempts=0)
            session.add(msg)
        exp = now() + timedelta(seconds=s.telegram_callback_ttl_seconds)
        ta, trv = random_token(), random_token()
        session.add(TelegramCallbackToken(token=ta, story_id=story.id, action="approve", chat_id=chat, expires_at=exp))
        session.add(TelegramCallbackToken(token=trv, story_id=story.id, action="request_revision", chat_id=chat, expires_at=exp))
        msg.attempts += 1
        try:
            mid = tg.send(chat, text, build_buttons(ta, trv, studio, review_url),
                          Path(r.thumbnail_paths[0]) if r.thumbnail_paths else None)
            msg.message_id, msg.status, msg.error = mid, "sent", None
            sent += 1
        except RetryableError as e:
            msg.status, msg.error = "failed", redact(e)
            session.commit()
            raise
        session.commit()
    return {"sent": sent, "chats": len(chats)}


# ------------------------------------------------------------------ 13. refresh status (quan sát, không thay đổi)

def refresh_status_job(session: Session, story: Story, p: Providers) -> dict:
    up = session.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == story.id, YouTubeUpload.youtube_video_id.is_not(None)))
    if not up:
        return {"observed": None}
    ch = session.get(Channel, up.channel_id)
    client = make_youtube_client(session, ch, p)
    privacy = client.get_privacy(up.youtube_video_id)
    up.privacy_status_observed, up.observed_at = privacy, now()
    if privacy == "public" and story.state in (S.EDITOR_REVIEW.value, S.APPROVED_PRIVATE.value):
        transition(session, story, S.PUBLISHED_MANUALLY_OBSERVED, note="Chủ kênh đã tự công khai trên YouTube Studio")
        session.add(ReviewEvent(story_id=story.id, action="observed_published", actor="RefreshStatusJob", via="youtube",
                                note="Quan sát thấy video đã được chủ kênh công khai thủ công"))
    return {"observed": privacy}


# ------------------------------------------------------------------ pipeline

def _step(session, story, job_type, ihash, fn, p, **kw):
    return run_job(session, story, job_type, ihash, lambda: fn(session, story, p, **kw))


def run_pipeline(story_id: str, providers: Providers | None = None, actor: str = "system",
                 session: Session | None = None, stop_at: str | None = None) -> dict:
    """Chạy từ trạng thái hiện tại tới điểm dừng tiếp theo (chờ người duyệt hoặc hoàn tất).

    MANUAL dừng ở SCRIPT_DRAFT (chờ duyệt script) và READY_TO_UPLOAD (chờ bấm upload RIÊNG TƯ).
    AUTOPILOT_PRIVATE chạy tiếp nếu mọi gate tự động PASS.
    """
    from ..db import SessionLocal

    p = providers or Providers()
    own = session is None
    session = session or SessionLocal()
    s = get_settings()
    try:
        story = session.get(Story, story_id)
        if story is None:
            raise PermanentError("Story không tồn tại")
        if not acquire_lease(session, story_id):
            return {"state": story.state, "note": "Story đang được worker khác xử lý"}
        story = session.get(Story, story_id)
        try:
            if story.state in (S.FAILED_RETRYABLE.value, S.NEEDS_REVIEW.value, S.FAILED_PERMANENT.value) and story.resume_state:
                transition(session, story, S(story.resume_state), actor, "resume")
                session.commit()
            auto = story.mode == "AUTOPILOT_PRIVATE" and story.autopilot_authorized
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.DISCOVERED.value:
                transition(session, story, S.RESEARCHING, actor)
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.RESEARCHING.value:
                _step(session, story, "FactCheckJob", input_hash(story.id, "fc", now().date().isoformat(), story.updated_at), fact_check_job, p)
                transition(session, story, S.FACT_CHECK_PENDING, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.FACT_CHECK_PENDING.value:
                _step(session, story, "ResearchJob", input_hash(story.id, "rp", story.revision, story.editorial_notes), research_job, p)
                transition(session, story, S.SCRIPT_DRAFT, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.SCRIPT_DRAFT.value:
                sv = session.get(ScriptVersion, story.current_script_version_id) if story.current_script_version_id else None
                if sv is None or not sv.qa_passed:
                    if sv is None:
                        _step(session, story, "ScriptJob", input_hash(story.id, latest_pack(session, story).id, 0), script_job, p)
                        session.commit()
                        sv = current_script(session, story)
                if not sv.qa_passed:
                    raise GateBlocked([f"Script QA chưa đạt (v{sv.version})"])
                if not (auto or story.script_approved_by):
                    session.commit()
                    return {"state": story.state, "waiting_for": "Biên tập viên duyệt kịch bản"}
                transition(session, story, S.SCRIPT_APPROVED_FOR_RENDER, actor)
                session.commit()
            # TTS + vòng chỉnh thời lượng theo audio thật
            revisions = 0
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            while story.state == S.SCRIPT_APPROVED_FOR_RENDER.value:
                sv = current_script(session, story)
                try:
                    try:
                        tts_ver = p.get_tts().model_version  # đổi bộ đọc (vd: demo → VieNeu) thì phải đọc lại
                    except BlockedByCredentials:
                        tts_ver = "unavailable"
                    _step(session, story, "TTSJob", input_hash(sv.id, get_setting(session, "voice", s.vieneu_voice), tts_ver),
                          tts_job, p)
                    transition(session, story, S.TTS_DONE, actor)
                    session.commit()
                except DurationOutOfRange as e:
                    session.commit()
                    from ..agents import learning

                    learning.on_audio_measured(session, sv.word_count, e.seconds)
                    session.commit()
                    revisions += 1
                    if revisions > s.max_script_revisions:
                        raise GateBlocked([f"Sai thời lượng sau {s.max_script_revisions} lần viết lại: {e.seconds:.0f}s "
                                           f"(mục tiêu {s.duration_min_seconds:.0f}–{s.duration_max_seconds:.0f}s)"])
                    transition(session, story, S.SCRIPT_DRAFT, actor, f"Viết lại vì audio {e.seconds:.0f}s")
                    _step(session, story, "ScriptJob", input_hash(sv.id, "dur", round(e.seconds, 1)), script_job, p,
                          actual_audio_seconds=e.seconds)
                    transition(session, story, S.SCRIPT_APPROVED_FOR_RENDER, actor, "duration-fix (QA đạt)")
                    session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.TTS_DONE.value:
                sv = current_script(session, story)
                _step(session, story, "MediaJob", input_hash(story.id, sv.id), media_job, p)
                transition(session, story, S.ASSETS_READY, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.ASSETS_READY.value:
                sv = current_script(session, story)
                if not fc.freshness_ok(story.freshness_checked_at):
                    _step(session, story, "FactCheckJob", input_hash(story.id, "fc-render", now().isoformat()), fact_check_job, p)
                _step(session, story, "StoryboardJob", input_hash(sv.id, "sb"), storyboard_job, p)
                transition(session, story, S.RENDERING, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.RENDERING.value:
                sv = current_script(session, story)
                _step(session, story, "RenderJob", input_hash(sv.id, "render"), render_job, p)
                _step(session, story, "SEOJob", input_hash(story.current_render_id, "seo"), seo_job, p)
                transition(session, story, S.QA_PENDING, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.QA_PENDING.value:
                _step(session, story, "MediaQAJob", input_hash(story.current_render_id, "qa"), qa_job, p)
                transition(session, story, S.READY_TO_UPLOAD, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.READY_TO_UPLOAD.value:
                if not auto and not story.upload_requested_by:
                    return {"state": story.state, "waiting_for": "Chủ kênh bấm 'Tải lên RIÊNG TƯ'"}
                if not fc.freshness_ok(story.freshness_checked_at):
                    _step(session, story, "FactCheckJob", input_hash(story.id, "fc-upload", now().isoformat()), fact_check_job, p)
                reasons = upload_gate(session, story)
                if reasons:
                    raise GateBlocked(reasons)
                transition(session, story, S.UPLOADING, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.UPLOADING.value:
                try:
                    _step(session, story, "UploadPrivateJob", input_hash(story.current_render_id, story.channel_id), upload_private_job, p)
                except RetryableError:
                    session.rollback()
                    story = session.get(Story, story_id)
                    transition(session, story, S.READY_TO_UPLOAD, actor, "upload lỗi mạng — sẽ resume, không đăng trùng")
                    session.commit()
                    raise
                transition(session, story, S.UPLOADED_PRIVATE, actor)
                session.commit()
            if stop_at and story.state == stop_at:
                return {"state": story.state, "stopped_at": stop_at}
            if story.state == S.UPLOADED_PRIVATE.value:
                try:
                    _step(session, story, "NotifyTelegramJob", input_hash(story.id, "tg", story.current_render_id), notify_telegram_job, p)
                    transition(session, story, S.TELEGRAM_NOTIFIED, actor)
                    transition(session, story, S.EDITOR_REVIEW, actor)
                    story.blockers = []
                except Exception as e:  # Telegram lỗi: video VẪN private, giữ UPLOADED_PRIVATE + cảnh báo
                    session.rollback()
                    story = session.get(Story, story_id)
                    story.blockers = [f"Telegram chưa gửi được: {redact(e)}"]
                    story.last_error = redact(e)
                    audit(session, "system", "telegram.failed", "story", story.id, error=redact(e))
                    session.commit()
                    if not was_logged(e):
                        log.warning("Telegram chưa gửi được cho story %s: %s", story_id, redact(e), exc_info=e,
                                    extra={"evcs_source": "telegram", "evcs_story_id": story_id})
                session.commit()
            return {"state": story.state, "blockers": story.blockers}
        except DurationOutOfRange as e:  # pragma: no cover - đã xử lý trong vòng lặp
            fail_story(session, story, GateBlocked(str(e)))
            return {"state": session.get(Story, story_id).state, "error": str(e)}
        except Exception as e:
            fail_story(session, story, e)  # rollback trước rồi mới ghi log → không giữ khoá DB khi ghi Nhật ký lỗi
            st = session.get(Story, story_id)
            if not was_logged(e):
                gate = isinstance(e, (GateBlocked, BlockedByCredentials))
                log.log(logging.WARNING if gate else logging.ERROR, "Pipeline dừng ở story %s (%s): %s", story_id,
                        st.state, redact(e), exc_info=None if gate else e,
                        extra={"evcs_source": "pipeline", "evcs_story_id": story_id,
                               "evcs_details": {"topic": st.topic, "state": st.state, "blockers": st.blockers}})
            return {"state": st.state, "error": st.last_error, "blockers": st.blockers}
        finally:
            release_lease(session, story_id)
    finally:
        if own:
            session.close()


# ------------------------------------------------------------------ review / revision / autopilot

def apply_review(session: Session, story: Story, action: str, actor: str, via: str, note: str | None = None) -> dict:
    """Duyệt KHÔNG BAO GIỜ đổi privacy YouTube. Yêu cầu sửa → story-version mới; bản private cũ giữ nguyên."""
    if story.state not in {x.value for x in HAS_PRIVATE_UPLOAD} - {S.REVISION_REQUESTED.value, S.REJECTED.value}:
        raise GateBlocked(f"Chưa thể duyệt ở trạng thái {story.state}")
    if story.state in (S.UPLOADED_PRIVATE.value, S.TELEGRAM_NOTIFIED.value):
        transition(session, story, S.EDITOR_REVIEW, actor)
    ev = ReviewEvent(story_id=story.id, action=action, actor=actor, via=via, note=note)
    session.add(ev)
    out: dict = {"story_id": story.id}
    from ..agents import learning

    if action == "approve":
        transition(session, story, S.APPROVED_PRIVATE, actor, "Đã kiểm duyệt — video VẪN RIÊNG TƯ")
    elif action == "request_revision":
        new = create_revision(session, story, note or "", actor)
        ev.new_story_id = new.id
        out["new_story_id"] = new.id
        learning.on_revision_requested(session, story, note or "")
    elif action == "reject":
        transition(session, story, S.REJECTED, actor)
        learning.on_rejected(session, story, note)
    else:
        raise GateBlocked(f"Hành động không hợp lệ: {action}")
    out["state"] = story.state
    return out


def create_revision(session: Session, story: Story, note: str, actor: str) -> Story:
    transition(session, story, S.REVISION_REQUESTED, actor, note[:200])
    new = Story(news_item_id=story.news_item_id, parent_story_id=story.id, revision=story.revision + 1, pillar=story.pillar,
                topic=story.topic, core_question=story.core_question,
                editorial_notes=(story.editorial_notes + "\n" if story.editorial_notes else "") + f"[Yêu cầu sửa v{story.revision}] {note}",
                mode="MANUAL", channel_id=story.channel_id, is_demo=story.is_demo)
    session.add(new)
    session.flush()
    audit(session, actor, "story.revision_created", "story", new.id, parent=story.id)
    return new


def autopilot_tick(session: Session, providers: Providers | None = None) -> dict:
    s = get_settings()
    if not get_setting(session, "autopilot_enabled", False):
        return {"ran": False, "reason": "Autopilot đang tắt"}
    ch = session.scalar(select(Channel).where(Channel.selected.is_(True)))
    if ch is None:
        return {"ran": False, "reason": "Chưa chọn kênh đích"}
    week = session.scalar(select(func.count(Story.id)).where(Story.mode == "AUTOPILOT_PRIVATE",
                                                             Story.created_at >= now() - timedelta(days=7))) or 0
    if week >= s.max_videos_per_week:
        return {"ran": False, "reason": f"Đã đạt {s.max_videos_per_week} video/tuần"}
    from ..agents import budget

    ok, why = budget.can_start_story(session)  # Ban Tài chính: tối đa N tin/ngày và phải còn đủ tiền cho một video
    if not ok:
        return {"ran": False, "reason": why}
    from ..agents.growth import autopilot_priority, pillar_weights

    used = {x for (x,) in session.execute(select(Story.news_item_id))}
    cands = list(session.scalars(select(NewsItem).where(NewsItem.status == "shortlist", NewsItem.score >= s.score_autopilot)
                                 .order_by(NewsItem.score.desc())))
    w = pillar_weights(session)  # Ban Tăng trưởng: ưu tiên trụ cột đang mang lại doanh thu/lượt xem (không hạ ngưỡng điểm)
    cands.sort(key=lambda n: -autopilot_priority(n.score, n.pillar, w))
    for n in cands:
        if n.id in used:
            continue
        claims = list(n.claims)
        if fc.blocking_claims(claims) or not any(c.status == "verified" and c.is_key for c in claims):
            continue
        st = Story(news_item_id=n.id, pillar=n.pillar or "P1", topic=n.title.replace("[DEMO] ", ""), mode="AUTOPILOT_PRIVATE",
                   autopilot_authorized=True, channel_id=ch.id, is_demo=n.is_demo)
        session.add(st)
        session.commit()
        audit(session, "autopilot", "story.created", "story", st.id, news=n.id, score=n.score)
        session.commit()
        res = run_pipeline(st.id, providers)
        return {"ran": True, "story_id": st.id, **res}
    return {"ran": False, "reason": "Không có tin ≥ ngưỡng Autopilot đủ chứng cứ"}
