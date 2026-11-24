"""Ban Tài chính — trần chi tiêu AI CỨNG theo tháng, theo ngày, theo phòng ban và theo video.

Nguyên tắc:
1. Trước MỌI lần gọi Claude/ChatGPT phải `reserve()` số tiền xấu nhất (token vào ước tính dư + toàn bộ max_tokens ra).
   Vượt bất kỳ trần nào → `BudgetExceeded` → không gọi. Story chuyển NEEDS_REVIEW, không âm thầm tiêu tiếp.
2. Gọi xong `settle()` với usage thật; lỗi trước khi có usage → `release()`.
3. Sổ cái ghi bằng session riêng, commit ngay: job rollback hay process chết cũng không làm mất dấu tiền đã tiêu.
   Giữ chỗ quá 2 giờ mà chưa chốt được tính là đã tiêu (an toàn) cho tới khi có người kiểm tra.
4. Ngân sách ngày = phần còn lại của tháng (lúc đầu ngày) chia đều cho số ngày còn lại → không bao giờ tiêu lố tháng,
   ngày nào tiêu ít thì các ngày sau được nhiều hơn một chút, nhưng không quá 2× mức bình quân (50$ → tối đa ~3,2$/ngày).
"""
from __future__ import annotations

import calendar
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..config import get_settings
from ..errors import GateBlocked
from . import pricing

# phần trăm trần tháng dành cho từng phòng ban (tổng = 1.0)
DEPARTMENT_SHARES: dict[str, float] = {
    "san_tin": 0.15,
    "nghien_cuu": 0.25,
    "bien_kich": 0.50,
    "bien_tap": 0.07,
    "hoc_tap": 0.03,
}
DEFAULT_STORY_COST = 0.35  # ước tính ban đầu khi chưa có dữ liệu thật
STALE_RESERVATION = timedelta(hours=2)
DAY_CEILING_MULT = 2.0

_lock = threading.Lock()


class BudgetExceeded(GateBlocked):
    """Chạm trần ngân sách. Kế thừa GateBlocked → story dừng ở NEEDS_REVIEW, bấm 'Chạy tiếp' khi có ngân sách."""

    def __init__(self, scope: str, detail: str):
        self.scope = scope
        super().__init__(f"BLOCKED_BY_BUDGET ({scope}): {detail}")


@dataclass
class Window:
    month_start: datetime
    day_start: datetime
    days_in_month: int
    days_left: int  # tính cả hôm nay


def window(at: datetime | None = None) -> Window:
    tz = ZoneInfo(get_settings().timezone)
    local = (at or datetime.now(timezone.utc)).astimezone(tz)
    dim = calendar.monthrange(local.year, local.month)[1]
    m0 = datetime.combine(local.date().replace(day=1), time.min, tz).astimezone(timezone.utc)
    d0 = datetime.combine(local.date(), time.min, tz).astimezone(timezone.utc)
    return Window(m0, d0, dim, dim - local.day + 1)


def _session():
    from ..db import SessionLocal

    return SessionLocal()


def _spent(db, since: datetime, until: datetime | None = None, department: str | None = None,
           story_id: str | None = None) -> float:
    from ..models import BudgetLedger as L

    q = select(func.coalesce(func.sum(L.usd), 0.0)).where(L.at >= since, L.status.in_(("settled", "reserved")))
    if until is not None:
        q = q.where(L.at < until)
    if department:
        q = q.where(L.department == department)
    if story_id:
        q = q.where(L.story_id == story_id)
    return float(db.scalar(q) or 0.0)


def effective_cap() -> float:
    s = get_settings()
    return round(s.budget_monthly_usd * (1 - s.budget_safety_margin), 4)


def day_allowance(db, w: Window | None = None) -> float:
    w = w or window()
    before_today = _spent(db, w.month_start, w.day_start)
    cap = effective_cap()
    even = (cap - before_today) / max(1, w.days_left)
    return max(0.0, min(even, DAY_CEILING_MULT * cap / w.days_in_month))  # một ngày không bao giờ quá 2× mức bình quân


def reserve(department: str, provider: str, model: str, est_input_tokens: int, max_output_tokens: int,
            story_id: str | None = None, note: str = "") -> int:
    """Giữ chỗ chi phí xấu nhất. Trả id sổ cái. Raise BudgetExceeded nếu chạm bất kỳ trần nào."""
    from ..models import BudgetLedger

    s = get_settings()
    est = pricing.cost_usd(model, est_input_tokens, max_output_tokens, overrides_json=s.model_prices_json)
    with _lock, _session() as db:
        w = window()
        cap = effective_cap()
        month = _spent(db, w.month_start)
        if month + est > cap:
            raise BudgetExceeded("tháng", f"đã dùng ${month:.2f}/{cap:.2f}, lần gọi này cần tới ${est:.3f}")
        today = _spent(db, w.day_start)
        allow = day_allowance(db, w)
        if today + est > allow:
            raise BudgetExceeded("ngày", f"hôm nay đã dùng ${today:.2f}/{allow:.2f}, lần gọi này cần tới ${est:.3f}")
        share = DEPARTMENT_SHARES.get(department, 0.05)
        dept = _spent(db, w.month_start, department=department)
        if dept + est > cap * share:
            raise BudgetExceeded("phòng ban", f"{department} đã dùng ${dept:.2f}/{cap * share:.2f} của tháng")
        if story_id:
            st = _spent(db, w.month_start - timedelta(days=31), story_id=story_id)
            if st + est > s.budget_story_max_usd:
                raise BudgetExceeded("video", f"video này đã dùng ${st:.2f}/{s.budget_story_max_usd:.2f}")
        row = BudgetLedger(department=department, provider=provider, model=model, story_id=story_id, status="reserved",
                           input_tokens=est_input_tokens, output_tokens=max_output_tokens, usd=est, note=note[:200])
        db.add(row)
        db.commit()
        return row.id


