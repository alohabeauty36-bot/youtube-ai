"""FastAPI — API nội bộ (mục L). KHÔNG có endpoint/hành động publish công khai."""
from __future__ import annotations

import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import init_db
from ..errors import BlockedByCredentials, EVStudioError, GateBlocked
from ..models import (
    AppSetting, Asset, AuditLog, Channel, Claim, CostUsage, ErrorLog, Job, NewsItem, Render, ResearchPack, ReviewEvent, Scene,
    ScriptVersion, SEOVariant, Source, Story, TelegramMessage, TTSSegment, YouTubeUpload, now,
)
from ..security import audit, encrypt_str, redact, setup_logging, sign_link, verify_link
from ..state_machine import StoryState as S
from .deps import Actor, current_actor, get_db, is_trusted_local, require_owner

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    from ..services.telegram.callbacks import start_polling
    from ..workers.schedules import start_scheduler

    start_scheduler()
    start_polling()  # tự bật khi có bot token + production (cấu hình đổi được lúc chạy)
    yield
    from ..workers.schedules import stop_scheduler

    stop_scheduler()


app = FastAPI(title="EV Creator Studio API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[get_settings().frontend_base_url, "http://localhost:3000"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(EVStudioError)
async def _evs_err(request: Request, exc: EVStudioError):
    from fastapi.responses import JSONResponse

    code = 409 if isinstance(exc, GateBlocked) else 424 if isinstance(exc, BlockedByCredentials) else 400
    return JSONResponse({"detail": redact(exc), "reasons": getattr(exc, "reasons", None)}, status_code=code)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Lỗi không lường trước ở API → ghi vào Nhật ký lỗi, trả 500 gọn (không lộ traceback ra trình duyệt)."""
    from fastapi.responses import JSONResponse

    log.error("API %s %s lỗi: %s", request.method, request.url.path, redact(f"{type(exc).__name__}: {exc}"), exc_info=exc,
              extra={"evcs_source": "api", "evcs_details": {"method": request.method, "path": request.url.path}})
    return JSONResponse({"detail": f"Lỗi máy chủ ({type(exc).__name__}). Xem chi tiết ở trang Nhật ký lỗi."}, status_code=500)


# ------------------------------------------------------------------ serializers

def media_url(story_id: str, kind: str) -> str:
    return f"/api/media/{story_id}/{kind}?t={sign_link('media', f'{story_id}/{kind}', 6 * 3600)}"


def ser_news(n: NewsItem, with_claims: bool = True) -> dict:
    d = {k: getattr(n, k) for k in ("id", "title", "url", "canonical_url", "publisher", "published_at", "event_at",
                                    "effective_from", "effective_to", "pillar", "score", "score_breakdown", "score_reasons",
                                    "status", "duplicate_of", "block_reason", "trust_tier", "model_names", "entities",
                                    "supporting_sources", "contradictions", "is_demo", "raw_excerpt", "flags")}
    d["supporting_sources"] = [{k: v for k, v in x.items() if k != "text"} for x in (n.supporting_sources or [])]
    if with_claims:
        d["claims"] = [ser_claim(c) for c in n.claims]
    return d


def ser_claim(c: Claim) -> dict:
    return {k: getattr(c, k) for k in ("id", "claim_key", "text", "claim_type", "subject", "value", "unit", "date_scope",
                                       "source_url", "source_ids", "evidence_excerpt", "status", "is_key", "sensitive",
                                       "volatile", "verification_reason", "editor_note", "freshness_checked_at")}


def ser_story(st: Story) -> dict:
    return {k: getattr(st, k) for k in ("id", "news_item_id", "parent_story_id", "revision", "pillar", "topic", "core_question",
                                        "editorial_notes", "state", "resume_state", "mode", "autopilot_authorized",
                                        "upload_requested_by", "script_approved_by", "channel_id", "current_script_version_id",
                                        "current_render_id", "last_error", "blockers", "is_demo", "created_at", "updated_at",
                                        "freshness_checked_at")}


def story_detail(db: Session, st: Story) -> dict:
    from ..workers.executor import is_running

    out = ser_story(st)
    out["running"] = is_running(st.id)
    out["news"] = ser_news(st.news_item) if st.news_item else None
    rp = db.scalar(select(ResearchPack).where(ResearchPack.story_id == st.id).order_by(ResearchPack.version.desc()))
    out["research_pack"] = {"id": rp.id, "version": rp.version, "content": rp.content, "is_demo": rp.is_demo, "model": rp.model} if rp else None
    svs = list(db.scalars(select(ScriptVersion).where(ScriptVersion.story_id == st.id).order_by(ScriptVersion.version.desc())))
    out["scripts"] = [{"id": v.id, "version": v.version, "created_by": v.created_by, "created_at": v.created_at, "word_count": v.word_count,
                       "qa_passed": v.qa_passed, "actual_audio_seconds": v.actual_audio_seconds, "change_note": v.change_note,
                       "is_demo": v.is_demo} for v in svs]
    cur = db.get(ScriptVersion, st.current_script_version_id) if st.current_script_version_id else None
    out["script"] = {"id": cur.id, "version": cur.version, "content": cur.content, "qa": cur.qa, "qa_passed": cur.qa_passed,
                     "actual_audio_seconds": cur.actual_audio_seconds, "is_demo": cur.is_demo} if cur else None
    if cur:
        segs = list(db.scalars(select(TTSSegment).where(TTSSegment.script_version_id == cur.id).order_by(TTSSegment.idx)))
        out["segments"] = [{"idx": t.idx, "key": t.segment_key, "text": t.text, "start": t.start, "end": t.end, "duration": t.duration,
                            "qc": t.qc, "cache_hit": t.cache_hit, "voice": t.voice, "url": media_url(st.id, f"segment_{t.idx}")}
                           for t in segs]
        if segs:
            out["audio_url"] = media_url(st.id, "audio")
        scenes = list(db.scalars(select(Scene).where(Scene.story_id == st.id, Scene.script_version_id == cur.id).order_by(Scene.idx)))
        out["scenes"] = [{"idx": sc.idx, "start": sc.start_seconds, "end": sc.end_seconds, "visual_type": sc.visual_type,
                          "asset_ids": sc.asset_ids, "claim_ids": sc.claim_ids, "caption": sc.payload.get("caption"),
                          "image_url": media_url(st.id, f"scene_{sc.idx}")} for sc in scenes]
    out["assets"] = [{"id": a.id, "provider": a.provider, "source_url": a.source_url, "creator": a.creator,
                      "review_status": a.review_status, "rejection_reason": a.rejection_reason, "is_illustration": a.is_illustration,
                      "scene_description": a.scene_description, "is_demo": a.is_demo,
                      "license": {"name": a.license.license_name, "url": a.license.license_url, "commercial": a.license.allowed_commercial,
                                  "attribution": a.license.attribution_text, "proof": a.license.permission_proof} if a.license else None}
                     for a in db.scalars(select(Asset).where(Asset.story_id == st.id))]
    r = db.get(Render, st.current_render_id) if st.current_render_id else None
    if r:
        out["render"] = {"id": r.id, "sha256": r.sha256, "duration": r.duration, "width": r.width, "height": r.height, "qa": r.qa,
                         "qa_passed": r.qa_passed, "is_demo": r.is_demo, "video_url": media_url(st.id, "video"),
                         "preview_url": media_url(st.id, "preview"), "srt_url": media_url(st.id, "srt"),
                         "thumbnails": [media_url(st.id, f"thumbnail_{i}") for i in range(len(r.thumbnail_paths or []))]}
        v = db.scalar(select(SEOVariant).where(SEOVariant.render_id == r.id).order_by(SEOVariant.created_at.desc()))
        out["seo"] = {"id": v.id, "title_options": v.title_options, "selected_index": v.selected_index, "description": v.description,
                      "tags": v.tags, "chapters": v.chapters} if v else None
    ups = list(db.scalars(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id)))
    out["uploads"] = [{"id": u.id, "status": u.status, "youtube_video_id": u.youtube_video_id, "privacy_requested": u.privacy_status_requested,
                       "privacy_observed": u.privacy_status_observed, "verified_at": u.verified_at, "attempts": u.attempts,
                       "error": u.error, "is_demo": u.is_demo,
                       "studio_url": f"https://studio.youtube.com/video/{u.youtube_video_id}/edit" if u.youtube_video_id else None}
                      for u in ups]
    out["telegram"] = [{"chat_id": m.chat_id, "status": m.status, "attempts": m.attempts, "error": m.error, "is_demo": m.is_demo,
                        "created_at": m.created_at} for m in db.scalars(select(TelegramMessage).where(TelegramMessage.story_id == st.id))]
    out["jobs"] = [{"id": j.id, "job_type": j.job_type, "status": j.status, "attempts": j.attempts, "error": j.error,
                    "duration_seconds": j.duration_seconds, "created_at": j.created_at, "history": j.history}
                   for j in db.scalars(select(Job).where(Job.story_id == st.id).order_by(Job.created_at))]
    out["reviews"] = [{"action": e.action, "actor": e.actor, "via": e.via, "note": e.note, "created_at": e.created_at,
                       "new_story_id": e.new_story_id} for e in db.scalars(select(ReviewEvent).where(ReviewEvent.story_id == st.id))]
    ch = db.get(Channel, st.channel_id) if st.channel_id else None
    out["channel"] = {"id": ch.id, "title": ch.title, "youtube_channel_id": ch.youtube_channel_id, "is_demo": ch.is_demo} if ch else None
    return out


def get_story(db: Session, story_id: str) -> Story:
    st = db.get(Story, story_id)
    if st is None:
        raise HTTPException(404, "Không tìm thấy story")
    return st


# ------------------------------------------------------------------ system

@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    from ..services.renderer import ffmpeg_available

    s = get_settings()
    db.execute(select(1))
    return {"status": "ok", "mode": s.app_mode, "ffmpeg": ffmpeg_available(), "time": now()}


@app.get("/api/system/integrations")
def integrations(actor: Actor = Depends(current_actor)):
    s = get_settings()

    def st(ok: bool, demo: bool) -> str:
        return "DEMO" if demo else ("CONFIGURED" if ok else "BLOCKED_BY_CREDENTIALS")

    vieneu_installed = True
    try:
        import vieneu  # noqa: F401
    except ImportError:
        vieneu_installed = False
    return {
        "mode": s.app_mode,
        "anthropic": st(bool(s.anthropic_api_key), s.is_demo and not s.anthropic_api_key and s.writer_provider != "anthropic"),
        "anthropic_model": s.anthropic_model,
        "tts": st(vieneu_installed or s.tts_provider == "vieneu_http", s.is_demo and s.tts_provider in ("auto", "demo")),
        "tts_provider": s.tts_provider, "voice": s.vieneu_voice,
        "youtube": st(bool(s.google_client_secrets_json or (s.google_client_secrets_file and Path(s.google_client_secrets_file).exists())), s.is_demo),
        "telegram": st(bool(s.telegram_bot_token and s.allowed_chat_ids), s.is_demo),
        "pexels": st(bool(s.pexels_api_key), s.is_demo),
        "privacy_policy": "Mọi upload: privacyStatus=private (không cấu hình được). Không có chức năng công khai.",
    }


# ------------------------------------------------------------------ sources & news

class SourceIn(BaseModel):
    name: str
    domain: str
    feed_url: str | None = None
    trust_tier: int = Field(2, ge=1, le=3)
    allowed: bool = True
    blocked: bool = False


@app.get("/api/sources")
def list_sources(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    return [{k: getattr(x, k) for k in ("id", "name", "domain", "feed_url", "kind", "trust_tier", "allowed", "blocked",
                                        "last_fetched_at", "is_demo")} for x in db.scalars(select(Source))]


@app.post("/api/sources")
def add_source(body: SourceIn, db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    src = Source(**body.model_dump(), kind="rss" if body.feed_url else "manual")
    db.add(src)
    audit(db, actor.name, "source.add", "source", None, domain=body.domain)
    db.commit()
    return {"id": src.id}


@app.patch("/api/sources/{sid}")
def patch_source(sid: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    src = db.get(Source, sid)
    if src is None:
        raise HTTPException(404)
    for k in ("allowed", "blocked", "trust_tier", "feed_url", "name"):
        if k in body:
            setattr(src, k, body[k])
    audit(db, actor.name, "source.update", "source", sid, **{k: str(v) for k, v in body.items()})
    db.commit()
    return {"ok": True}


@app.post("/api/sources/scan")
def scan(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..providers import get_writer
    from ..services.ingest import run_scan

    s = get_settings()
    if s.is_demo:
        from ..demo import seed_demo

        seed_demo(db)
    stats = run_scan(db, writer=get_writer(), actor=actor.name)
    db.commit()
    return stats


@app.get("/api/news")
def news(pillar: str | None = None, status: str | None = None, db: Session = Depends(get_db),
         actor: Actor = Depends(current_actor)):
    q = select(NewsItem).order_by(NewsItem.score.desc(), NewsItem.fetched_at.desc())
    if pillar:
        q = q.where(NewsItem.pillar == pillar)
    if status:
        q = q.where(NewsItem.status == status)
    used = {n for (n,) in db.execute(select(Story.news_item_id))}
    return [ser_news(n) | {"has_story": n.id in used} for n in db.scalars(q.limit(200))]


@app.patch("/api/news/{nid}")
def patch_news(nid: str, body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    n = db.get(NewsItem, nid)
    if not n:
        raise HTTPException(404)
    if body.get("status") in ("dismissed", "research", "shortlist", "reference"):
        if body["status"] == "shortlist" and any(c.status in ("unverified", "contested") and c.is_key for c in n.claims):
            raise HTTPException(409, "Còn claim trọng yếu chưa xác minh — không thể đưa vào shortlist")
        n.status = body["status"]
    audit(db, actor.name, "news.update", "news_item", nid, status=body.get("status"))
    db.commit()
    return ser_news(n)


class ClaimPatch(BaseModel):
    status: Literal["verified", "unverified", "removed", "contested", "inference"] | None = None
    source_url: str | None = None
    editor_note: str | None = None


@app.patch("/api/claims/{cid}")
def patch_claim(cid: str, body: ClaimPatch, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    c = db.get(Claim, cid)
    if not c:
        raise HTTPException(404)
    if body.status == "verified":
        url = body.source_url or c.source_url
        if not url or not url.startswith("http"):
            raise HTTPException(409, "Xác minh thủ công cần source_url hợp lệ")
        if not body.editor_note:
            raise HTTPException(409, "Ghi chú lý do xác minh (editor_note) là bắt buộc")
        c.source_url = url
        c.verification_reason = f"Biên tập viên {actor.name} xác minh thủ công: {body.editor_note}"
    if body.status:
        c.status = body.status
    if body.editor_note:
        c.editor_note = body.editor_note
    audit(db, actor.name, "claim.update", "claim", cid, status=c.status, source_url=c.source_url or "")
    db.commit()
    return ser_claim(c)


# ------------------------------------------------------------------ stories

class StoryIn(BaseModel):
    news_item_id: str
    pillar: Literal["P1", "P2", "P3"] | None = None
    core_question: str | None = None
    editorial_notes: str = ""


@app.post("/api/stories")
def create_story(body: StoryIn, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    n = db.get(NewsItem, body.news_item_id)
    if not n:
        raise HTTPException(404, "Không tìm thấy tin")
    if n.status in ("blocked", "duplicate"):
        raise HTTPException(409, f"Tin đang ở trạng thái {n.status}: {n.block_reason or 'trùng'} — không sản xuất")
    ch = db.scalar(select(Channel).where(Channel.selected.is_(True)))
    st = Story(news_item_id=n.id, pillar=body.pillar or n.pillar or "P1", topic=n.title.replace("[DEMO] ", ""),
               core_question=body.core_question or "", editorial_notes=body.editorial_notes, mode="MANUAL",
               channel_id=ch.id if ch else None, is_demo=n.is_demo)
    db.add(st)
    audit(db, actor.name, "story.create", "story", None, news=n.id)
    db.commit()
    return ser_story(st)


@app.get("/api/stories")
def list_stories(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..workers.executor import is_running

    return [ser_story(s) | {"running": is_running(s.id)} for s in db.scalars(select(Story).order_by(Story.created_at.desc()).limit(200))]


@app.get("/api/stories/{sid}")
def get_story_detail(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    return story_detail(db, get_story(db, sid))


def _submit(st: Story, actor: Actor, stop_at: str | None = None) -> dict:
    from ..workers.executor import submit_pipeline

    return {"story_id": st.id, "submitted": submit_pipeline(st.id, actor.name, stop_at), "state": st.state}


@app.post("/api/stories/{sid}/run")
def run_story(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    """Chạy tiếp tới điểm dừng kế tiếp (chờ duyệt script / chờ bấm upload / hoàn tất)."""
    return _submit(get_story(db, sid), actor)


@app.post("/api/stories/{sid}/research")
def research(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    st = get_story(db, sid)
    if st.state not in (S.DISCOVERED.value, S.RESEARCHING.value, S.FACT_CHECK_PENDING.value, S.NEEDS_REVIEW.value):
        raise HTTPException(409, f"Không thể research ở trạng thái {st.state}")
    return _submit(st, actor, stop_at=S.SCRIPT_DRAFT.value)


@app.post("/api/stories/{sid}/generate-script")
def generate_script(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..providers import Providers
    from ..workers.jobs import script_job

    st = get_story(db, sid)
    if st.state != S.SCRIPT_DRAFT.value:
        if st.state in (S.DISCOVERED.value, S.RESEARCHING.value, S.FACT_CHECK_PENDING.value):
            return _submit(st, actor, stop_at=S.SCRIPT_APPROVED_FOR_RENDER.value)
        raise HTTPException(409, f"Sinh script mới chỉ ở SCRIPT_DRAFT (hiện {st.state})")
    try:
        res = script_job(db, st, Providers())
    except GateBlocked as e:
        db.commit()
        return {"qa_passed": False, "reasons": e.reasons, "story": ser_story(st)}
    db.commit()
    return {"qa_passed": True, **res}


class ScriptPatch(BaseModel):
    spoken_script: str | None = None
    selected_title_index: int | None = Field(None, ge=0, le=2)
    title_options: list[dict] | None = None
    rewrite_paragraph_index: int | None = None
    rewrite_instruction: str | None = None
    change_note: str = "Biên tập viên chỉnh sửa"


@app.patch("/api/scripts/{svid}")
def patch_script(svid: str, body: ScriptPatch, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    """Mọi chỉnh sửa tạo version mới (immutable). Story quay về SCRIPT_DRAFT, cần duyệt lại."""
    from ..providers import Providers
    from ..services.script_qa import paragraphs
    from ..workers.jobs import latest_pack, save_script_version
    from ..workers.orchestrator import transition

    sv = db.get(ScriptVersion, svid)
    if not sv:
        raise HTTPException(404)
    st = db.get(Story, sv.story_id)
    if st.state in {x.value for x in (S.UPLOADING, S.UPLOADED_PRIVATE, S.TELEGRAM_NOTIFIED, S.EDITOR_REVIEW, S.APPROVED_PRIVATE,
                                      S.REVISION_REQUESTED, S.REJECTED, S.PUBLISHED_MANUALLY_OBSERVED)}:
        raise HTTPException(409, "Video đã upload private — hãy dùng 'Yêu cầu sửa' để tạo phiên bản mới")
    content = dict(sv.content)
    if body.rewrite_paragraph_index is not None:
        paras = paragraphs(content["spoken_script"])
        if not 0 <= body.rewrite_paragraph_index < len(paras):
            raise HTTPException(400, "Chỉ số đoạn không hợp lệ")
        w = Providers().get_writer()
        rp = latest_pack(db, st)
        new_p, _ = w.rewrite_paragraph({"pillar": st.pillar, "research_pack": rp.content, "core_question": st.core_question},
                                       paras[body.rewrite_paragraph_index], body.rewrite_instruction or "viết gọn hơn")
        teaser = content.get("closing_teaser", "")
        paras[body.rewrite_paragraph_index] = new_p
        content["spoken_script"] = "\n\n".join(paras)
        if teaser and not content["spoken_script"].rstrip().endswith(teaser):
            content["closing_teaser"] = paras[-1].split(". ")[-1]
    if body.spoken_script is not None:
        content["spoken_script"] = body.spoken_script
    if body.title_options is not None:
        content["title_options"] = body.title_options
    if body.selected_title_index is not None:
        content["selected_title_index"] = body.selected_title_index
    if st.state != S.SCRIPT_DRAFT.value:
        transition(db, st, S.SCRIPT_DRAFT, actor.name, "Chỉnh sửa kịch bản")
    st.script_approved_by = None
    try:
        res = save_script_version(db, st, content, created_by=actor.name, model=None, is_demo=sv.is_demo, change_note=body.change_note)
        ok, reasons = True, []
    except GateBlocked as e:
        ok, reasons, res = False, e.reasons, {}
    audit(db, actor.name, "script.edit", "script_version", st.current_script_version_id)
    db.commit()
    return {"qa_passed": ok, "reasons": reasons, "script_version_id": st.current_script_version_id, **res}


@app.post("/api/scripts/{svid}/approve")
def approve_script(svid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..workers.orchestrator import transition

    sv = db.get(ScriptVersion, svid)
    if not sv:
        raise HTTPException(404)
    st = db.get(Story, sv.story_id)
    if st.current_script_version_id != sv.id:
        raise HTTPException(409, "Chỉ duyệt được bản hiện hành")
    if not sv.qa_passed:
        raise HTTPException(409, "Script QA chưa đạt")
    if st.state == S.NEEDS_REVIEW.value:
        transition(db, st, S.SCRIPT_DRAFT, actor.name, "Biên tập viên xử lý")
    st.script_approved_by = actor.name
    audit(db, actor.name, "script.approve", "script_version", sv.id)
    db.commit()
    return _submit(st, actor)


@app.post("/api/stories/{sid}/tts")
def tts(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    st = get_story(db, sid)
    if st.state not in (S.SCRIPT_APPROVED_FOR_RENDER.value, S.SCRIPT_DRAFT.value, S.NEEDS_REVIEW.value, S.FAILED_RETRYABLE.value):
        raise HTTPException(409, f"TTS không khả dụng ở {st.state}")
    return _submit(st, actor, stop_at=S.TTS_DONE.value)


@app.post("/api/stories/{sid}/tts/sample")
def tts_sample(sid: str, voice: str | None = None, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..providers import get_tts
    from ..services.tts.base import chunk_script
    from ..services.tts.pipeline import make_sample
    from ..workers.jobs import current_script, get_setting, story_dir

    s = get_settings()
    st = get_story(db, sid)
    sv = current_script(db, st)
    v = voice or get_setting(db, "voice", s.vieneu_voice)
    out = story_dir(st) / "sample_30s.wav"
    m = make_sample(chunk_script(sv.spoken_script), get_tts(), v, out, s.artifacts_dir / "_tts_cache")
    return {"duration": m.duration, "voice": v, "url": media_url(st.id, "sample")}


@app.get("/api/voices")
def voices(actor: Actor = Depends(current_actor)):
    from ..providers import get_tts

    try:
        t = get_tts()
        return {"provider": t.name, "is_demo": t.is_demo, "voices": t.list_voices()}
    except BlockedByCredentials as e:
        from ..services.tts.adapters import VIENEU_V3TURBO_PRESETS

        return {"provider": "unavailable", "error": str(e), "voices": [{"label": v, "voice_id": v} for v in VIENEU_V3TURBO_PRESETS]}


@app.post("/api/stories/{sid}/assets/search")
def assets_search(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..providers import Providers
    from ..workers.jobs import media_job

    st = get_story(db, sid)
    res = media_job(db, st, Providers())
    db.commit()
    return res


class AssetPatch(BaseModel):
    review_status: Literal["approved", "rejected"]
    license_name: str | None = None
    license_url: str | None = None
    allowed_commercial: bool | None = None
    attribution_text: str | None = None
    permission_proof: str | None = None
    note: str | None = None


@app.patch("/api/assets/{aid}")
def patch_asset(aid: str, body: AssetPatch, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..models import AssetLicense
    from ..services.media.license_gate import evaluate

    a = db.get(Asset, aid)
    if not a:
        raise HTTPException(404)
    lic = a.license or AssetLicense()
    for k in ("license_name", "license_url", "allowed_commercial", "attribution_text", "permission_proof"):
        if getattr(body, k) is not None:
            setattr(lic, k, getattr(body, k))
    a.license = lic
    if body.review_status == "approved":
        dec = evaluate({"provider": a.provider, "license_name": lic.license_name, "allowed_commercial": lic.allowed_commercial,
                        "attribution_required": lic.attribution_required, "attribution_text": lic.attribution_text,
                        "permission_proof": lic.permission_proof, "source_url": a.source_url})
        if dec.status != "approved":
            raise HTTPException(409, f"Không thể duyệt: {dec.reason}")
    a.review_status = body.review_status
    a.rejection_reason = body.note if body.review_status == "rejected" else None
    audit(db, actor.name, f"asset.{body.review_status}", "asset", aid, note=body.note or "")
    db.commit()
    return {"ok": True, "review_status": a.review_status}


@app.post("/api/stories/{sid}/render")
def render(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    st = get_story(db, sid)
    if st.state not in (S.TTS_DONE.value, S.ASSETS_READY.value, S.RENDERING.value, S.NEEDS_REVIEW.value, S.FAILED_RETRYABLE.value,
                        S.FAILED_PERMANENT.value):
        raise HTTPException(409, f"Render không khả dụng ở {st.state}")
    return _submit(st, actor, stop_at=S.QA_PENDING.value)


@app.post("/api/stories/{sid}/qa")
def qa(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    st = get_story(db, sid)
    if st.state not in (S.QA_PENDING.value, S.NEEDS_REVIEW.value):
        raise HTTPException(409, f"QA không khả dụng ở {st.state}")
    return _submit(st, actor, stop_at=S.READY_TO_UPLOAD.value)


@app.post("/api/stories/{sid}/upload-private")
def upload_private(sid: str, confirm_private: bool = Body(..., embed=True), db: Session = Depends(get_db),
                   actor: Actor = Depends(require_owner)):
    """Tải lên YouTube ở trạng thái RIÊNG TƯ (idempotent). Không có tham số privacy — luôn 'private'."""
    from ..workers.jobs import upload_gate

    if confirm_private is not True:
        raise HTTPException(400, "Phải xác nhận upload ở chế độ RIÊNG TƯ")
    st = get_story(db, sid)
    if st.state in {x.value for x in (S.UPLOADED_PRIVATE, S.TELEGRAM_NOTIFIED, S.EDITOR_REVIEW, S.APPROVED_PRIVATE)}:
        up = db.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id))
        return {"already_uploaded": True, "youtube_video_id": up.youtube_video_id if up else None, "privacy": "private"}
    st.upload_requested_by = actor.name
    if st.state == S.READY_TO_UPLOAD.value:
        reasons = upload_gate(db, st)
        reasons = [r for r in reasons if not r.startswith("freshness_check")]  # pipeline sẽ tự chạy lại freshness
        if reasons:
            db.rollback()
            raise HTTPException(409, {"message": "Gate chặn upload", "reasons": reasons})
    audit(db, actor.name, "upload_private.requested", "story", sid)
    db.commit()
    ch = db.get(Channel, st.channel_id) if st.channel_id else None
    return _submit(st, actor) | {"privacy": "private", "channel": ch.title if ch else None}


@app.post("/api/stories/{sid}/review")
def review(sid: str, action: Literal["approve", "request_revision", "reject"] = Body(..., embed=True),
           note: str | None = Body(None, embed=True), db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    """Duyệt/đòi sửa/loại — KHÔNG thay đổi privacy trên YouTube."""
    from ..workers.jobs import apply_review

    st = get_story(db, sid)
    res = apply_review(db, st, action, actor.name, "dashboard", note)
    db.commit()
    return res


@app.post("/api/stories/{sid}/refresh-status")
def refresh_status(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from ..providers import Providers
    from ..workers.jobs import refresh_status_job

    st = get_story(db, sid)
    res = refresh_status_job(db, st, Providers())
    db.commit()
    return res | {"state": st.state}


@app.get("/api/stories/{sid}/preview")
def preview(sid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    st = get_story(db, sid)
    if not st.current_render_id:
        raise HTTPException(404, "Chưa có bản dựng")
    return {"preview_url": media_url(sid, "preview"), "video_url": media_url(sid, "video"),
            "review_link": f"/api/review/{sid}?t={sign_link('review', sid)}"}


# ------------------------------------------------------------------ media (signed)

def _media_path(db: Session, st: Story, kind: str) -> Path:
    from ..workers.jobs import story_dir

    r = db.get(Render, st.current_render_id) if st.current_render_id else None
    sv = db.get(ScriptVersion, st.current_script_version_id) if st.current_script_version_id else None
    if kind == "video" and r:
        return Path(r.path)
    if kind == "preview" and r:
        return Path(r.preview_path)
    if kind == "srt" and r:
        return Path(r.subtitle_srt_path)
    if kind.startswith("thumbnail_") and r:
        return Path(r.thumbnail_paths[int(kind.split("_")[1])])
    if kind == "audio" and sv:
        return story_dir(st) / f"audio_v{sv.version}" / "narration.wav"
    if kind == "sample":
        return story_dir(st) / "sample_30s.wav"
    if kind.startswith("segment_") and sv:
        t = db.scalar(select(TTSSegment).where(TTSSegment.script_version_id == sv.id, TTSSegment.idx == int(kind.split("_")[1])))
        if t:
            return Path(t.path)
    if kind.startswith("scene_") and sv:
        sc = db.scalar(select(Scene).where(Scene.story_id == st.id, Scene.script_version_id == sv.id, Scene.idx == int(kind.split("_")[1])))
        if sc:
            return Path(sc.image_path)
    raise HTTPException(404)


@app.get("/api/media/{sid}/{kind}")
def media(sid: str, kind: str, t: str = Query(...), db: Session = Depends(get_db)):
    if not verify_link("media", f"{sid}/{kind}", t):
        raise HTTPException(403, "Link hết hạn hoặc không hợp lệ")
    p = _media_path(db, get_story(db, sid), kind)
    root = get_settings().artifacts_dir.resolve()
    if not p.exists() or root not in p.resolve().parents:
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/review/{sid}", response_class=HTMLResponse)
def review_page(sid: str, t: str = Query(...), db: Session = Depends(get_db)):
    """Trang preview có ký (link gửi Telegram). Chỉ xem — không có nút công khai."""
    if not verify_link("review", sid, t):
        raise HTTPException(403, "Link xem trước hết hạn hoặc không hợp lệ")
    d = story_detail(db, get_story(db, sid))
    e = html.escape
    claims = "".join(f"<li><b>{e(c['claim_key'])}</b> [{e(c['status'])}] {e(c['text'])} — <a href='{e(c['source_url'] or '#')}'>{e(c['source_url'] or 'thiếu nguồn')}</a></li>"
                     for c in (d["news"] or {}).get("claims", []))
    seo_ = d.get("seo") or {}
    title = seo_.get("title_options", [{}])[seo_.get("selected_index", 0)].get("title", d["topic"]) if seo_ else d["topic"]
    up = (d["uploads"] or [{}])[0]
    demo = "<p style='color:#e74c3c'><b>DEMO — dữ liệu giả lập, không có upload thật</b></p>" if d["is_demo"] else ""
    vid = f"<video controls width='100%' src='{e(d['render']['preview_url'])}'></video>" if d.get("render") else ""
    return f"""<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Xem trước: {e(title)}</title><body style="font-family:sans-serif;max-width:900px;margin:auto;padding:16px;background:#0c1421;color:#eef">
{demo}<h2>{e(title)}</h2><p>Trạng thái: <b>{e(d['state'])}</b> · YouTube: <b>RIÊNG TƯ</b> · ID: {e(str(up.get('youtube_video_id')))}</p>
{vid}<h3>Mô tả</h3><pre style="white-space:pre-wrap">{e(seo_.get('description', ''))}</pre><h3>Claim & nguồn</h3><ul>{claims}</ul>
<p>Việc công khai chỉ thực hiện thủ công trên YouTube Studio.</p></body></html>"""


# ------------------------------------------------------------------ jobs, settings, channels

@app.get("/api/jobs/{jid}")
def job(jid: str, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    j = db.get(Job, jid)
    if not j:
        raise HTTPException(404)
    return {k: getattr(j, k) for k in ("id", "story_id", "job_type", "idempotency_key", "status", "attempts", "max_attempts",
                                       "output", "error", "history", "started_at", "finished_at", "duration_seconds")}


@app.get("/api/jobs")
def jobs(story_id: str | None = None, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    q = select(Job).order_by(Job.created_at.desc()).limit(200)
    if story_id:
        q = q.where(Job.story_id == story_id)
    return [{k: getattr(j, k) for k in ("id", "story_id", "job_type", "status", "attempts", "error", "created_at", "duration_seconds")}
            for j in db.scalars(q)]


SETTING_KEYS = {"autopilot_enabled": False, "scan_schedule_enabled": False, "voice": None}


@app.get("/api/settings")
def get_settings_api(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    s = get_settings()
    vals = {k: (db.get(AppSetting, k).value if db.get(AppSetting, k) else d) for k, d in SETTING_KEYS.items()}
    vals["voice"] = vals["voice"] or s.vieneu_voice
    return vals | {"mode": s.app_mode, "scan_interval_hours": s.scan_interval_hours, "max_videos_per_week": s.max_videos_per_week,
                   "score_autopilot": s.score_autopilot, "duration_range": [s.duration_min_seconds, s.duration_max_seconds],
                   "privacy": "private (cố định)"}


@app.patch("/api/settings")
def patch_settings(body: dict = Body(...), db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    from ..workers.jobs import set_setting

    for k, v in body.items():
        if k not in SETTING_KEYS:
            raise HTTPException(400, f"Không hỗ trợ thiết lập {k}")
        if k == "autopilot_enabled" and v:
            ch = db.scalar(select(Channel).where(Channel.selected.is_(True)))
            if not ch:
                raise HTTPException(409, "Chọn kênh đích trước khi bật Autopilot Private")
        set_setting(db, k, v)
        audit(db, actor.name, "settings.update", "settings", k, value=str(v))
    db.commit()
    return get_settings_api(db, actor)


@app.post("/api/autopilot/tick")
def autopilot_now(db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    from ..workers.executor import submit_callable
    from ..workers.jobs import autopilot_tick

    def run():
        from ..db import SessionLocal

        with SessionLocal() as s2:
            return autopilot_tick(s2)

    return {"submitted": submit_callable("autopilot", run)}


@app.get("/api/settings/channels")
def channels(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    return [{"id": c.id, "youtube_channel_id": c.youtube_channel_id, "title": c.title, "selected": c.selected, "is_demo": c.is_demo,
             "scopes": c.scopes, "connected": bool(c.oauth_token_encrypted) or c.is_demo, "token_refreshed_at": c.token_refreshed_at}
            for c in db.scalars(select(Channel))]


@app.post("/api/settings/channels/{cid}/select")
def select_channel(cid: str, db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    s = get_settings()
    target = db.get(Channel, cid)
    if not target:
        raise HTTPException(404)
    if s.youtube_expected_channel_id and target.youtube_channel_id != s.youtube_expected_channel_id:
        raise HTTPException(409, "Kênh khác YOUTUBE_EXPECTED_CHANNEL_ID")
    for c in db.scalars(select(Channel)):
        c.selected = c.id == cid
    audit(db, actor.name, "channel.select", "channel", cid)
    db.commit()
    return {"ok": True}


@app.get("/api/oauth/google/start")
def oauth_start(actor: Actor = Depends(require_owner)):
    from ..services.youtube.client import make_oauth_flow

    state = sign_link("oauth", "google", 900)
    flow = make_oauth_flow(get_settings(), state)
    url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent select_account")
    return {"authorization_url": url}


@app.get("/api/oauth/google/callback")
def oauth_callback(state: str, code: str, db: Session = Depends(get_db)):
    from ..services.youtube.client import GoogleYouTubeClient, make_oauth_flow

    s = get_settings()
    if not verify_link("oauth", "google", state):
        raise HTTPException(403, "OAuth state không hợp lệ/hết hạn (CSRF)")
    flow = make_oauth_flow(s, state)
    flow.fetch_token(code=code)
    creds = flow.credentials
    me = GoogleYouTubeClient(creds).get_my_channel()
    if s.youtube_expected_channel_id and me["id"] != s.youtube_expected_channel_id:
        raise HTTPException(409, f"Bạn đã chọn kênh '{me['title']}' nhưng kênh đích cấu hình là {s.youtube_expected_channel_id}")
    ch = db.scalar(select(Channel).where(Channel.youtube_channel_id == me["id"])) or Channel(youtube_channel_id=me["id"], title=me["title"])
    ch.title, ch.oauth_token_encrypted, ch.scopes, ch.token_refreshed_at = me["title"], encrypt_str(creds.to_json()), list(creds.scopes or []), now()
    db.add(ch)
    audit(db, "owner", "channel.oauth_connected", "channel", me["id"], title=me["title"])
    db.commit()
    return RedirectResponse(f"{s.public_base_url.rstrip('/')}/settings/?connected={me['id']}")


@app.post("/api/telegram/webhook")
def telegram_webhook(update: dict = Body(...), x_telegram_bot_api_secret_token: str | None = Header(default=None),
                     db: Session = Depends(get_db)):
    import hmac as _h

    from ..providers import get_telegram
    from ..services.telegram.callbacks import handle_callback

    s = get_settings()
    if not s.telegram_webhook_secret or not x_telegram_bot_api_secret_token or not _h.compare_digest(
            x_telegram_bot_api_secret_token, s.telegram_webhook_secret.get_secret_value()):
        raise HTTPException(403)
    if "callback_query" in update:
        return handle_callback(db, update["callback_query"], get_telegram())
    return {"ok": True}


@app.get("/api/costs")
def costs(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    rows = db.execute(select(CostUsage.service, CostUsage.unit_type, func.sum(CostUsage.units), func.sum(CostUsage.estimated_usd),
                             func.max(CostUsage.is_demo)).group_by(CostUsage.service, CostUsage.unit_type)).all()
    per_story = db.execute(select(CostUsage.story_id, func.sum(CostUsage.estimated_usd)).group_by(CostUsage.story_id)).all()
    jobs_t = db.execute(select(Job.job_type, func.count(Job.id), func.sum(Job.duration_seconds)).group_by(Job.job_type)).all()
    return {"by_service": [{"service": a, "unit_type": b, "units": c, "usd": round(d or 0, 4), "is_demo": bool(e)} for a, b, c, d, e in rows],
            "by_story": [{"story_id": a, "usd": round(b or 0, 4)} for a, b in per_story],
            "compute": [{"job_type": a, "runs": b, "seconds": round(c or 0, 1)} for a, b, c in jobs_t]}


@app.get("/api/analytics")
def analytics(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    s = get_settings()
    ups = list(db.scalars(select(YouTubeUpload).where(YouTubeUpload.youtube_video_id.is_not(None))))
    published = [u for u in ups if u.privacy_status_observed == "public"]
    note = ("DEMO: không có YouTube Analytics thật." if s.is_demo else
            "Cần cấp thêm scope yt-analytics.readonly để đọc CTR/retention — BLOCKED_BY_CREDENTIALS nếu chưa cấp.")
    return {"read_only": True, "note": note, "uploaded_private": len(ups), "observed_public_by_owner": len(published),
            "pillar_mix": [{"pillar": p, "count": c} for p, c in db.execute(select(Story.pillar, func.count(Story.id)).group_by(Story.pillar))],
            "metrics": None}


@app.get("/api/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    return [{"at": a.at, "actor": a.actor, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id, "details": a.details}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)))]


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    by_state = dict(db.execute(select(Story.state, func.count(Story.id)).group_by(Story.state)).all())
    news_by = {}
    for p in ("P1", "P2", "P3"):
        news_by[p] = [ser_news(n, False) for n in db.scalars(select(NewsItem).where(NewsItem.pillar == p, NewsItem.status == "shortlist")
                                                              .order_by(NewsItem.score.desc()).limit(5))]
    cost = db.scalar(select(func.sum(CostUsage.estimated_usd))) or 0
    ch = db.scalar(select(Channel).where(Channel.selected.is_(True)))
    alerts = [{"story_id": s.id, "topic": s.topic, "blockers": s.blockers, "state": s.state}
              for s in db.scalars(select(Story).order_by(Story.updated_at.desc()).limit(50)) if s.blockers][:10]
    open_errors = dict(db.execute(select(ErrorLog.level, func.count(ErrorLog.id)).where(ErrorLog.resolved.is_(False))
                                  .group_by(ErrorLog.level)).all())
    return {"by_state": by_state, "shortlist": news_by, "cost_usd": round(cost, 4), "channel": ch.title if ch else None,
            "settings": get_settings_api(db, actor), "alerts": alerts, "mode": get_settings().app_mode,
            "open_errors": {"total": sum(open_errors.values()), **open_errors}}


# ------------------------------------------------------------------ nhật ký lỗi (màn hình "Nhật ký lỗi")

def ser_error(e: ErrorLog, full: bool = False) -> dict:
    from ..error_log import SOURCE_LABEL, hint_for

    d = {k: getattr(e, k) for k in ("id", "level", "source", "logger", "message", "exc_type", "story_id", "job_id", "job_type",
                                    "count", "first_seen_at", "last_seen_at", "resolved", "resolved_at", "resolved_by",
                                    "resolution_note")}
    d["source_label"] = SOURCE_LABEL.get(e.source, e.source)
    d["hint"] = hint_for(e.exc_type, e.message, e.source)
    d["has_traceback"] = bool(e.traceback)
    if full:
        d["traceback"] = e.traceback
        d["details"] = e.details
    return d


def _error_filters(level: str | None, source: str | None, status: str, q: str | None, story_id: str | None,
                   since_hours: float | None) -> list:
    from datetime import timedelta

    conds = []
    if level:
        conds.append(ErrorLog.level.in_([x for x in level.upper().split(",") if x]))
    if source:
        conds.append(ErrorLog.source.in_([x for x in source.split(",") if x]))
    if status == "open":
        conds.append(ErrorLog.resolved.is_(False))
    elif status == "resolved":
        conds.append(ErrorLog.resolved.is_(True))
    if story_id:
        conds.append(ErrorLog.story_id == story_id)
    if since_hours:
        conds.append(ErrorLog.last_seen_at >= now() - timedelta(hours=since_hours))
    if q:
        like = f"%{q.strip()}%"
        conds.append(ErrorLog.message.ilike(like) | ErrorLog.exc_type.ilike(like) | ErrorLog.job_type.ilike(like)
                     | ErrorLog.story_id.ilike(like))
    return conds


@app.get("/api/errors")
def list_errors(level: str | None = None, source: str | None = None, status: Literal["open", "resolved", "all"] = "open",
                q: str | None = None, story_id: str | None = None, since_hours: float | None = None,
                limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from datetime import timedelta

    from ..error_log import SOURCE_LABEL, flush_pending

    flush_pending(2.0)  # lỗi vừa xảy ra (đang trong hàng đợi) cũng hiện ngay
    conds = _error_filters(level, source, status, q, story_id, since_hours)
    total = db.scalar(select(func.count(ErrorLog.id)).where(*conds)) or 0
    rows = list(db.scalars(select(ErrorLog).where(*conds).order_by(ErrorLog.last_seen_at.desc(), ErrorLog.id.desc())
                           .offset(offset).limit(limit)))
    topics = dict(db.execute(select(Story.id, Story.topic).where(Story.id.in_(list({r.story_id for r in rows if r.story_id})))).all())
    items = [ser_error(r) | {"story_topic": topics.get(r.story_id)} for r in rows]
    open_ = ErrorLog.resolved.is_(False)
    summary = {
        "open": db.scalar(select(func.count(ErrorLog.id)).where(open_)) or 0,
        "open_critical": db.scalar(select(func.count(ErrorLog.id)).where(open_, ErrorLog.level.in_(["ERROR", "CRITICAL"]))) or 0,
        "last_24h": db.scalar(select(func.coalesce(func.sum(ErrorLog.count), 0))
                              .where(ErrorLog.last_seen_at >= now() - timedelta(hours=24))) or 0,
        "resolved": db.scalar(select(func.count(ErrorLog.id)).where(ErrorLog.resolved.is_(True))) or 0,
        "by_source": [{"source": s_, "label": SOURCE_LABEL.get(s_, s_), "count": n}
                      for s_, n in db.execute(select(ErrorLog.source, func.count(ErrorLog.id)).where(open_)
                                              .group_by(ErrorLog.source).order_by(func.count(ErrorLog.id).desc())).all()],
    }
    return {"items": items, "total": total, "limit": limit, "offset": offset, "summary": summary,
            "sources": [{"source": k, "label": v} for k, v in SOURCE_LABEL.items()]}


@app.get("/api/errors/{eid}")
def get_error(eid: int, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    e = db.get(ErrorLog, eid)
    if e is None:
        raise HTTPException(404, "Không tìm thấy lỗi")
    out = ser_error(e, full=True)
    st = db.get(Story, e.story_id) if e.story_id else None
    out["story"] = {"id": st.id, "topic": st.topic, "state": st.state, "last_error": st.last_error, "blockers": st.blockers} if st else None
    j = db.get(Job, e.job_id) if e.job_id else None
    out["job"] = {"id": j.id, "job_type": j.job_type, "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts,
                  "history": j.history, "duration_seconds": j.duration_seconds} if j else None
    return out


class ErrorResolve(BaseModel):
    resolved: bool = True
    note: str | None = Field(default=None, max_length=2000)


@app.post("/api/errors/{eid}/resolve")
def resolve_error(eid: int, body: ErrorResolve, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    e = db.get(ErrorLog, eid)
    if e is None:
        raise HTTPException(404, "Không tìm thấy lỗi")
    e.resolved = body.resolved
    e.resolved_at, e.resolved_by = (now(), actor.name) if body.resolved else (None, None)
    e.resolution_note = body.note if body.resolved else None
    audit(db, actor.name, "error.resolve" if body.resolved else "error.reopen", "error_log", str(e.id), note=body.note or "")
    db.commit()
    return ser_error(e)


class ErrorBulk(BaseModel):
    ids: list[int] | None = None  # None = mọi lỗi đang mở khớp bộ lọc
    level: str | None = None
    source: str | None = None
    q: str | None = None
    story_id: str | None = None
    note: str | None = Field(default=None, max_length=2000)


@app.post("/api/errors/resolve-bulk")
def resolve_errors_bulk(body: ErrorBulk, db: Session = Depends(get_db), actor: Actor = Depends(current_actor)):
    from sqlalchemy import update as sa_update

    conds = _error_filters(body.level, body.source, "open", body.q, body.story_id, None)
    if body.ids is not None:
        conds.append(ErrorLog.id.in_(body.ids))
    n = db.execute(sa_update(ErrorLog).where(*conds).values(resolved=True, resolved_at=now(), resolved_by=actor.name,
                                                            resolution_note=body.note)
                   .execution_options(synchronize_session=False)).rowcount
    audit(db, actor.name, "error.resolve_bulk", "error_log", None, count=n, source=body.source or "", level=body.level or "")
    db.commit()
    return {"resolved": n}


@app.delete("/api/errors")
def purge_errors(older_than_days: float = Query(0, ge=0), db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    """Xoá các lỗi ĐÃ XỬ LÝ (tuỳ chọn: chỉ những lỗi cũ hơn N ngày). Lỗi đang mở không bao giờ bị xoá ở đây."""
    from datetime import timedelta

    from sqlalchemy import delete as sa_delete

    conds = [ErrorLog.resolved.is_(True)]
    if older_than_days:
        conds.append(ErrorLog.last_seen_at < now() - timedelta(days=older_than_days))
    n = db.execute(sa_delete(ErrorLog).where(*conds)).rowcount
    audit(db, actor.name, "error.purge", "error_log", None, count=n, older_than_days=older_than_days)
    db.commit()
    return {"deleted": n}


# ------------------------------------------------------------------ cấu hình API (trang Thiết lập)

@app.get("/api/config")
def config_get(request: Request, db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    from ..runtime_config import describe

    return describe(db) | {"local_access": is_trusted_local(request)}


class ConfigUpdate(BaseModel):
    values: dict = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)


@app.put("/api/config")
def config_put(body: ConfigUpdate, db: Session = Depends(get_db), actor: Actor = Depends(require_owner)):
    """Lưu cấu hình. Trường bí mật để trống = giữ nguyên. Không có trường nào điều khiển privacy (luôn private)."""
    from ..runtime_config import describe, save

    try:
        res = save(db, body.values, body.clear, actor.name)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    return {"updated": res.updated, "cleared": res.cleared, "restart_needed": res.restart_needed, **describe(db)}


@app.post("/api/config/test/{service}")
def config_test(service: str, send_test: bool = False, actor: Actor = Depends(require_owner)):
    from ..config import reset_settings_cache
    from ..services.connection_tests import TESTS

    if service not in TESTS:
        raise HTTPException(404, "Dịch vụ không hỗ trợ kiểm tra")
    reset_settings_cache()
    s = get_settings()
    if service == "telegram":
        return TESTS[service](s, send_test=send_test)
    return TESTS[service](s)


# ------------------------------------------------------------------ giao diện web (Next.js static export)

WEB_DIST = Path(__file__).resolve().parents[1] / "web_dist"
if WEB_DIST.is_dir() and (WEB_DIST / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def no_web():
        return ("<h3>EV Creator Studio API đang chạy.</h3><p>Chưa có giao diện web đã build "
                "(backend/ev_studio/web_dist). Chạy <code>python run.py --build-web</code> hoặc xem <a href='/docs'>/docs</a>.</p>")
