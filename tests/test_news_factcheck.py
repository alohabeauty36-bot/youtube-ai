"""Ingest: 10 URL mẫu (2 trùng + 1 thiếu nguồn), xác minh 2 lớp, chấm điểm, chặn tin đồn."""
from sqlalchemy import select

from ev_studio.services import fact_checker as fc
from ev_studio.services import ranker


def test_canonical_url_strips_tracking_and_www():
    a = ranker.canonical_url("https://www.example.org/a/b/?utm_source=fb&x=1#top")
    b = ranker.canonical_url("http://example.org/a/b?x=1&utm_campaign=z")
    assert a == b == "https://example.org/a/b?x=1"


def test_scan_dedupes_and_blocks_missing_source(seeded, db):
    from ev_studio.models import NewsItem

    assert seeded["fetched"] == 10
    assert seeded["duplicates"] == 2
    assert seeded["blocked"] == 1
    items = list(db.scalars(select(NewsItem)))
    blocked = [n for n in items if n.status == "blocked"]
    assert blocked and "Thiếu nguồn" in blocked[0].block_reason
    dups = [n for n in items if n.status == "duplicate"]
    assert all(d.duplicate_of for d in dups)


def test_scoring_buckets_and_rumor_penalty(seeded, db):
    from ev_studio.models import NewsItem

    by_id = {n.title: n for n in db.scalars(select(NewsItem).where(NewsItem.status != "duplicate"))}
    offer = next(n for t, n in by_id.items() if "ưu đãi sạc" in t)
    rumor = next(n for t, n in by_id.items() if "Nghe nói" in t)
    assert offer.score >= 80 and offer.status == "shortlist" and offer.pillar == "P3"
    assert rumor.score < 60 and any("đồn đoán" in r for r in rumor.score_reasons)
    assert set(offer.score_breakdown) == {"freshness", "impact", "credibility", "explainability", "asset_feasibility", "novelty"}


def test_contested_claims_marked(seeded, db):
    from ev_studio.models import Claim, NewsItem

    item = db.scalar(select(NewsItem).where(NewsItem.title.contains("DEMO-Volt")))
    sts = {c.claim_key: c.status for c in item.claims}
    assert set(sts) == {"c1", "c2"} and set(sts.values()) <= {"contested", "removed"}  # 'removed' nếu test API đã xử lý
    assert item.status != "shortlist"
    assert all(fc.NEEDS_REVIEW_MARK in c.verification_reason for c in item.claims)


def test_sensitive_claim_requires_primary_or_two_sources():
    refs = {"s1": fc.SourceRef("s1", "https://a.vn/x", "Báo A", 2, None, "giá xe là 300 triệu đồng"),
            "s2": fc.SourceRef("s2", "https://b.vn/y", "Báo B", 2, None, "giá xe là 300 triệu đồng"),
            "s3": fc.SourceRef("s3", "https://hang.vn/z", "Hãng", 1, None, "giá xe là 300 triệu đồng")}
    c = {"claim_key": "c1", "text": "Giá 300 triệu", "claim_type": "price", "source_url": "https://a.vn/x",
         "evidence_excerpt": "giá xe là 300 triệu đồng"}
    assert fc.verify_claim(c | {"source_ids": ["s1"]}, refs, analysis=False)[0] == "unverified"
    assert fc.verify_claim(c | {"source_ids": ["s1", "s2"]}, refs, analysis=False)[0] == "verified"
    assert fc.verify_claim(c | {"source_ids": ["s3"]}, refs, analysis=False)[0] == "verified"
    # chế độ phân tích: một báo đưa → "đã đưa tin", dùng được khi ghi "theo Báo A"
    st, why = fc.verify_claim(c | {"source_ids": ["s1"]}, refs, analysis=True)
    assert st == "reported" and "theo Báo A" in why
    assert fc.verify_claim(c | {"source_ids": ["s1", "s2"]}, refs, analysis=True)[0] == "verified"


def test_evidence_must_match_source_text():
    refs = {"s1": fc.SourceRef("s1", "https://a.vn/x", "Hãng", 1, None, "Ưu đãi đến hết 31/12.")}
    c = {"claim_key": "c1", "text": "Miễn phí vĩnh viễn", "claim_type": "charging_offer", "source_url": "https://a.vn/x",
         "source_ids": ["s1"], "evidence_excerpt": "miễn phí sạc vĩnh viễn"}
    for mode in (False, True):  # trích dẫn không có trong bài gốc = có thể bịa → loại ở CẢ HAI chế độ
        st, reason = fc.verify_claim(c, refs, analysis=mode)
        assert st == "unverified" and "không khớp" in reason


