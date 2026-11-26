"""Ảnh minh họa AI: Cloudflare Workers AI (miễn phí, hạn mức ngày) trước, fal.ai (trả phí, qua Ban Tài chính) sau."""
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import delete

from ev_studio.config import get_settings
from ev_studio.services.media import ai_images as ai
from ev_studio.services.media.license_gate import evaluate


def jpeg(w=1024, h=1024, color=(30, 90, 160)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def cf_client(status=200, calls=None):
    def h(req: httpx.Request):
        if calls is not None:
            calls.append(json.loads(req.content))
        assert req.url.path.endswith("/ai/run/@cf/black-forest-labs/flux-1-schnell")
        assert req.headers["Authorization"] == "Bearer tok"
        if status != 200:
            return httpx.Response(status, json={"success": False, "errors": [{"message": "quota"}]})
        return httpx.Response(200, json={"success": True, "result": {"image": base64.b64encode(jpeg()).decode()}})
    return httpx.Client(transport=httpx.MockTransport(h))


def fal_client(calls=None, nsfw=False):
    def h(req: httpx.Request):
        if calls is not None:
            calls.append(json.loads(req.content))
        assert str(req.url) == "https://fal.run/fal-ai/flux-1/schnell" and req.headers["Authorization"] == "Key fk"
        data = "data:image/jpeg;base64," + base64.b64encode(jpeg(1024, 576)).decode()
        return httpx.Response(200, json={"images": [{"url": data, "content_type": "image/jpeg"}], "seed": 1,
                                         "has_nsfw_concepts": [nsfw], "prompt": "x"})
    return httpx.Client(transport=httpx.MockTransport(h))


def settings(**kw):
    from pydantic import SecretStr

    base = {"cloudflare_account_id": "acc", "cloudflare_api_token": SecretStr("tok"), "fal_api_key": SecretStr("fk"),
            "ai_image_provider": "auto", "cloudflare_daily_images": 2}
    return get_settings().model_copy(update={**base, **kw})


@pytest.fixture()
def clean_counts(db_ready):
    from ev_studio.db import SessionLocal
    from ev_studio.models import AppSetting, BudgetLedger

    yield
    with SessionLocal() as s:
        s.execute(delete(AppSetting).where(AppSetting.key.like("ai_images_count:%")))
        s.execute(delete(BudgetLedger).where(BudgetLedger.provider == "fal"))
        s.commit()


def test_prompt_strips_brands_and_numbers():
    p = ai.build_prompt("VinFast VF 8 at V-Green station 150 kW", None)
    low = p.lower()
    assert "vinfast" not in low and "vf" not in low.split() and "v-green" not in low and "150" not in p
    assert "no text" in low and "no logos" in low and "no license plates" in low and "faces" in low


def test_cloudflare_first_then_fal_when_daily_cap_reached_and_budget_recorded(tmp_path, clean_counts):
    from ev_studio.db import SessionLocal
    from ev_studio.models import BudgetLedger

    cf_calls, fal_calls = [], []
    gens = {"cloudflare": ai.CloudflareImages("acc", "tok", client=cf_client(calls=cf_calls)),
            "fal": ai.FalImages("fk", client=fal_client(calls=fal_calls))}
    with SessionLocal() as db:
        m = ai.AIImageMaker(settings(), tmp_path, db, story_id=None, generators=gens)
        out = [m.make(q, None, seed=i) for i, q in enumerate(["city street", "ev charger", "highway at dusk"])]
        assert [g.provider for g in out] == ["cloudflare", "cloudflare", "fal"]  # Cloudflare hết 2 ảnh/ngày → fal.ai
        assert all(Image.open(g.path).size == (1920, 1080) for g in out)
        assert out[2].usd == pytest.approx(0.003)
        assert fal_calls[0]["image_size"] == "landscape_16_9" and fal_calls[0]["enable_safety_checker"] is True
        assert cf_calls[0]["steps"] == 4
        row = db.query(BudgetLedger).filter(BudgetLedger.provider == "fal").one()
        assert row.status == "settled" and row.usd == pytest.approx(0.003) and row.department == "dao_dien"
        again = m.make("city street", None, seed=0)  # cùng prompt + seed → lấy kho đệm, không tốn lượt
        assert again.cached and len(cf_calls) == 2


def test_failures_fall_through_and_nsfw_rejected(tmp_path, clean_counts):
    from ev_studio.db import SessionLocal

    gens = {"cloudflare": ai.CloudflareImages("acc", "tok", client=cf_client(status=429)),
            "fal": ai.FalImages("fk", client=fal_client(nsfw=True))}
    with SessionLocal() as db:
        m = ai.AIImageMaker(settings(), tmp_path, db, generators=gens)
        assert m.make("parking garage", None, seed=1) is None
        assert any("cloudflare" in n for n in m.notes) and any("an toàn" in n for n in m.notes)


def test_ai_asset_passes_license_gate_as_labeled_illustration(tmp_path):
    g = ai.Generated(tmp_path / "x.jpg", "fal", "fal-ai/flux-1/schnell", "prompt", False)
    fa = ai.as_found_asset(g, "ev charger")
    dec = evaluate(fa.__dict__, story_models=["VF 8"])
    assert dec.status == "approved" and dec.is_illustration
    assert fa.is_ai_generated and "AI" in fa.attribution_text and fa.source_url.startswith("ai://fal/")


def test_provider_order_respects_settings():
    from pydantic import SecretStr

    assert ai.providers_in_order(settings()) == ["cloudflare", "fal"]
    assert ai.providers_in_order(settings(ai_image_provider="fal")) == ["fal"]
    assert ai.providers_in_order(settings(ai_image_provider="off")) == []
    assert ai.providers_in_order(settings(cloudflare_api_token=None)) == ["fal"]
    assert ai.providers_in_order(settings(cloudflare_api_token=SecretStr("t"), fal_api_key=None)) == ["cloudflare"]


def test_connection_test_reports_each_provider():
    from ev_studio.services.connection_tests import test_ai_images

    gens = {"cloudflare": ai.CloudflareImages("acc", "tok", client=cf_client()),
            "fal": ai.FalImages("fk", client=fal_client())}
    r = test_ai_images(settings(), generators=gens)
    assert r["ok"] and "cloudflare: tạo được ảnh 1024×1024" in r["message"] and "fal: tạo được ảnh 1024×576" in r["message"]
