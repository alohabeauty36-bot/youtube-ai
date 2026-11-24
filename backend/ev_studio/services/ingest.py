"""NewsIngestion: lấy tin → chuẩn hoá URL → chống trùng → claim → xác minh → chấm điểm → phân nhóm."""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Claim, NewsItem, Source, Story, now
from ..security import audit
from . import fact_checker as fc
from . import ranker
from .sources.fetcher import FixtureSourceAdapter, RawArticle, RSSSourceAdapter

log = logging.getLogger(__name__)


def adapters_for(session: Session) -> list:
    s = get_settings()
    if s.is_demo:
        return [FixtureSourceAdapter()]
    out = []
    for src in session.scalars(select(Source).where(Source.allowed.is_(True), Source.blocked.is_(False), Source.kind == "rss")):
        if src.feed_url:
            out.append(RSSSourceAdapter(src.feed_url, src.name, src.trust_tier, src.rate_limit_seconds))
    return out


def _sources_for(a: RawArticle, url: str) -> dict[str, fc.SourceRef]:
    from .sources.fetcher import _dt

    refs = {"s1": fc.SourceRef("s1", url, a.publisher or "", a.trust_tier,
                               a.published_at.isoformat() if a.published_at else None, a.raw_excerpt)}
    for s in a.supporting_sources:
        d = _dt(s.get("published_at"))
        refs[s["source_id"]] = fc.SourceRef(s["source_id"], s["url"], s.get("publisher", ""), int(s.get("trust_tier", 3)),
                                            d.isoformat() if d else None, s.get("text"))
    return refs


