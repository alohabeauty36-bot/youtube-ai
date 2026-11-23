"""Tìm hình cho TỪNG ĐOẠN lời đọc, theo thứ tự ưu tiên:

1. Ảnh thật trên mạng có giấy phép máy đọc được (thư viện chủ kênh → Openverse → Wikimedia → Pexels → Pixabay),
   từng ảnh qua license gate rồi mới tải.
2. Chỉ khi KHÔNG có ảnh hợp quyền: gọi bộ tạo ảnh AI (Gemini) — ảnh luôn dán nhãn "minh họa tạo bằng AI".
   Không tạo ảnh AI cho đoạn nói về sự cố/sự kiện có thật, và có trần số ảnh AI mỗi video.
3. Nếu vẫn không có: cảnh của đoạn dùng đồ họa nội bộ (như trước).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...errors import BlockedByCredentials, PermanentError, RetryableError
from ...models import Asset, AssetLicense, CostUsage, now
from ...security import audit
from ..ranker import strip_accents
from .finders import FoundAsset, detect_model, download, norm_model
from .generators import as_found_asset, is_sensitive
from .license_gate import evaluate, file_sha256
from .visual_plan import ParagraphVisual

log = logging.getLogger(__name__)
MAX_REJECTED_ROWS_PER_PARAGRAPH = 3
FINDER_FAILS_BEFORE_SKIP = 2


@dataclass
class ParagraphReport:
    paragraph_idx: int
    need: int
    found: int = 0
    source: str = "graphics"  # web | own_library | ai | graphics | skipped
    asset_ids: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    note: str = ""


def spread(items: list, k: int) -> list:
    """Chọn k phần tử trải đều trong danh sách (giữ thứ tự) — chia ảnh AI đều trên toàn video."""
    if k <= 0:
        return []
    if k >= len(items):
        return list(items)
    step = len(items) / k
    return [items[int(i * step + step / 2)] for i in range(k)]


def origin_key(fa: FoundAsset) -> str:
    return (fa.source_url or fa.local_path or fa.download_url or "")[:1000]


class ParagraphSourcer:
    def __init__(self, session: Session, story, script_version, finders: list, generator, assets_dir: Path,
                 story_models: list[str]):
        self.s, self.story, self.sv = session, story, script_version
        self.finders, self.generator, self.adir = finders, generator, assets_dir
        self.models = story_models
        self.cache: dict[tuple[str, str], list[FoundAsset]] = {}
        self.finder_fails: dict[str, int] = {}
        self.warnings: list[str] = []
        st = get_settings()
        existing = list(session.scalars(select(Asset).where(Asset.story_id == story.id)))
        self.rejected_keys = {a.origin_key for a in existing if a.review_status == "rejected" and a.origin_key}
        self.used_keys = {a.origin_key for a in existing if a.review_status == "approved" and a.origin_key
                          and a.script_version_id == script_version.id}
        self.ai_used = sum(1 for a in existing if a.is_ai_generated and a.script_version_id == script_version.id)
        self.ai_budget = max(0, st.gemini_max_images_per_video)
        self.ai_blocked: str | None = None

    # ------------------------------------------------------------ search
    def _search(self, finder, query: str) -> list[FoundAsset]:
        key = (finder.name, query)
        if key in self.cache:
            return self.cache[key]
        if self.finder_fails.get(finder.name, 0) >= FINDER_FAILS_BEFORE_SKIP:
            return []
        try:
            res = finder.search(query, limit=5)
        except RetryableError as e:
            self.finder_fails[finder.name] = self.finder_fails.get(finder.name, 0) + 1
            if self.finder_fails[finder.name] == FINDER_FAILS_BEFORE_SKIP:
                self.warnings.append(f"{finder.name}: tạm bỏ qua sau {FINDER_FAILS_BEFORE_SKIP} lỗi ({e})")
            res = []
        self.cache[key] = res
        return res

    def _normalize_model(self, fa: FoundAsset) -> None:
        if fa.model_name:
            return
        found = detect_model(" ".join(x for x in (fa.scene_description, fa.extra.get("text")) if x))
        if found:
            match = next((m for m in self.models if norm_model(m) == norm_model(found)), None)
            fa.model_name = match or found

    # ------------------------------------------------------------ persist
    def _persist(self, fa: FoundAsset, paragraph_idx: int | None, query: str | None, dec) -> Asset:
        a = Asset(story_id=self.story.id, provider=fa.provider, source_url=fa.source_url, creator=fa.creator,
                  model_name=fa.model_name, year=fa.year, scene_description=fa.scene_description,
                  review_status=dec.status, rejection_reason=None if dec.status == "approved" else dec.reason,
                  is_ai_generated=fa.is_ai_generated, is_illustration=dec.is_illustration, is_demo=fa.is_demo,
                  script_version_id=self.sv.id if paragraph_idx is not None else None, paragraph_idx=paragraph_idx,
                  search_query=(query or None) and query[:300], origin_key=origin_key(fa) or None)
        a.license = AssetLicense(license_name=fa.license_name, license_url=fa.license_url,
                                 allowed_commercial=fa.allowed_commercial, attribution_required=fa.attribution_required,
                                 attribution_text=fa.attribution_text, permission_proof=fa.permission_proof)
        self.s.add(a)
        audit(self.s, "system", f"asset.{dec.status}", "asset", None, reason=dec.reason, provider=fa.provider,
              paragraph=paragraph_idx if paragraph_idx is not None else "", query=(query or "")[:200])
        return a

    def _accept(self, fa: FoundAsset, paragraph_idx: int | None, query: str | None, dec) -> Asset | None:
        """Tải (chỉ sau khi gate cho phép) rồi lưu. Tải lỗi → lưu dạng rejected, thử ảnh khác."""
        try:
            path = download(fa, self.adir)
        except (RetryableError, PermanentError) as e:
            self.rejected_keys.add(origin_key(fa))
            bad = type(dec)("rejected", f"Tải ảnh lỗi: {e}")
            self._persist(fa, paragraph_idx, query, bad)
            return None
        a = self._persist(fa, paragraph_idx, query, dec)
        a.local_path, a.content_hash, a.downloaded_at = str(path), file_sha256(path), now()
        self.used_keys.add(origin_key(fa))
        return a

    # ------------------------------------------------------------ story-level (thư viện chủ kênh, ảnh dùng chung)
    def library_pass(self, query: str) -> tuple[int, int]:
        approved = rejected = 0
        for finder in self.finders:
            if finder.name != "own_library":
                continue
            for fa in self._search(finder, query):
                k = origin_key(fa)
                if k in self.rejected_keys or self.s.scalar(select(Asset.id).where(
                        Asset.story_id == self.story.id, Asset.origin_key == k, Asset.paragraph_idx.is_(None))):
                    continue
                dec = evaluate(fa.__dict__, story_models=self.models)
                if dec.status == "approved":
                    if self._accept(fa, None, query, dec):
                        approved += 1
                else:
                    self.rejected_keys.add(k)
                    self._persist(fa, None, query, dec)
                    rejected += 1
        return approved, rejected

    # ------------------------------------------------------------ từng đoạn
    def search_web(self, text: str, plan: ParagraphVisual, need: int) -> ParagraphReport:
        rep = ParagraphReport(plan.paragraph_idx, need, queries=list(plan.search_queries))
        have = list(self.s.scalars(select(Asset).where(
            Asset.story_id == self.story.id, Asset.script_version_id == self.sv.id, Asset.paragraph_idx == plan.paragraph_idx,
            Asset.review_status == "approved")))
        rep.asset_ids = [a.id for a in have]
        rep.found = len(have)
        if have:
            rep.source = "ai" if all(a.is_ai_generated for a in have) else "web"
        if rep.found >= need:
            rep.note = "Đã có ảnh từ lần chạy trước"
            return rep
        if any(a.is_ai_generated for a in have):
            rep.note = "Đã có ảnh AI từ lần chạy trước"
            return rep  # không tìm/tạo lại cho đoạn đã có ảnh AI
        if plan.skip_image:
            rep.source, rep.note = "skipped", plan.reason or "Đoạn dùng đồ họa nội bộ"
            return rep
        # 1) Ảnh thật trên mạng có giấy phép
        rep.note = ""
        rejected_rows = 0
        lib_query = f"{text} {strip_accents(text)}"
        for finder in self.finders:
            queries = [lib_query] if finder.name == "own_library" else plan.search_queries
            for q in queries:
                if rep.found >= need:
                    break
                approved_here: list[tuple[FoundAsset, object]] = []
                for fa in self._search(finder, q):
                    if finder.name == "own_library" and fa.extra.get("generic"):
                        continue  # ảnh dùng chung của thư viện → xếp ở mức story, không coi là ảnh của đoạn
                    k = origin_key(fa)
                    if not k or k in self.rejected_keys or k in self.used_keys:
                        continue
                    self._normalize_model(fa)
                    dec = evaluate(fa.__dict__, story_models=self.models)
                    if dec.status == "approved":
                        approved_here.append((fa, dec))
                    else:
                        self.rejected_keys.add(k)
                        if rejected_rows < MAX_REJECTED_ROWS_PER_PARAGRAPH:
                            self._persist(fa, plan.paragraph_idx, q, dec)
                            rejected_rows += 1
                # ưu tiên ảnh đúng chủ đề (không bị hạ xuống "minh họa"), sau đó theo thứ hạng của nguồn
                approved_here.sort(key=lambda x: (x[1].is_illustration,))
                for fa, dec in approved_here:
                    if rep.found >= need:
                        break
                    a = self._accept(fa, plan.paragraph_idx, q, dec)
                    if a is not None:
                        rep.asset_ids.append(a.id)
                        rep.found += 1
                        rep.source = "own_library" if fa.provider == "own_library" else "web"
            if rep.found >= need:
                break
        if rep.found < need:
            rep.note = "Không tìm được ảnh hợp quyền trên mạng"
        return rep

    # ------------------------------------------------------------ 2) ảnh AI cho đoạn còn thiếu
    def generate_ai(self, text: str, plan: ParagraphVisual, rep: ParagraphReport) -> ParagraphReport:
        """Chỉ gọi cho đoạn đã tìm web mà vẫn thiếu ảnh. Tối đa 1 ảnh AI mỗi đoạn."""
        reason = self._ai_unavailable_reason(text)
        if reason:
            if not rep.found:
                rep.note = reason
            return rep
        dest = self.adir / f"ai_v{self.sv.version}_p{plan.paragraph_idx:03d}_{uuid.uuid4().hex[:8]}.png"  # không ghi đè ảnh cũ
        try:
            img = self.generator.generate(plan.image_prompt or text[:300], dest)
        except BlockedByCredentials as e:
            self.ai_blocked = str(e)
            self.warnings.append(f"Tạo ảnh AI bị chặn: {e}")
            rep.note = "Không tạo được ảnh AI (thiếu/sai key) → đồ họa nội bộ"
            return rep
        except (PermanentError, RetryableError) as e:
            self.warnings.append(f"Đoạn {plan.paragraph_idx}: bộ tạo ảnh không trả ảnh ({e})")
            rep.note = "Bộ tạo ảnh không trả ảnh → đồ họa nội bộ"
            return rep
        fa = as_found_asset(img, self.generator.name, plan.image_prompt or "Ảnh minh họa tạo bằng AI")
        dec = evaluate(fa.__dict__, story_models=self.models)
        a = self._persist(fa, plan.paragraph_idx, None, dec)
        if dec.status == "approved":
            a.local_path, a.content_hash, a.downloaded_at = str(img.path), file_sha256(img.path), now()
            rep.asset_ids.append(a.id)
            rep.found += 1
            if rep.source == "graphics":
                rep.source = "ai"
            rep.note = "Không tìm được ảnh hợp quyền trên mạng → ảnh minh họa AI"
        self.ai_used += 1
        if not img.is_demo:
            self.s.add(CostUsage(story_id=self.story.id, job_type="MediaJob", service=self.generator.name, units=1,
                                 unit_type="images", estimated_usd=img.usage_usd, is_demo=False))
        return rep

    def ai_remaining(self) -> int:
        return 0 if self.generator is None or self.ai_blocked else max(0, self.ai_budget - self.ai_used)

    def ai_skip_reason(self, text: str) -> str | None:
        """Lý do không tạo ảnh AI cho đoạn (không tính ngân sách)."""
        if self.generator is None:
            return "Không tìm được ảnh hợp quyền; chưa bật tạo ảnh AI → đồ họa nội bộ"
        if self.ai_blocked:
            return "Tạo ảnh AI đang bị chặn (key) → đồ họa nội bộ"
        if is_sensitive(text):
            return "Đoạn nói về sự cố/sự kiện có thật — không dựng ảnh AI → đồ họa nội bộ"
        return None

    def _ai_unavailable_reason(self, text: str) -> str | None:
        if self.generator is None:
            return "Không tìm được ảnh hợp quyền; chưa bật tạo ảnh AI → đồ họa nội bộ"
        if self.ai_blocked:
            return "Tạo ảnh AI đang bị chặn (key) → đồ họa nội bộ"
        if is_sensitive(text):
            return "Đoạn nói về sự cố/sự kiện có thật — không dựng ảnh AI → đồ họa nội bộ"
        if self.ai_used >= self.ai_budget:
            return f"Đã dùng hết trần {self.ai_budget} ảnh AI cho video → đồ họa nội bộ"
        return None
