"""Ban Tăng trưởng — theo dõi kênh bằng YouTube Analytics (chỉ đọc), đối chiếu KPI doanh thu tháng, tự chỉnh kế hoạch.

Mỗi lần chạy (lịch hằng ngày hoặc bấm tay):
1. Đồng bộ số liệu ngày (≈62 ngày gần nhất), top video 28 ngày, tổng 365 ngày, số người đăng ký. $0.
2. Tính tình hình KPI: đã kiếm trong tháng, dự báo cuối tháng, còn thiếu, RPM, lượt xem/ngày cần có. $0.
3. Ra quyết định có lý do: số video/ngày cho Autopilot, tỷ trọng ưu tiên trụ cột P1/P2/P3. $0.
   Chỉ áp dụng khi `growth_auto_apply` bật, và luôn nằm trong: trần ngân sách AI, trần video/ngày của Ban, trần video/tuần.
4. Liệt kê việc CHỈ chủ kênh làm được (công khai video đang riêng tư, nâng trần, bật Autopilot…).
5. Hằng tuần: bản ghi nhớ chiến lược do Claude (Haiku) viết, qua Ban Tài chính. Định hướng nội dung trong bản ghi nhớ
   được đưa vào kịch bản như ghi chú biên tập, không phải dữ kiện.

Bất biến giữ nguyên: không tự công khai video, không sửa video trên kênh, không tự tăng trần chi tiêu.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import get_settings
from ..errors import BlockedByCredentials, GateBlocked
from ..models import Channel, ChannelMetricDay, GrowthPlan, Story, VideoMetric, YouTubeUpload, now

log = logging.getLogger(__name__)
PILLARS = ("P1", "P2", "P3")
PILLAR_FLOOR = 0.15  # mỗi trụ cột luôn giữ ít nhất 15% để tiếp tục thử nghiệm
DEFAULT_VIEWS_PER_VIDEO = 500  # khi kênh chưa có dữ liệu video mới
MEMO_EVERY = timedelta(days=7)
S_TARGET, S_WEIGHTS, S_GUIDANCE = "growth_stories_per_day", "growth_pillar_weights", "growth_content_guidance"
# YPP (ad revenue): 1.000 người đăng ký + 4.000 giờ xem công khai/12 tháng; người đăng ký MỚI từ 01/02/2027 cần 8.000 giờ
YPP_SUBS, YPP_HOURS_NOW, YPP_HOURS_2027, YPP_2027 = 1000, 4000, 8000, date(2027, 2, 1)


# ------------------------------------------------------------------ settings đọc/ghi (AppSetting)

def _get(db, key, default=None):
    from ..workers.jobs import get_setting

    return get_setting(db, key, default)


def _set(db, key, value):
    from ..workers.jobs import set_setting

    set_setting(db, key, value)


def effective_stories_per_day(db) -> int:
    """Số video/ngày Autopilot dùng: kế hoạch của Ban Tăng trưởng (nếu đang tự áp dụng), không thì cấu hình chủ kênh."""
    s = get_settings()
    t = _get(db, S_TARGET) if s.growth_auto_apply else None
    if t is None:
        return int(s.stories_per_day)
    return max(1, min(int(t), int(s.growth_max_stories_per_day)))


def pillar_weights(db) -> dict[str, float]:
    s = get_settings()
    w = _get(db, S_WEIGHTS) if s.growth_auto_apply else None
    if not w:
        return {p: 1 / 3 for p in PILLARS}
    return {p: float(w.get(p, 1 / 3)) for p in PILLARS}


def content_guidance(db) -> str:
    return (_get(db, S_GUIDANCE) or "").strip() if get_settings().growth_auto_apply else ""


def autopilot_priority(score: float, pillar: str | None, weights: dict[str, float]) -> float:
    """Xếp thứ tự tin cho Autopilot: điểm tin × hệ số trụ cột (0,82–1,37). Không hạ ngưỡng điểm, không bỏ kiểm chứng."""
    return float(score) * (1 + (weights.get(pillar or "P1", 1 / 3) - 1 / 3))


# ------------------------------------------------------------------ 1. đồng bộ

def analytics_client_for(db, channel: Channel):
    from ..providers import youtube_mode
    from ..security import decrypt_str, encrypt_str
    from ..services.youtube.analytics import DemoAnalyticsClient, GoogleAnalyticsClient
    from ..services.youtube.client import ANALYTICS_SCOPE, credentials_from_json

    if youtube_mode() == "demo":
        vids = [v for (v,) in db.execute(select(YouTubeUpload.youtube_video_id).where(
            YouTubeUpload.channel_id == channel.id, YouTubeUpload.youtube_video_id.is_not(None)))]
        return DemoAnalyticsClient(channel_id=channel.youtube_channel_id, video_ids=vids)
    if not channel.oauth_token_encrypted:
        raise BlockedByCredentials("Kênh chưa kết nối OAuth. Vào Thiết lập → Kết nối kênh YouTube.")
    if ANALYTICS_SCOPE not in (channel.scopes or []):
        raise BlockedByCredentials("Kênh được kết nối trước khi có Ban Tăng trưởng nên chưa có quyền đọc Analytics. "
                                   "Vào Thiết lập → bấm 'Kết nối kênh YouTube' lại và đồng ý các quyền mới.")

    def on_refresh(tok: str):
        channel.oauth_token_encrypted = encrypt_str(tok)
        channel.token_refreshed_at = now()

    return GoogleAnalyticsClient(credentials_from_json(decrypt_str(channel.oauth_token_encrypted)), on_refresh=on_refresh)


def _f(v):
    return None if v is None else float(v)


def sync(db, client, channel: Channel, today: date | None = None) -> dict:
    today = today or datetime.now(ZoneInfo(get_settings().timezone)).date()
    cid = channel.youtube_channel_id
    start = today - timedelta(days=62)
    rows, money_err = client.daily(start, today)
    existing = {m.day: m for m in db.scalars(select(ChannelMetricDay).where(ChannelMetricDay.channel_id == cid,
                                                                             ChannelMetricDay.day >= start.isoformat()))}
    for r in rows:
        m = existing.get(r["day"]) or ChannelMetricDay(channel_id=cid, day=r["day"])
        m.views, m.watch_minutes = int(r.get("views") or 0), float(r.get("estimatedMinutesWatched") or 0)
        m.avg_view_seconds = float(r.get("averageViewDuration") or 0)
        m.subs_gained, m.subs_lost = int(r.get("subscribersGained") or 0), int(r.get("subscribersLost") or 0)
        m.revenue_usd, m.ad_revenue_usd = _f(r.get("estimatedRevenue")), _f(r.get("estimatedAdRevenue"))
        m.playback_cpm_usd, m.fetched_at, m.is_demo = _f(r.get("playbackBasedCpm")), now(), client.is_demo
        db.add(m)
    # top video 28 ngày (ghi đè)
    p0 = today - timedelta(days=27)
    vids, vmoney_err = client.top_videos(p0, today)
    details = client.video_details([v["video"] for v in vids]) if vids else {}
    ours = {u.youtube_video_id: u.story_id for u in db.scalars(select(YouTubeUpload).where(YouTubeUpload.youtube_video_id.is_not(None)))}
    pillars = dict(db.execute(select(Story.id, Story.pillar).where(Story.id.in_(list(ours.values()) or [""]))).all())
    db.query(VideoMetric).filter(VideoMetric.channel_id == cid).delete(synchronize_session=False)
    for v in vids:
        d = details.get(v["video"], {})
        sid = ours.get(v["video"])
        pub = d.get("published_at")
        db.add(VideoMetric(
            channel_id=cid, youtube_video_id=v["video"], title=d.get("title"), privacy=d.get("privacy"),
            published_at=datetime.fromisoformat(pub.replace("Z", "+00:00")) if pub else None,
            story_id=sid, pillar=pillars.get(sid), period_start=p0.isoformat(), period_end=today.isoformat(),
            views=int(v.get("views") or 0), watch_minutes=float(v.get("estimatedMinutesWatched") or 0),
            avg_view_seconds=float(v.get("averageViewDuration") or 0), avg_view_pct=float(v.get("averageViewPercentage") or 0),
            subs_gained=int(v.get("subscribersGained") or 0), revenue_usd=_f(v.get("estimatedRevenue")),
            ad_revenue_usd=_f(v.get("estimatedAdRevenue")), is_demo=client.is_demo))
    year = client.total(today - timedelta(days=364), today)
    stats = client.channel_stats()
    db.flush()
    return {"synced_at": now().isoformat(), "days": len(rows), "videos": len(vids), "money_error": money_err or vmoney_err,
            "subscribers": stats.get("subscribers"), "hidden_subscribers": stats.get("hidden_subscribers"),
            "channel_title": stats.get("title"), "watch_hours_365": round(float(year.get("estimatedMinutesWatched") or 0) / 60, 1),
            "is_demo": client.is_demo}


# ------------------------------------------------------------------ 2. tình hình KPI

def _rev(m: ChannelMetricDay) -> float | None:
    return m.ad_revenue_usd if m.ad_revenue_usd is not None else m.revenue_usd


def kpi_status(db, channel_id: str, today: date | None = None, sync_info: dict | None = None) -> dict:
    s = get_settings()
    today = today or datetime.now(ZoneInfo(s.timezone)).date()
    m0 = today.replace(day=1)
    dim = (m0.replace(month=m0.month % 12 + 1, year=m0.year + (m0.month == 12)) - m0).days
    days = list(db.scalars(select(ChannelMetricDay).where(ChannelMetricDay.channel_id == channel_id,
                                                          ChannelMetricDay.day >= (today - timedelta(days=62)).isoformat())
                           .order_by(ChannelMetricDay.day)))
    month = [d for d in days if d.day >= m0.isoformat()]
    with_rev = [d for d in month if _rev(d) is not None]
    monetized = any(_rev(d) is not None for d in days)
    mtd = round(sum(_rev(d) for d in with_rev), 2)
    last = date.fromisoformat(with_rev[-1].day) if with_rev else (date.fromisoformat(month[-1].day) if month else m0 - timedelta(days=1))
    covered = max(0, (last - m0).days + 1)
    remaining = max(0, dim - covered)
    rev_days = [d for d in days if _rev(d) is not None]
    last7 = rev_days[-7:]
    daily_rate = (sum(_rev(d) for d in last7) / len(last7)) if last7 else 0.0
    projected = round(mtd + daily_rate * remaining, 2)
    w28 = days[-28:]
    views28 = sum(d.views for d in w28)
    rev28 = sum(_rev(d) or 0 for d in w28)
    rpm = round(rev28 / views28 * 1000, 3) if views28 and monetized and rev28 > 0 else None
    vpd7 = round(sum(d.views for d in days[-7:]) / max(1, len(days[-7:])), 1) if days else 0.0
    kpi = float(s.kpi_monthly_revenue_usd or 0)
    need_left = max(0.0, kpi - mtd)
    need_per_day = round(need_left / remaining, 3) if remaining else None
    req_vpd = round(need_per_day / rpm * 1000) if (need_per_day is not None and rpm) else None
    # video mới (≤ 60 ngày): trung bình lượt xem trong 28 ngày — để ước tính mỗi video thêm mang lại bao nhiêu
    vids = list(db.scalars(select(VideoMetric).where(VideoMetric.channel_id == channel_id)))
    cutoff = datetime.now(timezone.utc) - timedelta(days=60)
    fresh = [v for v in vids if v.published_at and v.published_at.replace(tzinfo=v.published_at.tzinfo or timezone.utc) >= cutoff]
    sample = fresh if len(fresh) >= 3 else vids
    vpv = round(sum(v.views for v in sample) / len(sample)) if sample else DEFAULT_VIEWS_PER_VIDEO
    pub28 = sum(1 for v in fresh if v.published_at.replace(tzinfo=v.published_at.tzinfo or timezone.utc)
                >= datetime.now(timezone.utc) - timedelta(days=28))
    by_pillar = {}
    for p in PILLARS:
        pv = [v for v in vids if v.pillar == p]
        if pv:
            r = [v.ad_revenue_usd if v.ad_revenue_usd is not None else v.revenue_usd for v in pv]
            by_pillar[p] = {"videos": len(pv), "avg_views": round(sum(v.views for v in pv) / len(pv)),
                            "avg_view_pct": round(sum(v.avg_view_pct for v in pv) / len(pv), 1),
                            "avg_revenue_usd": round(sum(x or 0 for x in r) / len(pv), 3) if any(x is not None for x in r) else None}
    from sqlalchemy import func

    n_waiting = int(db.scalar(select(func.count(Story.id)).where(Story.state.in_(["EDITOR_REVIEW", "APPROVED_PRIVATE"]))) or 0)
    si = sync_info or {}
    hours_needed = YPP_HOURS_2027 if today >= YPP_2027 else YPP_HOURS_NOW
    return {
        "today": today.isoformat(), "month": m0.strftime("%Y-%m"), "days_in_month": dim, "data_through": last.isoformat() if covered else None,
        "days_covered": covered, "days_remaining": remaining, "kpi_usd": kpi, "kpi_set": kpi > 0, "monetized": monetized,
        "mtd_usd": mtd, "projected_usd": projected, "gap_usd": round(max(0.0, kpi - projected), 2) if kpi else None,
        "progress": round(mtd / kpi, 4) if kpi else None, "projected_ratio": round(projected / kpi, 4) if kpi else None,
        "daily_rate_usd": round(daily_rate, 3), "need_per_day_usd": need_per_day, "rpm_usd": rpm,
        "views_per_day_7d": vpd7, "required_views_per_day": req_vpd, "views_28d": views28,
        "avg_views_per_new_video": vpv, "videos_published_28d": pub28, "by_pillar": by_pillar,
        "waiting_owner_publish": n_waiting,
        "series": [{"day": d.day, "views": d.views, "revenue_usd": _rev(d)} for d in days[-35:]],
        "top_videos": [{"id": v.youtube_video_id, "title": v.title, "views": v.views, "avg_view_pct": v.avg_view_pct,
                        "revenue_usd": v.ad_revenue_usd if v.ad_revenue_usd is not None else v.revenue_usd,
                        "pillar": v.pillar, "ours": bool(v.story_id)}
                       for v in sorted(vids, key=lambda v: -v.views)[:10]],
        "ypp": {"subscribers": si.get("subscribers"), "subscribers_needed": YPP_SUBS, "watch_hours_365": si.get("watch_hours_365"),
                "watch_hours_needed": hours_needed,
                "note": "Giờ xem ở đây là tổng ước tính 365 ngày; YouTube chỉ tính giờ xem hợp lệ từ video dài công khai. "
                        "Người đăng ký YPP mới từ 01/02/2027 cần 8.000 giờ."},
        "sync": si,
    }


# ------------------------------------------------------------------ 3. quyết định

def _affordable_per_day(db) -> tuple[int, dict]:
    from . import budget

    st = budget.status(db)
    avg = st["avg_story_usd"] or budget.DEFAULT_STORY_COST
    per_day = st["remaining_month_usd"] / max(1, st["days_left"]) / avg if avg else 0
    return int(per_day), st


def decide(db, status: dict) -> tuple[list[dict], list[dict], dict]:
    from ..workers.jobs import get_setting

    s = get_settings()
    decisions: list[dict] = []
    owner: list[dict] = []
    cur = effective_stories_per_day(db)
    afford, bst = _affordable_per_day(db)
    week_cap = math.ceil(s.max_videos_per_week / 7)
    hi = max(1, min(int(s.growth_max_stories_per_day), afford or 1, week_cap))
    lo = min(int(s.stories_per_day_min), hi)
    target, why = cur, "giữ nguyên"
    vpv = max(1, status["avg_views_per_new_video"])
    if not status["kpi_set"]:
        owner.append({"title": "Đặt KPI doanh thu tháng", "detail": "Thiết lập → Ban Tăng trưởng & KPI → KPI doanh thu quảng cáo mỗi tháng (USD)."})
    elif not status["monetized"]:
        target, why = hi, ("Kênh chưa có doanh thu (chưa bật kiếm tiền hoặc chưa cấp quyền doanh thu): ưu tiên tăng giờ xem "
                           "và người đăng ký để đủ điều kiện YPP, nên sản xuất ở mức cao nhất ngân sách cho phép.")
    elif status["required_views_per_day"] is not None:
        need = status["required_views_per_day"]
        # Mô hình đơn giản: mỗi video mới có ~vpv lượt xem trong 28 ngày đầu (~vpv/28 mỗi ngày). Đăng đều k video/ngày
        # → luôn có ~28k video "mới" → đóng góp ~k·vpv lượt xem/ngày. Phần còn lại đến từ video cũ (thư viện).
        # Cuối tháng, video đăng hôm nay chưa kịp đủ 28 ngày: lượt xem dồn nhiều vào những ngày đầu nên dùng căn bậc hai.
        rem = max(1, status["days_remaining"])
        vpv_eff = max(1.0, vpv * min(1.0, (rem / 28) ** 0.5))
        rate = status["videos_published_28d"] / 28
        base_old = max(0.0, status["views_per_day_7d"] - rate * vpv)
        k = max(0.0, (need - base_old) / vpv_eff)
        behind = bool(status["gap_usd"] and status["gap_usd"] > 0)
        ahead = status["projected_ratio"] is not None and status["projected_ratio"] >= 1.1
        if behind:
            target = max(cur, math.ceil(k))  # đang thiếu KPI: không bao giờ giảm sản lượng
        elif ahead:
            target = max(lo, min(cur, math.ceil(k)))  # vượt KPI rõ: có thể giảm để tiết kiệm ngân sách
        else:
            target = cur
        why = (f"{'Đang thiếu' if behind else 'Đang vượt' if ahead else 'Đang bám sát'} KPI (dự báo ${status['projected_usd']:.2f}/"
               f"${status['kpi_usd']:.2f}). Cần ~{need:,.0f} lượt xem/ngày trong {rem} ngày còn lại (RPM ${status['rpm_usd']}), "
               f"hiện ~{status['views_per_day_7d']:,.0f}/ngày; mỗi video mới trung bình ~{vpv:,} lượt xem/28 ngày "
               f"(~{vpv_eff:,.0f} trong thời gian còn lại) → ước tính cần ~{k:.1f} video/ngày.")
        if math.ceil(k) > hi:
            binding = []
            if int(s.growth_max_stories_per_day) <= hi:
                binding.append(f"trần của Ban Tăng trưởng {s.growth_max_stories_per_day} video/ngày (Thiết lập → Ban Tăng trưởng)")
            if (afford or 1) <= hi:
                binding.append(f"ngân sách AI chỉ đủ ~{afford} video/ngày (${bst['remaining_month_usd']:.2f} cho {bst['days_left']} ngày)")
            if week_cap <= hi:
                binding.append(f"trần {s.max_videos_per_week} video/tuần (Thiết lập → Biên tập → Tối đa video/tuần)")
            owner.append({"title": f"KPI cần ~{math.ceil(k)} video/ngày nhưng hiện chỉ được {hi}",
                          "detail": "Đang bị chặn bởi " + "; ".join(binding) + ". Bạn có thể nâng trần này, hoặc chấp nhận KPI tháng "
                                    "này chưa đạt. Chất lượng và tỷ lệ xem hết video thường quan trọng hơn số lượng."})
    else:
        why = "Chưa đủ số liệu doanh thu tháng này để tính, giữ nguyên."
    target = max(lo, min(hi, int(target)))
    if target != cur:
        decisions.append({"key": S_TARGET, "label": "Số video/ngày (Autopilot)", "from": cur, "to": target, "reason": why})
    else:
        decisions.append({"key": S_TARGET, "label": "Số video/ngày (Autopilot)", "from": cur, "to": cur, "reason": why, "unchanged": True})
    # tỷ trọng trụ cột: doanh thu (hoặc lượt xem) trung bình mỗi video, giữ sàn thử nghiệm, làm mượt với kế hoạch cũ
    bp = status["by_pillar"]
    old = pillar_weights(db)
    metric = {p: (bp[p]["avg_revenue_usd"] if bp[p]["avg_revenue_usd"] is not None else bp[p]["avg_views"])
              for p in bp if bp[p]["videos"] >= 2}
    if len(metric) >= 2 and sum(metric.values()) > 0:
        mean = sum(metric.values()) / len(metric)
        raw = {p: (metric.get(p, mean) or 0) for p in PILLARS}
        tot = sum(raw.values()) or 1
        new = {p: 0.5 * old[p] + 0.5 * raw[p] / tot for p in PILLARS}
        new = {p: max(PILLAR_FLOOR, v) for p, v in new.items()}
        tot = sum(new.values())
        new = {p: round(v / tot, 3) for p, v in new.items()}
        best = max(metric, key=metric.get)
        decisions.append({"key": S_WEIGHTS, "label": "Ưu tiên trụ cột cho Autopilot", "from": {p: round(old[p], 3) for p in PILLARS},
                          "to": new, "reason": f"{best} đang mang lại nhiều {'doanh thu' if bp[best]['avg_revenue_usd'] is not None else 'lượt xem'} "
                                               f"nhất mỗi video; các trụ cột khác giữ tối thiểu {PILLAR_FLOOR:.0%} để tiếp tục thử."})
    # việc chỉ chủ kênh làm được
    if status["waiting_owner_publish"]:
        owner.append({"title": f"{status['waiting_owner_publish']} video đang RIÊNG TƯ chờ bạn duyệt và công khai",
                      "detail": "Video riêng tư không có lượt xem và không có doanh thu. Xem lại rồi tự công khai trên YouTube Studio."})
    if not get_setting(db, "autopilot_enabled", False):
        owner.append({"title": "Autopilot đang tắt", "detail": "Kế hoạch số video/ngày chỉ có tác dụng khi Autopilot bật (Thiết lập → Tự động hoá)."})
    if not get_setting(db, "scan_schedule_enabled", False):
        owner.append({"title": "Lịch quét tin đang tắt", "detail": "Không có tin mới thì Autopilot không có gì để làm. Bật ở Bảng điều khiển."})
    if not status["monetized"]:
        y = status["ypp"]
        owner.append({"title": "Chưa đọc được doanh thu", "detail":
                      f"Nếu kênh chưa vào YPP: cần {y['subscribers_needed']:,} người đăng ký và {y['watch_hours_needed']:,} giờ xem công khai "
                      f"(hiện ~{y['subscribers'] if y['subscribers'] is not None else '?'} người đăng ký, ~{y['watch_hours_365'] or '?'} giờ). "
                      "Nếu kênh đã bật kiếm tiền: kết nối kênh lại và đồng ý quyền xem doanh thu."})
    return decisions, owner, {"afford_per_day": afford, "hi": hi, "lo": lo, "week_cap_per_day": week_cap}


def apply(db, decisions: list[dict]) -> bool:
    if not get_settings().growth_auto_apply:
        return False
    for d in decisions:
        if d.get("unchanged"):
            continue
        _set(db, d["key"], d["to"])
        d["applied"] = True
    return True


# ------------------------------------------------------------------ 4. bản ghi nhớ chiến lược (AI, hằng tuần)

class MemoAction(BaseModel):
    title: str
    why: str
    how: str
    who: str = Field("agent", description="agent | owner")
    expected_impact: str = ""


class GrowthMemo(BaseModel):
    summary: str
    diagnosis: list[str] = Field(default_factory=list)
    actions: list[MemoAction] = Field(default_factory=list)
    experiments: list[str] = Field(default_factory=list)
    content_guidance: str = ""
    risks: list[str] = Field(default_factory=list)


MEMO_SYSTEM = """Bạn là TRƯỞNG BAN TĂNG TRƯỞNG của một kênh YouTube tiếng Việt độc lập về VinFast và xe điện (video bình luận \
dài ≥ 10 phút, giọng đọc AI, ba trụ cột: P1 trước khi mua xe, P2 mẹo dùng & tối ưu, P3 trạm sạc/thị trường/chính sách).
Nhiệm vụ: đọc số liệu YouTube Analytics và KPI doanh thu quảng cáo tháng, rồi đề xuất mọi cách HỢP LỆ để đạt KPI.

