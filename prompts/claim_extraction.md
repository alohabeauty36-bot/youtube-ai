# PROMPT TRÍCH XUẤT CLAIM

Từ văn bản bài viết dưới đây, trích xuất các khẳng định kiểm chứng được. Mỗi claim chỉ có MỘT ý.
`evidence_excerpt` PHẢI là chuỗi con NGUYÊN VĂN trong văn bản (hệ thống sẽ kiểm tra; không khớp sẽ bị loại).
claim_type ∈ price, charging_offer, warranty, spec, safety, sales_volume, station_count, law, deadline, general.
Nhận định/dự báo của tác giả → status "inference". Không tự thêm thông tin ngoài văn bản.

Trả về DUY NHẤT JSON: {"claims": [{"claim_key": "c1", "text": "...", "claim_type": "...", "subject": "...", "value": "...", "unit": "...", "date_scope": "...", "evidence_excerpt": "...", "status": "unverified|inference", "is_key": true}]}
