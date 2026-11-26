"""Các phòng ban của tòa soạn. Mỗi phòng là một bộ não độc lập: bộ nhớ riêng, bài học riêng, hồ sơ năng lực riêng,
trần ngân sách riêng (xem budget.DEPARTMENT_SHARES)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Department:
    code: str
    name: str
    role: str
    model_setting: str  # tên trường Settings chọn model của thầy Claude
    max_tokens: int
    similarity_threshold: float  # trò phải viết giống thầy ít nhất mức này (ROUGE-1 F1) mới tính là đạt
    learns: bool = True  # có "trò" học thay thế thầy hay không
    strategy_setting: str | None = None  # tên trường Settings chọn thầy riêng cho phòng (None = theo "Thầy chính")


DEPARTMENTS: dict[str, Department] = {
    "san_tin": Department("san_tin", "Phòng Săn tin", "Đọc bài báo, tách các dữ kiện (claim) có trích dẫn nguyên văn",
                          "model_claims", 4000, 0.45),
    "nghien_cuu": Department("nghien_cuu", "Phòng Nghiên cứu", "Lập ResearchPack: góc nhìn, phản biện, rủi ro, câu hỏi cốt lõi",
                             "model_research", 6000, 0.30),
    "bien_kich": Department("bien_kich", "Phòng Biên kịch", "Viết kịch bản lời đọc khoảng 10 phút theo trụ cột P1/P2/P3",
                            "model_script", 16000, 0.28),
    "bien_tap": Department("bien_tap", "Phòng Biên tập", "Sửa từng đoạn theo yêu cầu của chủ kênh",
                           "model_editor", 2000, 0.35),
    "bai_luan": Department("bai_luan", "Phòng Bài luận",
                           "Tự research web (miễn phí) rồi viết bài luận theo chủ đề, có trích nguồn; học cách viết của cả Claude lẫn ChatGPT",
                           "model_essay", 9000, 0.30, strategy_setting="essay_teacher_strategy"),
    "dao_dien": Department("dao_dien", "Phòng Đạo diễn hình ảnh",
                           "Lên danh sách cảnh quay: mỗi cảnh 3–9 giây một từ khoá tìm video/ảnh, chữ nhấn mạnh, cảnh số liệu",
                           "model_director", 9000, 0.0, learns=False),
    "tang_truong": Department("tang_truong", "Ban Tăng trưởng",
                              "Theo dõi kênh bằng YouTube Analytics, đối chiếu KPI doanh thu tháng, tự chỉnh kế hoạch sản xuất "
                              "và viết bản ghi nhớ chiến lược hằng tuần", "model_growth", 3000, 0.0, learns=False),
    "hoc_tap": Department("hoc_tap", "Ban Đào tạo", "Định kỳ đúc kết bài học của các phòng thành sổ tay văn phong",
                          "model_editor", 3000, 0.0, learns=False),
}

FINANCE = {"code": "tai_chinh", "name": "Ban Tài chính",
           "role": "Giữ trần chi tiêu AI: tháng, ngày, phòng ban, từng video. Không phải AI, không tốn tiền."}


def teacher_model(settings, dept: Department) -> str:
    m = getattr(settings, dept.model_setting, "") or ""
    return m or settings.anthropic_model


LEVELS = ["Học việc", "Tập sự", "Thành thạo", "Chuyên gia", "Bậc thầy"]
MASTER = 4


def level_of(recent: list[int], passes: int, graduated: bool, settings=None) -> int:
    """0 Học việc → 1 Tập sự → 2 Thành thạo (tốt nghiệp, tự làm, thầy kiểm tra ngẫu nhiên)
    → 3 Chuyên gia → 4 Bậc thầy (tự làm hoàn toàn; thầy chỉ được gọi khi bài của trò không qua QA)."""
    rate = (sum(recent) / len(recent)) if recent else 0.0
    if settings is not None:
        m_pass, m_rate, full = settings.agent_master_min_passes, settings.agent_master_rate, settings.agent_graduation_window
    else:
        m_pass, m_rate, full = 40, 0.95, 20
    if graduated and passes >= m_pass and rate >= m_rate and len(recent) >= full:
        return MASTER
    if graduated and passes >= min(30, m_pass) and rate >= 0.9:
        return 3
    if graduated:
        return 2
    if len(recent) >= 5 and rate >= 0.5:
        return 1
    return 0
