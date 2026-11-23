# IMPLEMENTATION_PLAN — EV Creator Studio

Nguồn yêu cầu duy nhất: `docs/EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md` (Mục 0, I–V).

## 1. Môi trường đã khảo sát (máy build)
- Linux, Python 3.13, Node 22, FFmpeg có libass/blackdetect/freezedetect/volumedetect, font DejaVu (đủ dấu tiếng Việt).
- Không có GPU, không có API key Anthropic/Google/Telegram/Pexels → các tích hợp thật được viết đầy đủ nhưng nghiệm thu thật = `BLOCKED_BY_CREDENTIALS`.
- Mạng ra ngoài bị chặn (chỉ registry gói) → không tải được model weights VieNeu từ HuggingFace; adapter VieNeu đã viết theo API của `vieneu==3.8.5` (đã kiểm tra mã nguồn wheel: `Vieneu(mode="v3turbo")`, `.infer(text, voice=...)`, `.save()`, `.list_preset_voices()` trả `(label, voice_id)`, 48 kHz).
- Lưu ý: giọng "Hải Đăng" mà đặc tả gợi ý **không có** trong 25 preset v3 Turbo hiện hành; mặc định dùng `Quang Sơn` (có trong danh sách).

## 2. Kiến trúc
| Lớp | Lựa chọn | Ghi chú |
|---|---|---|
| API | FastAPI + Pydantic v2 | `backend/ev_studio/api` |
| DB | SQLAlchemy 2 + Alembic; SQLite (local-first) hoặc PostgreSQL (docker-compose) | `migrations/` |
| Job | Orchestrator riêng: state machine, checkpoint theo idempotency key, retry exponential backoff, lease theo story; executor `inline` (thread pool, giới hạn concurrency) hoặc `celery` (Redis) | `backend/ev_studio/workers` |
| Lịch | APScheduler (quét tin 3h/lần, chỉ chạy khi bật trong Settings) | không tự chạy khi mở dashboard |
| LLM | Anthropic SDK, model cấu hình qua env, JSON validate bằng Pydantic | prompt đọc từ `prompts/youtube_script_10min.md` |
| TTS | `TTSProvider` → `VieNeuLocalAdapter` / `VieNeuHttpAdapter` / `DemoTTSAdapter` | cache theo `(text_hash, voice, model_version)` |
| Asset | `OwnLibraryFinder`, `PexelsFinder`, `WikimediaFinder`, đồ họa nội bộ Pillow; license gate bắt buộc | không tải ảnh Google |
| Render | FFmpeg 1920×1080 30fps H.264/AAC 48 kHz, subtitle SRT/ASS (burn-in libass), preview 360p, atomic rename | |
| YouTube | google-api-python-client, OAuth web flow, resumable upload, **privacy hard-lock = private**, readback `videos.list`, đối soát chống trùng bằng tag idempotency | |
| Telegram | Bot API qua httpx; callback token 1 lần, có hạn, allowlist chat_id | "Đã kiểm duyệt" chỉ đổi DB |
| Frontend | Next.js + TypeScript (CSS thuần thay Tailwind để giảm phụ thuộc build) | `frontend/` |

## 3. State machine
`DISCOVERED → RESEARCHING → FACT_CHECK_PENDING → SCRIPT_DRAFT → SCRIPT_APPROVED_FOR_RENDER → TTS_DONE → ASSETS_READY → RENDERING → QA_PENDING → READY_TO_UPLOAD → UPLOADING → UPLOADED_PRIVATE → TELEGRAM_NOTIFIED → EDITOR_REVIEW → APPROVED_PRIVATE | REVISION_REQUESTED | REJECTED | PUBLISHED_MANUALLY_OBSERVED`, cộng `FAILED_RETRYABLE`, `FAILED_PERMANENT`, `NEEDS_REVIEW`. Không có trạng thái/đường đi `PUBLISHED` do app tạo.

## 4. Gate chặn upload (backend, không phải UI)
1. QA render PASS. 2. Mọi claim trọng yếu `verified` + có `source_url`. 3. Mọi asset dùng trong cảnh `approved` + có license. 4. Kênh OAuth khớp kênh đích. 5. MANUAL: chủ kênh bấm upload; AUTOPILOT: Autopilot bật + story được uỷ quyền. 6. Freshness check < 24h. 7. Body upload `privacyStatus == "private"`, không `publishAt`.

## 5. Thông tin chủ kênh phải tự cung cấp
`ANTHROPIC_API_KEY`; Google OAuth client (`client_secret.json`) + chọn kênh; `YOUTUBE_EXPECTED_CHANNEL_ID`; `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_CHAT_IDS`; `PEXELS_API_KEY` (tuỳ chọn); thư viện media có giấy phép; máy chạy VieNeu (CPU/GPU).

## 6. Thứ tự triển khai (đã làm)
1. Khung repo, config, prompts → 2. models/migration/state machine/bảo mật → 3. tin tức + fact-check + Claude writer → 4. TTS + asset + render + SEO + QA → 5. YouTube + Telegram + jobs + API → 6. Frontend → 7. Test, smoke E2E demo, tài liệu, bảng nghiệm thu (`ACCEPTANCE.md`).

## 7. Rủi ro
- Chất lượng phát âm VieNeu với thuật ngữ xe: có từ điển `configs/pronunciation_vi.json` + người nghe duyệt bản đầu.
- Quota YouTube thay đổi (11/2026): đọc từ Google Cloud Console, không hard-code.
- Forced alignment tiếng Việt chưa tích hợp: subtitle dùng mốc thời gian theo segment TTS thật (không ước lượng theo cả bài).
- Nhận dạng mẫu xe trong ảnh bằng vision chưa tích hợp: ảnh gắn tên mẫu xe phải khớp metadata, nếu không chỉ dùng làm "minh họa" hoặc chờ duyệt tay.
