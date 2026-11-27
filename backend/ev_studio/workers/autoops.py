"""Chạy mỗi 10 phút. Gồm tự xử lý lỗi (`auto_heal`, mặc định bật, cho MỌI video) và Tự động hoàn toàn (`full_auto`).

1. Tự xử lý lỗi (chủ kênh 27/11/2026: "gặp lỗi các bạn tự xử lý cho tôi, không thể xử lý hãy để tôi xem"):
   video dừng (NEEDS_REVIEW / FAILED_*) → app chẩn đoán và tự sửa theo loại lỗi, tối đa `auto_heal_max_attempts` lần:
   - kịch bản trượt kiểm tra → bỏ qua kiểm tra (nếu bật) hoặc viết kịch bản mới;
   - video dưới 10 phút → quay lại bước kịch bản, viết bản mới dài hơn;
   - hết ngân sách → đợi ngân sách ngày mới rồi chạy tiếp;
   - lỗi mạng/dịch vụ bận/lỗi khác → chạy lại.
   Không tự xử lý được (thiếu key/kết nối, hoặc đã thử đủ số lần) → GIỮ video, ghi "🔎 Cần bạn xem" + lý do, nhắn Telegram
   một lần. Không bao giờ tự bỏ video.
2. Autopilot: mở tin mới nếu còn suất trong ngày và còn ngân sách (thay cho việc chờ lượt quét 3 giờ).
3. Tự công khai: video đã tải RIÊNG TƯ, Telegram đã báo kèm nút Dừng, hết thời gian chờ, không bị Dừng, kiểm chứng lại vẫn ổn
   → công khai (services/youtube/publish.py) và đọc lại. YouTube giữ riêng tư (project API chưa audit) → dừng, báo chủ kênh.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..config import get_settings
from ..errors import BlockedByCredentials, GateBlocked
from ..models import Channel, ReviewEvent, Story, TelegramMessage, YouTubeUpload, now
from ..security import audit, redact
from ..state_machine import StoryState as S

log = logging.getLogger(__name__)
HEAL_STATES = (S.NEEDS_REVIEW.value, S.FAILED_RETRYABLE.value, S.FAILED_PERMANENT.value, S.UPLOADED_PRIVATE.value)
HEAL_COOLDOWN = timedelta(minutes=10)
OWNER_MARK = "🔎 Cần bạn xem:"
PUBLISHABLE = (S.EDITOR_REVIEW.value, S.APPROVED_PRIVATE.value)
LOCKED_MSG = ("YouTube giữ video ở chế độ RIÊNG TƯ dù app đã yêu cầu công khai. Nguyên nhân thường gặp: project Google Cloud "
              "chưa qua kiểm định (audit) YouTube API, nên mọi video tải lên qua API bị khoá riêng tư. Nộp đơn audit theo "
              "docs/youtube-api-audit.md; trong lúc chờ, app vẫn làm video nhưng không công khai được.")


def full_auto() -> bool:
    return bool(get_settings().full_auto)


def is_on(db, key: str) -> bool:
    """Autopilot / lịch quét: bật khi chủ kênh bật riêng HOẶC đang ở chế độ Tự động hoàn toàn."""
    from .jobs import get_setting

    return bool(get_setting(db, key, False)) or full_auto()


def auto_publish_active() -> bool:
    s = get_settings()
    return bool(s.full_auto and s.auto_publish_delay_minutes > 0)


def schedule_publish(story: Story) -> datetime | None:
    """Gọi khi gửi Telegram cho video Autopilot: đặt giờ tự công khai (nếu đang bật)."""
    if not auto_publish_active() or story.mode != "AUTOPILOT_PRIVATE" or story.publish_hold:
        return None
    if story.publish_after is None:
        story.publish_after = now() + timedelta(minutes=get_settings().auto_publish_delay_minutes)
    return story.publish_after


def local_hhmm(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo(get_settings().timezone)).strftime("%H:%M %d/%m")


def _tell_owner(db, story: Story, text: str, providers=None) -> None:
    """Nhắn chủ kênh (không có nút). Lỗi Telegram không được làm hỏng việc chính."""
    try:
        from ..providers import Providers

        p = providers or Providers()
        s = get_settings()
        chats = s.allowed_chat_ids or ({"DEMO_CHAT"} if story.is_demo else set())
        tg = p.get_telegram()
        for chat in sorted(chats):
            tg.send(chat, text, [], None)
    except Exception as e:  # noqa: BLE001
        log.info("Không nhắn được Telegram cho chủ kênh: %s", redact(e))


# ------------------------------------------------------------------ 1. tự sửa

def _resume(story_id: str, providers) -> dict:
    from .jobs import run_pipeline

    return run_pipeline(story_id, providers, actor="autoheal")


def heal_on() -> bool:
    s = get_settings()
    return bool(s.auto_heal or s.full_auto)


def diagnose(why: str) -> str:
    """Loại lỗi → cách tự sửa. credentials: app không tự sửa được (chủ kênh phải nhập key/kết nối lại)."""
    from ..services.ranker import strip_accents

    t = strip_accents(why or "").lower()
    if "blocked_by_credentials" in t or any(w in t for w in ("thieu key", "tu choi key", "tu choi token", "khong nhan api key",
                                                             "chua chon kenh", "chua ket noi", "invalid_grant")):
        return "credentials"
    if "blocked_by_budget" in t:
        return "budget"
    if any(w in t for w in ("khong duoi 10 phut", "duoi 10 phut", "media qa: thoi luong")):
        return "too_short"
    if "script qa" in t:
        return "script_qa"
    return "other"


def needs_owner(st: Story) -> bool:
    return any(str(b).startswith(OWNER_MARK) for b in (st.blockers or []))


def hand_over(db, st: Story, reason: str, providers=None) -> bool:
    """Không tự xử lý được → giữ video cho chủ kênh xem (một lần): ghi lý do lên đầu danh sách chặn + nhắn Telegram."""
    if needs_owner(st):
        return False
    st.blockers = [f"{OWNER_MARK} {reason}", *(st.blockers or [])]
    db.add(ReviewEvent(story_id=st.id, action="autoheal_handover", actor="autoheal", via="system", note=reason[:500]))
    audit(db, "autoheal", "story.handover", "story", st.id, reason=reason[:300])
    db.commit()
    base = get_settings().public_base_url.rstrip("/")
    _tell_owner(db, st, f"🔎 Video cần bạn xem: {st.topic}\n{reason}\nMở: {base}/story/?id={st.id}", providers)
    return True


def _fix(db, st: Story, kind: str) -> str:
    """Áp cách sửa theo loại lỗi trước khi chạy lại. Trả mô tả ngắn (ghi nhật ký)."""
    from ..models import ScriptVersion
    from .jobs import _override_script, transition

    sv = db.get(ScriptVersion, st.current_script_version_id) if st.current_script_version_id else None
    if kind == "script_qa" and sv is not None and not sv.qa_passed:
        if _override_script(sv):
            return "bỏ qua kiểm tra kịch bản (công tắc Bỏ qua mọi kiểm tra)"
        st.current_script_version_id = None
        return "viết kịch bản mới"
    if kind == "too_short" and st.state == S.NEEDS_REVIEW.value:
        st.current_script_version_id = None
        st.script_approved_by = None  # kịch bản mới (MANUAL: chủ kênh duyệt lại bằng nút "Duyệt & dựng")
        transition(db, st, S.SCRIPT_DRAFT, "autoheal", "Video dưới 10 phút → viết kịch bản mới dài hơn")
        return "viết kịch bản mới dài hơn (video dưới 10 phút)"
    if st.resume_state == S.SCRIPT_DRAFT.value and sv is not None and not sv.qa_passed:
        st.current_script_version_id = None
        return "viết kịch bản mới"
    return "chạy lại"


def heal(db, providers=None) -> list[dict]:
    s = get_settings()
    out = []
    t = now()
    today = t.astimezone(ZoneInfo(s.timezone)).date()
    q = select(Story).where(Story.state.in_(HEAL_STATES)).order_by(Story.updated_at)
    for st in list(db.scalars(q)):
        if needs_owner(st):
            continue  # đã giao cho chủ kênh; chủ kênh bấm Chạy lại thì app tự xử lý lại từ đầu
        upd = st.updated_at if st.updated_at.tzinfo else st.updated_at.replace(tzinfo=t.tzinfo)
        if t - upd < HEAL_COOLDOWN:
            continue
        if st.state == S.UPLOADED_PRIVATE.value and not st.blockers:
            continue
        lease = st.lease_expires_at.replace(tzinfo=st.lease_expires_at.tzinfo or t.tzinfo) if st.lease_expires_at else None
        if st.lease_owner and lease and lease > t:
            continue
        why = " ".join([*(st.blockers or []), st.last_error or ""])
        kind = diagnose(why)
        if kind == "credentials":
            if hand_over(db, st, "Thiếu hoặc sai key/kết nối dịch vụ — app không tự nhập key được. Mở trang video để xem "
                                 "cần nhập gì ở Thiết lập, rồi bấm Chạy lại.", providers):
                out.append({"story_id": st.id, "action": "handover", "kind": kind})
            continue
        if kind == "budget":
            if upd.astimezone(ZoneInfo(s.timezone)).date() == today:
                continue  # đợi ngân sách ngày mới
            out.append({"story_id": st.id, "action": "resume_budget", **_resume(st.id, providers)})
            continue
        if st.auto_heal_attempts >= s.auto_heal_max_attempts:
            if hand_over(db, st, f"App đã tự xử lý {st.auto_heal_attempts} lần nhưng chưa được. Lỗi gần nhất: "
                                 f"{redact(why.strip())[:300]}", providers):
                out.append({"story_id": st.id, "action": "handover", "kind": kind})
            continue
        st.auto_heal_attempts += 1
        fix = _fix(db, st, kind)
        db.add(ReviewEvent(story_id=st.id, action="autoheal_retry", actor="autoheal", via="system",
                           note=f"Lần {st.auto_heal_attempts}/{s.auto_heal_max_attempts}: {fix}. Lỗi: {why[:300]}"))
        audit(db, "autoheal", "story.autoheal_retry", "story", st.id, attempt=st.auto_heal_attempts, fix=fix, reason=why[:300])
        db.commit()
        out.append({"story_id": st.id, "action": "retry", "attempt": st.auto_heal_attempts, "fix": fix,
                    **_resume(st.id, providers)})
    return out


# ------------------------------------------------------------------ 3. tự công khai

def _publish_gates(db, st: Story) -> list[str]:
    from .jobs import story_claims

    reasons = []
    up = db.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id, YouTubeUpload.youtube_video_id.is_not(None)))
    if up is None or up.status != "verified" or up.privacy_status_observed != "private":
        reasons.append("Chưa có bản tải lên RIÊNG TƯ đã đọc lại")
    from ..models import Render

    r = db.get(Render, st.current_render_id) if st.current_render_id else None
    if r is None or not r.qa_passed:
        reasons.append("Media QA chưa đạt")
    if r is not None and r.duration and r.duration < 600 and not get_settings().allow_short_duration_for_tests:
        reasons.append("Video dưới 10 phút")
    sent = db.scalar(select(TelegramMessage.id).where(TelegramMessage.story_id == st.id, TelegramMessage.status == "sent").limit(1))
    if sent is None:
        reasons.append("Chưa báo chủ kênh qua Telegram (nút Dừng) — chưa công khai")
    bad = [c.claim_key for c in story_claims(db, st) if c.is_key and c.status in ("contested", "unverified")]
    if bad:
        reasons.append(f"Claim trọng yếu chưa ổn sau khi kiểm chứng lại: {', '.join(bad[:5])}")
    return reasons


def publish_due(db, providers=None) -> list[dict]:
    from ..providers import Providers
    from ..services.youtube.client import MANAGE_SCOPE
    from ..services.youtube.publish import LockedPrivate, publish_video
    from .jobs import fact_check_job, make_youtube_client, transition

    if not auto_publish_active():
        return []
    p = providers or Providers()
    out = []
    t = now()
    q = select(Story).where(Story.state.in_(PUBLISHABLE), Story.mode == "AUTOPILOT_PRIVATE", Story.publish_hold.is_(False),
                            Story.publish_after.is_not(None), Story.publish_after <= t)
    for st in list(db.scalars(q)):
        ch = db.get(Channel, st.channel_id)
        if ch is None:
            continue
        if not ch.is_demo and MANAGE_SCOPE not in (ch.scopes or []):
            msg = "Cần kết nối lại kênh YouTube để cấp quyền quản lý video (tự công khai)."
            if msg not in (st.blockers or []):
                st.blockers = [*(st.blockers or []), msg]
                db.commit()
                log.warning("Chưa tự công khai được %s: %s", st.id, msg, extra={"evcs_source": "youtube", "evcs_story_id": st.id})
            continue
        fc_reasons: list[str] = []
        try:
            fact_check_job(db, st, p)  # kiểm chứng lại + hạn hiệu lực ngay trước khi công khai
        except GateBlocked as e:
            fc_reasons = [f"Kiểm chứng lại: {r}" for r in e.reasons]
        except Exception as e:  # noqa: BLE001
            fc_reasons = [f"Không kiểm chứng lại được: {redact(e)}"]
        reasons = fc_reasons + _publish_gates(db, st)
        if reasons:
            st.publish_hold = True
            st.blockers = [f"Không tự công khai: {r}" for r in reasons]
            audit(db, "autopublish", "story.autopublish_held", "story", st.id, reasons=reasons)
            db.commit()
            _tell_owner(db, st, f"⛔ Chưa tự công khai \"{st.topic}\": {'; '.join(reasons)}. Video vẫn RIÊNG TƯ.", p)
            out.append({"story_id": st.id, "action": "held", "reasons": reasons})
            continue
        up = db.scalar(select(YouTubeUpload).where(YouTubeUpload.story_id == st.id, YouTubeUpload.youtube_video_id.is_not(None)))
        try:
            client = make_youtube_client(db, ch, p)
            privacy = publish_video(client, up.youtube_video_id)
        except LockedPrivate:
            st.publish_hold = True
            st.blockers = [LOCKED_MSG]
            up.privacy_status_observed, up.observed_at = "private", now()
            audit(db, "autopublish", "story.autopublish_locked", "story", st.id, video=up.youtube_video_id)
            db.commit()
            log.warning("Tự công khai không thành công (%s): YouTube giữ riêng tư — project API có thể chưa qua audit",
                        up.youtube_video_id, extra={"evcs_source": "youtube", "evcs_story_id": st.id})
            _tell_owner(db, st, f"⚠️ \"{st.topic}\": {LOCKED_MSG}", p)
            out.append({"story_id": st.id, "action": "locked_private"})
            continue
        except BlockedByCredentials as e:
            msg = f"Chưa tự công khai được: {redact(e)}"
            if msg not in (st.blockers or []):
                st.blockers = [msg]
                db.commit()
                log.warning("%s (story %s)", msg, st.id, extra={"evcs_source": "youtube", "evcs_story_id": st.id})
            continue
        up.privacy_status_observed, up.observed_at = privacy, now()
        st.blockers = []
        transition(db, st, S.PUBLISHED_AUTO, "autopublish", f"Tự công khai sau {get_settings().auto_publish_delay_minutes} phút chờ")
        db.add(ReviewEvent(story_id=st.id, action="auto_published", actor="autopublish", via="youtube",
                           note="Hết thời gian chờ, chủ kênh không bấm Dừng; kiểm chứng lại đạt"))
        audit(db, "autopublish", "story.auto_published", "story", st.id, video=up.youtube_video_id)
        db.commit()
        _tell_owner(db, st, f"✅ Đã công khai: {st.topic}\nhttps://youtu.be/{up.youtube_video_id}", p)
        out.append({"story_id": st.id, "action": "published", "video": up.youtube_video_id})
    return out


def hold(db, story: Story, actor: str, on: bool = True) -> dict:
    story.publish_hold = on
    if on:
        story.blockers = [b for b in (story.blockers or []) if not b.startswith("Không tự công khai")] + \
            [f"Chủ kênh đã bấm Dừng tự công khai ({actor}). Video giữ RIÊNG TƯ."]
    else:
        story.blockers = [b for b in (story.blockers or []) if "Dừng tự công khai" not in b]
        if auto_publish_active() and story.publish_after is None:
            story.publish_after = now() + timedelta(minutes=get_settings().auto_publish_delay_minutes)
    audit(db, actor, "story.autopublish_hold" if on else "story.autopublish_resume", "story", story.id)
    db.commit()
    return {"story_id": story.id, "publish_hold": story.publish_hold, "publish_after": story.publish_after}


# ------------------------------------------------------------------ lượt chạy

def tick(providers=None) -> dict:
    from ..db import SessionLocal
    from .jobs import autopilot_tick

    if not full_auto() and not heal_on():
        return {"ran": False, "reason": "Tự động hoàn toàn và Tự xử lý lỗi đều đang tắt"}
    res: dict = {"ran": True}
    with SessionLocal() as db:
        try:
            res["heal"] = heal(db, providers)
        except Exception:
            db.rollback()
            log.exception("Tự sửa lỗi gặp sự cố", extra={"evcs_source": "scheduler"})
        if not full_auto():
            return res  # chỉ tự xử lý lỗi; tự công khai / Autopilot thuộc Tự động hoàn toàn
        try:
            res["publish"] = publish_due(db, providers)
        except Exception:
            db.rollback()
            log.exception("Tự công khai gặp sự cố", extra={"evcs_source": "scheduler"})
        try:
            res["autopilot"] = autopilot_tick(db, providers)
        except Exception:
            db.rollback()
            log.exception("Autopilot gặp sự cố", extra={"evcs_source": "scheduler"})
    return res
