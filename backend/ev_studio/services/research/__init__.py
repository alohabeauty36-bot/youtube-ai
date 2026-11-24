"""Research web miễn phí cho Phòng Bài luận: tìm kiếm → tải trang → trích văn bản → xếp hạng đoạn → hồ sơ tư liệu.

Không gọi AI ở bước nào: toàn bộ chạy bằng code, nên khi Phòng Bài luận đạt cấp Bậc thầy thì viết bài luận
từ đầu đến cuối mà không tốn đồng nào cho Claude/ChatGPT.
"""
from .dossier import Dossier, Source, build_dossier, plan_queries  # noqa: F401
from .web import (  # noqa: F401
    BraveSearch, FixtureSearch, PageFetcher, SearchHit, SearxngSearch, WikipediaSearch, build_searchers, extract_text,
)