Luật bắt buộc (vi phạm có thể làm kênh mất kiếm tiền):
- Chỉ cách hợp lệ theo chính sách YouTube: KHÔNG mua lượt xem/người đăng ký, KHÔNG sub4sub, KHÔNG tự xem/click, KHÔNG nội dung
  lặp lại hay sản xuất hàng loạt kém giá trị, KHÔNG tiêu đề/thumbnail sai sự thật, KHÔNG bịa dữ kiện. Kênh dùng giọng AI nên
  mỗi video phải có giá trị phân tích riêng, khác biệt rõ.
- Ứng dụng KHÔNG tự công khai video và KHÔNG tự sửa video trên kênh; việc đó ghi `who: "owner"`.
- Không đề xuất vượt trần ngân sách AI; nếu cần thêm tiền, nói rõ là việc của chủ kênh.
- Chỉ dựa vào số liệu được cung cấp; nếu thiếu dữ liệu thì nói thiếu, không đoán con số.

Trả về DUY NHẤT một JSON: {"summary": "...", "diagnosis": ["..."], "actions": [{"title","why","how","who","expected_impact"}],
"experiments": ["..."], "content_guidance": "≤ 500 ký tự, định hướng biên tập cho Phòng Biên kịch (góc nhìn, kiểu mở bài, chủ đề nên/không nên)",
"risks": ["..."]}. Viết tiếng Việt, cụ thể, có số liệu dẫn chứng."""


def _memo_input(status: dict, decisions: list[dict], owner: list[dict]) -> str:
    keep = {k: status[k] for k in ("month", "kpi_usd", "mtd_usd", "projected_usd", "gap_usd", "rpm_usd", "views_per_day_7d",
                                    "required_views_per_day", "avg_views_per_new_video", "videos_published_28d", "by_pillar",
                                    "waiting_owner_publish", "monetized", "ypp", "days_remaining")}
    keep["top_videos"] = status["top_videos"]
    keep["decisions_this_run"] = [{k: d.get(k) for k in ("label", "from", "to", "reason")} for d in decisions]
    keep["owner_todo"] = owner
    return json.dumps(keep, ensure_ascii=False, default=str)


def demo_memo(status: dict) -> dict:
    bp = status["by_pillar"]
    best = max(bp, key=lambda p: bp[p]["avg_views"]) if bp else None
    diag = [f"[DEMO] Mỗi video mới đạt khoảng {status['avg_views_per_new_video']:,} lượt xem trong 28 ngày."]
    actions = []
    if best:
        diag.insert(0, f"[DEMO] {best} có lượt xem trung bình mỗi video cao nhất.")
        actions.append(MemoAction(title="Tăng tỷ trọng trụ cột đang hiệu quả", why="Lượt xem/video cao hơn", how=f"Ưu tiên tin {best} cho Autopilot"))
    else:
        diag.append("[DEMO] Chưa có video nào của app được công khai, nên chưa so sánh được ba trụ cột.")
    if status["waiting_owner_publish"]:
        actions.append(MemoAction(title="Công khai video đang chờ duyệt", why="Video riêng tư không có doanh thu",
                                  how="Duyệt trên Telegram/dashboard rồi công khai trên YouTube Studio", who="owner"))
    actions.append(MemoAction(title="Giữ nhịp đăng đều mỗi ngày", why="Lượt xem/ngày phụ thuộc số video mới trong 28 ngày",
                              how="Bật Autopilot và lịch quét tin để luôn có tin đủ chứng cứ", who="owner"))
    return GrowthMemo(
        summary=f"[DEMO] Bản ghi nhớ giả lập. Dự báo tháng {status['month']}: ${status['projected_usd']:.2f}"
                + (f" / KPI ${status['kpi_usd']:.2f}." if status["kpi_set"] else " (chưa đặt KPI)."),
        diagnosis=diag, actions=actions,
        experiments=["[DEMO] Thử mở bài bằng một câu hỏi về chi phí thực tế trong 15 giây đầu."],
        content_guidance="", risks=["[DEMO] Số liệu giả lập, không dùng để ra quyết định thật."],
    ).model_dump()


def write_memo(db, status: dict, decisions: list[dict], owner: list[dict], brain=None) -> tuple[dict | None, str | None]:
    from ..agents.writer import _schema, build_brain
    from .brain import Task
    from .budget import BudgetExceeded

    s = get_settings()
    if s.is_demo and brain is None and not s.anthropic_api_key:
        return demo_memo(status), None
    try:
        brain = brain or build_brain()
        db.commit()  # nhả khoá ghi SQLite để Ban Tài chính ghi sổ cái
        res, _usage = brain.ask(Task("tang_truong", MEMO_SYSTEM, _memo_input(status, decisions, owner), _schema(GrowthMemo),
                                     None, query=f"tăng trưởng {status['month']}", max_tokens=3000, use_cache=False))
        return res, None
    except BudgetExceeded as e:
        return None, f"Không viết bản ghi nhớ: {e}"
    except BlockedByCredentials as e:
        return None, f"Không viết bản ghi nhớ: {e}"
    except (GateBlocked, Exception) as e:  # noqa: BLE001 — bản ghi nhớ hỏng không được làm hỏng phần còn lại
        log.warning("Ban Tăng trưởng: không viết được bản ghi nhớ: %s", e, extra={"evcs_source": "llm"})
        return None, f"Không viết được bản ghi nhớ: {type(e).__name__}"


# ------------------------------------------------------------------ 5. một lượt chạy

def growth_tick(db, actor: str = "system", force_memo: bool = False, brain=None, client=None) -> dict:
    from ..security import audit

    s = get_settings()
    ch = db.scalar(select(Channel).where(Channel.selected.is_(True)))
    if ch is None:
        plan = GrowthPlan(month=datetime.now(ZoneInfo(s.timezone)).strftime("%Y-%m"), kpi_usd=s.kpi_monthly_revenue_usd,
                          status={"error": "Chưa chọn kênh đích"}, decisions=[],
                          owner_actions=[{"title": "Chọn kênh đích", "detail": "Thiết lập → Kết nối kênh YouTube → Chọn làm kênh đích."}],
                          actor=actor, is_demo=s.is_demo)
        db.add(plan)
        db.commit()
        return serialize(plan)
    sync_info, sync_error = {}, None
    try:
        sync_info = sync(db, client or analytics_client_for(db, ch), ch)
    except BlockedByCredentials as e:
        sync_error = str(e)
    except Exception as e:  # noqa: BLE001 — mất mạng/API lỗi: vẫn tính trên số liệu đã lưu
        log.warning("Ban Tăng trưởng: đồng bộ Analytics lỗi: %s", e, extra={"evcs_source": "pipeline"})
        sync_error = f"Đồng bộ YouTube Analytics lỗi: {type(e).__name__}: {e}"
    if sync_error:
        last = db.scalar(select(GrowthPlan).where(GrowthPlan.channel_id == ch.youtube_channel_id).order_by(GrowthPlan.at.desc()))
        sync_info = {**((last.status or {}).get("sync", {}) if last else {}), "error": sync_error}
    status = kpi_status(db, ch.youtube_channel_id, sync_info=sync_info)
    decisions, owner, limits = decide(db, status)
    if sync_error:
        owner.insert(0, {"title": "Chưa đồng bộ được số liệu kênh", "detail": sync_error})
    status["limits"] = limits
    applied = apply(db, decisions)
    last_memo = db.scalar(select(GrowthPlan).where(GrowthPlan.memo.is_not(None), GrowthPlan.channel_id == ch.youtube_channel_id)
                          .order_by(GrowthPlan.at.desc()))
    memo, memo_note = None, None
    due = force_memo or last_memo is None or (now() - last_memo.at.replace(tzinfo=last_memo.at.tzinfo or timezone.utc)) >= MEMO_EVERY
    if due and (status["views_28d"] or s.is_demo):
        memo, memo_note = write_memo(db, status, decisions, owner, brain=brain)
        if memo and memo.get("content_guidance") and applied:
            _set(db, S_GUIDANCE, memo["content_guidance"][:600])
    if memo_note:
        status["memo_note"] = memo_note
    plan = GrowthPlan(month=status["month"], channel_id=ch.youtube_channel_id, kpi_usd=status["kpi_usd"], status=status,
                      decisions=decisions, owner_actions=owner, memo=memo, applied=applied, actor=actor,
                      is_demo=bool(sync_info.get("is_demo", s.is_demo)))
    db.add(plan)
    audit(db, actor, "growth.tick", "growth_plan", None, applied=applied,
          changes={d["key"]: d["to"] for d in decisions if d.get("applied")}, kpi=status["kpi_usd"], projected=status["projected_usd"])
    db.commit()
    return serialize(plan)


def serialize(p: GrowthPlan) -> dict:
    return {"id": p.id, "at": p.at, "month": p.month, "kpi_usd": p.kpi_usd, "status": p.status, "decisions": p.decisions,
            "owner_actions": p.owner_actions, "memo": p.memo, "applied": p.applied, "actor": p.actor, "is_demo": p.is_demo}