def test_no_source_is_unverified_and_blocks():
    out = fc.check_claims([{"claim_key": "c1", "text": "x", "claim_type": "price", "source_ids": [], "is_key": True}], {})
    assert out[0]["status"] == "unverified"
    assert fc.blocking_claims(out, analysis=False)
    # chế độ phân tích không chặn vì claim chưa xác minh, nhưng claim không nguồn KHÔNG phải dữ kiện dùng được
    assert fc.blocking_claims(out, analysis=True) == [] and not fc.is_fact(out[0]["status"])


def test_analysis_mode_attribution_rules():
    assert fc.has_attribution("Theo VnExpress, giá xe giảm 10%.", [])
    assert fc.has_attribution("Tuổi Trẻ đưa tin hãng sẽ mở thêm trạm.", [])
    assert fc.has_attribution("Báo Dân trí: hãng sẽ mở thêm trạm.", ["Dân trí"])
    assert not fc.has_attribution("Giá xe giảm 10% từ tháng sau.", ["VnExpress"])
    assert "CHẾ ĐỘ PHÂN TÍCH" in fc.ANALYSIS_SCRIPT_NOTE and "không bịa" in fc.ANALYSIS_SCRIPT_NOTE.lower()


def test_script_qa_requires_source_for_reported_claims():
    from ev_studio.services.script_qa import run_script_qa

    pack = [{"claim_key": "c1", "text": "Giá xe giảm 10%", "status": "reported", "source_ids": ["s1"], "is_key": True}]

    def out(excerpt):
        return {"spoken_script": excerpt + " Hẹn gặp lại.", "closing_teaser": "Hẹn gặp lại.",
                "editorial_claim_map": [{"claim_id": "c1", "source_ids": ["s1"], "relevant_script_excerpt": excerpt}],
                "title_options": [{"title": "a"}, {"title": "b"}, {"title": "c"}], "production_notes": {}}

    def check(o, analysis):
        qa = run_script_qa(o, pack_claims=pack, source_texts=[], recent_scripts=[], analysis=analysis, publishers=["VnExpress"])
        return {c["name"]: c["ok"] for c in qa.checks}

    good = check(out("Theo VnExpress, giá xe giảm 10%."), True)
    assert good["Tin chưa xác minh được ghi rõ nguồn (\"theo <báo>\")"] and good["Có ít nhất một claim trọng yếu được dẫn"]
    bad = check(out("Giá xe giảm 10%."), True)
    assert not bad["Tin chưa xác minh được ghi rõ nguồn (\"theo <báo>\")"]
    strict = check(out("Theo VnExpress, giá xe giảm 10%."), False)  # chế độ chặt: tin chưa xác minh không được dùng
    assert not strict["Claim map chỉ dùng claim đã xác minh"]


def test_inference_never_promoted():
    out = fc.check_claims([{"claim_key": "c9", "text": "Có thể đông hơn", "status": "inference", "source_ids": ["s1"],
                            "source_url": "https://a"}], {"s1": fc.SourceRef("s1", "https://a", "A", 1)})
    assert out[0]["status"] == "inference"


def test_reapply_fact_mode_promotes_single_source_news(db):
    """Tin quét trước khi bật chế độ phân tích: claim một báo đưa → 'reported', tin đủ điểm lên shortlist; chế độ chặt hạ lại."""
    from ev_studio.config import get_settings
    from ev_studio.models import Claim, NewsItem
    from ev_studio.services import ingest

    s = get_settings()
    text = "Hãng công bố giá xe là 300 triệu đồng từ tháng 12."
    n = NewsItem(title="Giá xe mới", url="https://a.vn/gia", canonical_url="https://a.vn/gia-reapply", publisher="Báo A",
                 raw_excerpt=text, article_hash="reapply-test", trust_tier=2, pillar="P1", score=s.score_shortlist + 1,
                 status="research", supporting_sources=[{"source_id": "s1", "url": "https://a.vn/gia", "publisher": "Báo A",
                                                         "trust_tier": 2, "text": text}])
    n.claims.append(Claim(claim_key="c1", text="Giá 300 triệu", claim_type="price", source_url="https://a.vn/gia",
                          source_ids=["s1"], evidence_excerpt="giá xe là 300 triệu đồng", status="unverified", is_key=True,
                          verification_reason="Claim nhạy cảm cần nguồn cấp 1 hoặc ≥2 nguồn uy tín độc lập"))
    db.add(n)
    db.flush()
    try:
        assert s.fact_mode == "analysis"  # mặc định
        assert ingest.reapply_fact_mode(db) >= 1
        assert n.status == "shortlist" and n.claims[0].status == "reported"
        s.fact_mode = "strict"
        ingest.reapply_fact_mode(db)
        assert n.status == "research" and n.claims[0].status == "unverified"
    finally:
        s.fact_mode = "analysis"
        db.rollback()
