# Bảng nghiệm thu — EV Creator Studio

Ngày chạy: 23/11/2026, máy build Linux, 2 vCPU, không GPU, không có credential thật.
`PASS` chỉ được ghi khi đã có lần chạy thật. Những tích hợp chưa có key được ghi `BLOCKED_BY_CREDENTIALS`, kèm lý do cụ thể.

**Lệnh chung:** `python run.py` khởi chạy toàn bộ ứng dụng (đã thử từ thư mục sạch trên Linux). `make test` → **167 passed** (pytest, khoảng 7 phút). `make demo` → toàn tuyến DEMO **PASS**: MP4 1080p dài 653,9 giây (10 phút 54 giây, trên sàn 10 phút), Media QA 15/15 đạt, upload DEMO `private`, đọc lại `private`, Telegram DEMO đã gửi, story ở trạng thái `EDITOR_REVIEW`. Hình ảnh: 114 cảnh 3–9 giây, 110 cảnh có nền video/ảnh (83 video, 27 ảnh), 4 cảnh đồ họa; tổng thời gian khoảng 12,4 phút.

## Mục V — Điểm kiểm soát bàn giao

| requirement | implementation_path | test_command | test_result | external_credentials_needed | notes |
|---|---|---|---|---|---|
| 1. Cài đặt & khởi chạy | `run.py`, `start.bat`, `start.sh`, `docker-compose.yml`, `/api/health` | `python run.py` (thư mục sạch) + `curl :8010/api/health` | PASS (Linux) | — | Một lệnh: tạo .venv, cài thư viện, migrate, phục vụ giao diện và API cùng một cổng. Chưa thử trên Windows thật, chưa chạy `docker compose build`. |
| 1b. Cấu hình API trong giao diện | `runtime_config.py`, `services/connection_tests.py`, `/api/config`, trang Thiết lập | `pytest tests/test_config.py` | PASS | — | Lưu mã hoá, che giá trị, kiểm tra kết nối từng dịch vụ (dùng mock), chỉ chủ kênh được sửa. |
| 2. Ingest nguồn tin | `services/ingest.py`, `services/ranker.py`, `services/sources/fetcher.py` | `pytest tests/test_news_factcheck.py` | PASS | RSS chính thức (chủ kênh chọn) | 10 URL: 2 trùng (khớp canonical_url + hash nội dung), 1 thiếu nguồn bị chặn; lưu đủ published/event/effective. |
| 3. Fact checking | `services/fact_checker.py` | `pytest tests/test_news_factcheck.py tests/test_api_duration.py::test_claim_manual_verify_requires_source_and_note` | PASS | — | Claim nhạy cảm cần nguồn cấp 1 hoặc ≥ 2 nguồn độc lập. Trích dẫn phải khớp văn bản nguồn. Xung đột trong bài và giữa các bài → `[CẦN XÁC MINH]`. Có freshness_check. |
| 4. Claude writer | `services/claude_writer.py`, `prompts/youtube_script_10min.md` (= Mục III) | `pytest tests/test_writer_and_script_qa.py` | PASS (mock) / BLOCKED_BY_CREDENTIALS (thật) | `ANTHROPIC_API_KEY` | Prompt đọc từ file, test chứng minh file trùng nguyên văn Mục III. JSON validate bằng Pydantic. Chưa gọi Anthropic thật. |
| 5. VieNeu | `services/tts/*` | `pytest tests/test_tts_media.py`; `make demo` | PASS (pipeline + adapter HTTP mock) / BLOCKED_BY_CREDENTIALS (giọng thật) | Model weights VieNeu (HuggingFace) hoặc server VieNeu | Đã đọc mã nguồn `vieneu==3.8.5` để khớp chữ ký `infer/save/list_preset_voices`. Duration đo bằng ffprobe. Ở bản demo, audio là âm tone, KHÔNG phải giọng nói. |
| 6. Asset license | `services/media/license_gate.py`, `finders.py` | `pytest tests/test_tts_media.py -k license`; E2E kiểm tra 2 asset bị loại không được tải | PASS | `PEXELS_API_KEY` (tuỳ chọn) | Loại: thiếu giấy phép, NC, ND, editorial, owner-provided thiếu bằng chứng, CC BY thiếu ghi công, URL từ web search. |
| 7. Render | `services/renderer.py`, `storyboard.py`, `subtitles.py`, `media/graphics.py` | `make demo` + `python -m ev_studio.cli verify <id>` | PASS | — | H.264 yuv420p 1920×1080 30fps, AAC 48 kHz, phụ đề burn-in và file .srt, preview 360p, cảnh dài 4–12 giây, có đồ họa dữ kiện, công thức, timeline và lợi ích/rủi ro. |
| 8. SEO/thumbnail | `services/seo.py`, `thumbnail.py` | E2E `test_autopilot_end_to_end_demo` | PASS | — | 3 tiêu đề, mô tả có nguồn kèm ngày truy cập, chương, tag, 2 thumbnail 1280×720 dưới 2 MB. |
| 9. YouTube private | `services/youtube/privacy.py`, `client.py`, `workers/jobs.py::upload_private_job` | `pytest tests/test_privacy_security.py tests/test_pipeline_e2e.py` | PASS (DEMO + mock googleapiclient) / BLOCKED_BY_CREDENTIALS (thật) | OAuth client + chủ kênh cấp quyền | Chặn ở 3 lớp (body, client, CHECK constraint trong DB). Đọc lại bằng `videos.list`, nếu lệch thì kéo về private. Sai kênh → chặn. Không có route publish. |
| 10. Telegram | `services/telegram/bot.py`, `callbacks.py` | `pytest -k telegram` | PASS (DEMO + mock HTTP) / BLOCKED_BY_CREDENTIALS (thật) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS` | Token callback dùng 1 lần, có hạn, kiểm allowlist chat. "Đã kiểm duyệt" → `APPROVED_PRIVATE`, video vẫn private. |
| 11. Review/revisions | `workers/jobs.py::apply_review/create_revision`, `PATCH /api/scripts/{id}` | `pytest tests/test_pipeline_e2e.py -k revision`, `tests/test_api_duration.py` | PASS | — | Mỗi lần sửa tạo version immutable mới. "Yêu cầu sửa" tạo story v2, bản private cũ giữ nguyên. |
| 12. E2E demo | `cli.py demo`, `scripts/smoke_demo.sh` | `make demo` | PASS | — | Mọi thứ đều có nhãn DEMO: video, thumbnail, mô tả, Telegram, UI. |
| 13. E2E thật | — | — | BLOCKED_BY_CREDENTIALS | Anthropic, Google OAuth, Telegram, VieNeu weights | Chạy lại sau khi điền `.env` (APP_MODE=production). |
| 14. Security | `security.py`, `api/deps.py`, `workers/orchestrator.py` | `pytest tests/test_privacy_security.py tests/test_api_duration.py::test_auth_roles` | PASS | — | Che secrets trong log và lỗi; token OAuth mã hoá Fernet; owner/editor RBAC; audit log; idempotency key và lease. |

## Mục O — Tiêu chí nghiệm thu cụ thể

| # | Tiêu chí | Kết quả | Bằng chứng |
|---|---|---|---|
| 1 | Nhập prompt nguồn, 3 cấu hình trụ cột, đúng 3 tiêu đề | PASS | `prompts/pillars/P1-3.md`; schema bắt buộc 3 tiêu đề; QA chặn tiêu đề khẳng định quá mức |
| 2 | Quét 10 URL (2 trùng, 1 thiếu nguồn), dedupe, chặn | PASS | `test_scan_dedupes_and_blocks_missing_source` |
| 3 | ResearchPack ≥ 2 nguồn, hiển thị mapping claim → URL, ngày | PASS (DEMO writer) | trang Biên tập; `research_pack.content.sources/claims` |
| 4 | Kịch bản + TTS đạt 600–720 giây đo thật (không bao giờ dưới 10 phút); nghe lại từng segment | PASS với TTS DEMO (653,9 giây) / chưa thử giọng VieNeu thật | `make demo`; `test_duration_loop_rewrites_using_actual_audio` (viết lại theo ACTUAL_AUDIO_SECONDS) |
| 5 | Render 1080p, mỗi cảnh có asset_id hoặc đồ họa nội bộ, không ghép sai mẫu xe | PASS | `test_autopilot_end_to_end_demo`; `test_wrong_model_becomes_illustration` |
| 6 | QA pass, SEO, thumbnail, preview | PASS | Media QA 15/15 (thêm kiểm tra cứng *Không dưới 10 phút*) |
| 7 | Upload OAuth vào đúng kênh, ép private, đọc lại, Telegram | PASS (DEMO) / BLOCKED_BY_CREDENTIALS | `test_wrong_channel_is_blocked`, `test_privacy_readback_mismatch_forces_private` |
| 8 | "Đã kiểm duyệt" → video vẫn private; công khai thủ công chỉ được quan sát | PASS | `test_telegram_callbacks_…`, `test_owner_manual_publish_is_only_observed` |
| 9 | Lỗi giả lập: mất mạng khi upload, TTS lỗi, sai key, thiếu giấy phép, sai kênh, bot offline | PASS | `test_network_drop_…`, `test_timeout_after_youtube_received_reconciles_by_tag`, `test_tts_failure_retries_then_raises`, `test_missing_key_blocks_in_production`, `test_gates_block_…`, `test_telegram_offline_keeps_private_and_warns` |
| 10 | `.env.example`, docker-compose, migration, fixture, smoke, ảnh chụp | PASS (trừ chạy docker) | `alembic upgrade head` đã chạy trên SQLite trống; ảnh chụp 7 màn hình bằng Playwright |