def settle(rid: int, usage: dict) -> float:
    """Chốt chi phí thật từ usage {input_tokens, output_tokens, cache_write_tokens, cache_read_tokens}."""
    from ..models import BudgetLedger

    s = get_settings()
    with _session() as db:
        row = db.get(BudgetLedger, rid)
        if row is None:
            return 0.0
        row.input_tokens = int(usage.get("input_tokens", 0))
        row.output_tokens = int(usage.get("output_tokens", 0))
        row.cache_write_tokens = int(usage.get("cache_write_tokens", 0))
        row.cache_read_tokens = int(usage.get("cache_read_tokens", 0))
        row.usd = pricing.cost_usd(row.model, row.input_tokens, row.output_tokens, row.cache_write_tokens,
                                   row.cache_read_tokens, s.model_prices_json)
        row.status = "settled"
        db.commit()
        return row.usd


def release(rid: int) -> None:
    from ..models import BudgetLedger

    with _session() as db:
        row = db.get(BudgetLedger, rid)
        if row is not None and row.status == "reserved":
            row.status = "released"
            row.usd = 0.0
            db.commit()


def story_count_today(db) -> int:
    from ..models import Story

    w = window()
    return int(db.scalar(select(func.count(Story.id)).where(Story.created_at >= w.day_start, Story.parent_story_id.is_(None))) or 0)


def avg_story_cost(db) -> float:
    from ..models import BudgetLedger as L

    rows = db.execute(select(L.story_id, func.sum(L.usd)).where(L.story_id.is_not(None), L.status == "settled")
                      .group_by(L.story_id).order_by(func.max(L.at).desc()).limit(20)).all()
    vals = [float(v) for _, v in rows if v]
    return round(sum(vals) / len(vals), 4) if vals else DEFAULT_STORY_COST


def can_start_story(db) -> tuple[bool, str]:
    """Autopilot hỏi trước khi mở tin mới: còn suất trong ngày không, còn đủ tiền cho một video không."""
    s = get_settings()
    n = story_count_today(db)
    if n >= s.stories_per_day:
        return False, f"Hôm nay đã làm {n}/{s.stories_per_day} tin"
    w = window()
    left_today = day_allowance(db, w) - _spent(db, w.day_start)
    need = avg_story_cost(db)
    month_left = effective_cap() - _spent(db, w.month_start)
    if month_left < need:
        return False, f"Ngân sách tháng còn ${month_left:.2f}, không đủ cho một video (~${need:.2f})"
    if left_today < need:
        return False, f"Ngân sách hôm nay còn ${left_today:.2f}, không đủ cho một video (~${need:.2f})"
    return True, "ok"


def status(db) -> dict:
    from ..models import BudgetLedger as L

    s = get_settings()
    w = window()
    cap = effective_cap()
    month = _spent(db, w.month_start)
    today = _spent(db, w.day_start)
    allow = day_allowance(db, w)
    stale = db.scalar(select(func.count(L.id)).where(L.status == "reserved", L.at < datetime.now(timezone.utc) - STALE_RESERVATION)) or 0
    elapsed = max(1, w.days_in_month - w.days_left + 1)
    forecast = month / elapsed * w.days_in_month
    per_dept = []
    for d, share in DEPARTMENT_SHARES.items():
        used = _spent(db, w.month_start, department=d)
        per_dept.append({"department": d, "used_usd": round(used, 4), "cap_usd": round(cap * share, 4)})
    avg = avg_story_cost(db)
    ok, why = can_start_story(db)
    return {
        "monthly_cap_usd": s.budget_monthly_usd, "effective_cap_usd": cap, "safety_margin": s.budget_safety_margin,
        "spent_month_usd": round(month, 4), "remaining_month_usd": round(max(0.0, cap - month), 4),
        "spent_today_usd": round(today, 4), "allowance_today_usd": round(allow, 4),
        "days_left": w.days_left, "days_in_month": w.days_in_month,
        "forecast_month_usd": round(forecast, 2), "story_max_usd": s.budget_story_max_usd,
        "avg_story_usd": avg, "stories_today": story_count_today(db), "stories_per_day": s.stories_per_day,
        "stories_per_day_min": s.stories_per_day_min,
        "capacity_month_stories": int(max(0.0, cap - month) / avg) if avg else None,
        "can_start_story": ok, "reason": why, "stale_reservations": int(stale), "by_department": per_dept,
    }