def ingest_articles(session: Session, articles: list[RawArticle], writer=None, actor: str = "system") -> dict:
    s = get_settings()
    stats = {"fetched": len(articles), "created": 0, "duplicates": 0, "blocked": 0, "shortlist": 0, "research": 0, "reference": 0}
    recent_topics = [t for (t,) in session.execute(select(Story.topic).order_by(Story.created_at.desc()).limit(50))]
    blocked_domains = {d for (d,) in session.execute(select(Source.domain).where(Source.blocked.is_(True)))}
    created: list[NewsItem] = []
    for a in articles:
        # 1. nguồn thiếu → chặn
        if not a.url or not a.publisher:
            item = NewsItem(title=a.title, url=a.url or "", canonical_url=a.url or f"missing:{ranker.article_hash(a.title, a.raw_excerpt)[:16]}",
                            publisher=a.publisher, raw_excerpt=a.raw_excerpt, article_hash=ranker.article_hash(a.title, a.raw_excerpt),
                            status="blocked", block_reason="Thiếu nguồn gốc (URL/nhà phát hành) — không sản xuất video",
                            trust_tier=a.trust_tier, is_demo=a.is_demo, flags=a.flags)
            session.add(item)
            stats["blocked"] += 1
            continue
        canon = ranker.canonical_url(a.url)
        domain = canon.split("/")[2]
        if domain in blocked_domains:
            stats["blocked"] += 1
            continue
        ahash = ranker.article_hash(a.title, a.raw_excerpt)
        # 2. chống trùng: canonical_url, hash nội dung, tiêu đề gần giống + cùng model trong 3 ngày
        dup = session.scalar(select(NewsItem).where((NewsItem.canonical_url == canon) | (NewsItem.article_hash == ahash)).limit(1))
        if dup is None:
            dup = next((c for c in created if c.canonical_url == canon or c.article_hash == ahash), None)
        if dup is None and a.published_at:
            window = [c for c in created] + list(session.scalars(select(NewsItem).where(NewsItem.status != "duplicate")
                                                                     .order_by(NewsItem.fetched_at.desc()).limit(200)))
            for c in window:
                same_day = c.published_at and abs((c.published_at.replace(tzinfo=a.published_at.tzinfo) - a.published_at)) < timedelta(days=3)
                if same_day and ranker.jaccard(c.title, a.title) >= 0.85 and set(c.model_names) == set(a.model_names):
                    dup = c
                    break
        if dup is not None:
            session.add(NewsItem(title=a.title, url=a.url, canonical_url=canon, publisher=a.publisher, published_at=a.published_at,
                                 raw_excerpt=a.raw_excerpt, article_hash=ahash, status="duplicate", duplicate_of=dup.id,
                                 trust_tier=a.trust_tier, is_demo=a.is_demo))
            stats["duplicates"] += 1
            continue
        # 3. claim: fixture có sẵn; production → Claude trích xuất (trích dẫn phải khớp văn bản)
        raw_claims = a.claims
        if raw_claims is None and writer is not None:
            _p, hits0 = ranker.classify_pillar(a.title, a.raw_excerpt)
            ev_terms = ("xe dien", "vinfast", "o to dien", "xe may dien", "tram sac", "pin", "sac ")
            relevant = sum(hits0.values()) > 0 or any(t in ranker.strip_accents(a.title + " " + a.raw_excerpt) for t in ev_terms)
            if not getattr(writer, "is_demo", False) and not relevant:
                raw_claims = []  # không thuộc trụ cột P1/P2/P3 → không tốn tiền tách claim
                stats["skipped_offtopic"] = stats.get("skipped_offtopic", 0) + 1
            else:
                from ..agents.budget import BudgetExceeded
                from ..workers.jobs import record_cost

                session.commit()  # SQLite: nhả khoá ghi để Ban Tài chính ghi sổ cái trong lúc chờ AI
                try:
                    raw_claims, usage = writer.extract_claims(a.title, a.raw_excerpt)
                except BudgetExceeded as e:
                    stats["budget_skipped"] = stats.get("budget_skipped", 0) + 1  # để lần quét sau làm tiếp
                    stats["budget_reason"] = e.reasons[0]
                    continue
                record_cost(session, None, "ClaimExtraction", usage, getattr(writer, "is_demo", False))
            for c in raw_claims:
                c.setdefault("source_ids", ["s1"])
        raw_claims = raw_claims or []
        refs = _sources_for(a, ranker.canonical_url(a.url))
        for c in raw_claims:
            c["source_url"] = next((refs[sid].url for sid in c.get("source_ids", []) if sid in refs), None)
        checked = fc.check_claims(raw_claims, refs)
        # 4. chấm điểm
        pillar, hits = ranker.classify_pillar(a.title, a.raw_excerpt)
        key = [c for c in checked if c.get("is_key", True) and c["status"] != "inference"]
        verified_key = [c for c in key if c["status"] == "verified"]
        contested = [c for c in checked if c["status"] == "contested"]
        novelty = max([ranker.jaccard(a.title, t) for t in recent_topics] or [0])
        has_cond = any(w in ranker.strip_accents(a.raw_excerpt) for w in ("chi ap dung", "dieu kien", "den het", "khong ap dung", "hieu luc"))
        res = ranker.score_news(
            title=a.title, excerpt=a.raw_excerpt, published_at=a.published_at, trust_tier=a.trust_tier,
            n_supporting=len(a.supporting_sources), n_verified_claims=len(verified_key), n_key_claims=len(key) or 1,
            n_contested=len(contested), has_conditions=has_cond, asset_feasibility=7.0, novelty_similarity=novelty,
            flags=a.flags, pillar_hits=hits,
        )
        status = ranker.bucket(res.total, s.score_shortlist, s.score_research)
        if not key or not verified_key:
            status = "research" if status == "shortlist" else status
        item = NewsItem(
            title=a.title, url=a.url, canonical_url=canon, publisher=a.publisher, published_at=a.published_at,
            event_at=a.event_at, effective_from=a.effective_from, effective_to=a.effective_to,
            raw_excerpt=a.raw_excerpt, article_hash=ahash, model_names=a.model_names, entities=a.entities,
            supporting_sources=[{**r.__dict__, "text": (r.text or "")[:4000]} for r in refs.values()],
            contradictions=[c["claim_key"] for c in contested], trust_tier=a.trust_tier, flags=a.flags, pillar=pillar,
            score=res.total, score_breakdown=res.breakdown, score_reasons=res.reasons + res.penalties,
            curiosity_title=None, status=status, is_demo=a.is_demo,
        )
        for c in checked:
            item.claims.append(Claim(
                claim_key=c["claim_key"], text=c["text"], claim_type=c.get("claim_type", "general"), subject=c.get("subject"),
                value=str(c["value"]) if c.get("value") is not None else None, unit=c.get("unit"), date_scope=c.get("date_scope"),
                source_url=c.get("source_url") if c["status"] == "verified" else c.get("source_url"),
                source_ids=c.get("source_ids", []), evidence_excerpt=c.get("evidence_excerpt"), status=c["status"],
                is_key=c.get("is_key", True), sensitive=c["sensitive"], volatile=c["volatile"],
                verification_reason=c.get("verification_reason"),
            ))
        session.add(item)
        created.append(item)
        if not a.is_demo:
            from ..agents.learning import remember_claims

            remember_claims(session, a.title, checked)  # kiến thức đã xác minh → các phòng tra cứu về sau
        stats["created"] += 1
        stats[status] = stats.get(status, 0) + 1
    session.flush()
    _cross_item_conflicts(session, created)
    audit(session, actor, "news.scan", "news_items", None, **stats)
    return stats


def _cross_item_conflicts(session: Session, created: list[NewsItem]) -> None:
    """Hai bài khác nhau đưa số liệu khác nhau cho cùng (loại claim, chủ thể) → contested ở cả hai."""
    recent = list(session.scalars(select(Claim).join(NewsItem).where(NewsItem.status != "duplicate", Claim.subject.is_not(None))))
    groups: dict[tuple, list[Claim]] = {}
    for c in recent:
        groups.setdefault((c.claim_type, ranker.strip_accents(c.subject or "")), []).append(c)
    for items in groups.values():
        vals = {ranker.strip_accents(i.value or "") for i in items if i.status in ("verified", "contested")}
        if len(vals) > 1 and len({i.news_item_id for i in items}) > 1:
            for i in items:
                if i.status == "verified":
                    i.status = "contested"
                    i.verification_reason = f"{fc.NEEDS_REVIEW_MARK} Mâu thuẫn với bài khác (giá trị: {', '.join(sorted(vals))})"
                    ni = session.get(NewsItem, i.news_item_id)
                    if ni and ni.status == "shortlist":
                        ni.status = "research"


def run_scan(session: Session, writer=None, actor: str = "system") -> dict:
    total = {"fetched": 0, "created": 0, "duplicates": 0, "blocked": 0}
    for ad in adapters_for(session):
        arts = ad.fetch()
        st = ingest_articles(session, arts, writer=writer, actor=actor)
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
    for src in session.scalars(select(Source)):
        src.last_fetched_at = now()
    return total
