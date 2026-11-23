# EV Creator Studio

Tòa soạn video AI **có người duyệt cuối** cho kênh YouTube tiếng Việt về VinFast và xe điện (ba trụ cột P1, P2, P3).
Quy trình: quét tin hợp lệ → gom nguồn, tách claim, xác minh → chấm điểm → Claude viết ResearchPack và kịch bản khoảng 10 phút → VieNeu-TTS đọc (đo thời lượng bằng audio thật) → tư liệu có giấy phép hoặc đồ họa tự dựng → FFmpeg 1080p, phụ đề, thumbnail, SEO → **tải lên YouTube ở chế độ RIÊNG TƯ** → đọc lại để xác nhận private → nhắn Telegram → **chủ kênh tự công khai trên YouTube Studio**.

> 🔒 **Các bất biến không tắt được:** mọi upload dùng `privacyStatus="private"`, bị kiểm tra ba lớp (hàm tạo body, client ngay trước khi gọi API, ràng buộc CHECK trong DB) và được đọc lại bằng `videos.list`. Ứng dụng không có endpoint, nút hay job nào để công khai video. Bấm "Đã kiểm duyệt" (trên dashboard hoặc Telegram) chỉ đổi trạng thái trong DB thành `APPROVED_PRIVATE`.

Đặc tả gốc: [`docs/EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md`](docs/EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md). Kế hoạch: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). Bảng nghiệm thu: [`ACCEPTANCE.md`](ACCEPTANCE.md).

---

## 1. Chạy thử ngay (DEMO, không cần key)

Yêu cầu: Python ≥ 3.11, FFmpeg có libass, Node 20 trở lên (để chạy dashboard).

```bash
python3 -m pip install -e "backend[dev]"     # hoặc: pip install -r <(cd backend && python -c ...) — xem pyproject.toml
export PYTHONPATH=backend
make test                                     # 76 test: unit + integration + E2E DEMO rút gọn (~2 phút)
make demo                                     # toàn tuyến DEMO 10 phút: ra MP4 1080p, upload DEMO private, Telegram DEMO
```

Mở dashboard:

```bash
make api        # http://localhost:8000  (API + /docs)
make web        # http://localhost:3000  (cd frontend && npm ci trước)
```

Ở chế độ DEMO, mọi thứ đều có nhãn **DEMO — DỮ LIỆU GIẢ LẬP**:
- Tin tức lấy từ `fixtures/news_demo.json`. Đây là 10 bài hư cấu (doanh nghiệp "DEMO-A", "DEMO-Charge"…, URL example.org), gồm 2 bài trùng, 1 bài thiếu nguồn, 1 tin đồn, 1 bài có số liệu mâu thuẫn và 1 bài chỉ là MOU.
- Bộ viết `DemoWriter` dựng kịch bản từ template, **không phải Claude**.
- `DemoTTSAdapter` **không phải giọng nói**. Nó sinh âm tone theo âm tiết để đo thời lượng và kiểm tra cả pipeline.
- `DemoYouTubeClient` không upload thật. Nó ghi vào `artifacts/demo_youtube/videos.json`, có cờ giả lập mất mạng, sai kênh, timeout sau khi YouTube đã nhận video, và privacy đọc lại sai.
- `DemoTelegramClient` ghi vào `artifacts/demo_telegram/outbox.jsonl`.

## 2. Chạy thật (production)

1. `cp .env.example .env`, đặt `APP_MODE=production`, rồi điền `SECRET_KEY` và `ADMIN_TOKEN` (bắt buộc).
2. **Anthropic:** điền `ANTHROPIC_API_KEY` và chọn `ANTHROPIC_MODEL` là model mà tài khoản API của bạn được dùng. Gói Claude trên web không bao gồm quyền API.
3. **VieNeu-TTS** (không sửa repo upstream):
   - Chạy cùng máy: `pip install "backend[tts]"` (cài `vieneu==3.8.5`, CPU/ONNX), đặt `TTS_PROVIDER=vieneu_local`. Lần đầu chạy, model weights sẽ tải từ HuggingFace (vài trăm MB).
   - Tách sang máy GPU: chạy server của repo VieNeu (`uv run python -m apps.openai_speech` hoặc `docker compose --profile api-gpu`), rồi đặt `TTS_PROVIDER=vieneu_http` và `VIENEU_HTTP_URL`.
   - Giọng mặc định là `Quang Sơn`. Giọng "Hải Đăng" mà đặc tả gợi ý **không có** trong 25 preset v3 Turbo hiện hành. Nên nghe mẫu 30 giây ở trang Biên tập trước khi chốt giọng.
4. **YouTube:**
   - Trong Google Cloud Console, bật YouTube Data API v3 và tạo OAuth client loại *Web application*.
   - Thêm redirect URI `PUBLIC_BASE_URL/api/oauth/google/callback`.
   - Lưu file client vào `secrets/client_secret.json` và đặt `YOUTUBE_EXPECTED_CHANNEL_ID=UC…`.
   - Trong dashboard, vào **Thiết lập**, bấm **Kết nối kênh YouTube** và chọn đúng kênh, rồi bấm **Chọn làm kênh đích**.
   - Scope sử dụng: `youtube.upload` và `youtube.readonly`. Token được mã hoá Fernet trước khi lưu. Ứng dụng không dùng cookie hay mật khẩu.
5. **Telegram:** tạo bot bằng @BotFather, điền `TELEGRAM_BOT_TOKEN` và `TELEGRAM_ALLOWED_CHAT_IDS`.
   - Chạy local thì để trống `TELEGRAM_WEBHOOK_SECRET`, ứng dụng sẽ tự dùng long-polling.
   - Chạy VPS có HTTPS thì đặt webhook `https://…/api/telegram/webhook` kèm `secret_token`.
6. **Media:** điền `PEXELS_API_KEY` (tuỳ chọn). Wikimedia Commons chỉ nhận các giấy phép CC0, PD, CC BY và CC BY-SA. Ảnh của bạn đặt trong `media_library/`, mỗi file kèm một `<tên>.license.json` (xem `backend/ev_studio/demo.py`).
7. **Nguồn tin:** thêm RSS chính thức trong mục **Thiết lập → Nguồn tin**. Hệ thống tôn trọng `robots.txt`, có rate limit và chỉ dùng tiêu đề cùng tóm tắt có trong feed, không vượt paywall.
8. Chạy: `docker compose up -d --build`, hoặc chạy native bằng `alembic upgrade head && make api && make web`.

### Windows 11
Nên dùng WSL2 (Ubuntu) với Docker Desktop. Cách native cũng chạy được với Python 3.12, FFmpeg (gyan.dev, có libass) và `pip install vieneu` (CPU/ONNX). GPU CUDA trên Windows cần cài `torch==2.8.0` cu128 trước, theo README của VieNeu.

## 3. Hai chế độ
- **MANUAL:** chủ kênh chọn tin ở trang Săn tin. Pipeline dừng ở *Bản nháp kịch bản* để chờ duyệt, rồi dừng ở *Sẵn sàng tải RIÊNG TƯ* để chờ chủ kênh tick xác nhận và bấm upload.
- **AUTOPILOT_PRIVATE** (bật trong Settings): tự chọn tin có điểm ≥ 80/100, mọi claim trọng yếu đã xác minh, tối đa 3 video/tuần. Pipeline chạy đến upload RIÊNG TƯ và Telegram, nhưng **dừng và chuyển sang `NEEDS_REVIEW`** nếu thiếu nguồn, claim đang tranh cãi, sai thời lượng sau 3 lần viết lại, QA không đạt, sai kênh, hoặc thiếu credential (`BLOCKED_BY_CREDENTIALS`).

## 4. Kiến trúc

```
backend/ev_studio/
  api/main.py            FastAPI — các route trong mục L (không có route publish)
  workers/jobs.py        13 job: FactCheck, Research, Script, TTS, Media, Storyboard, Render, SEO, MediaQA,
                         UploadPrivate, NotifyTelegram, RefreshStatus (+ NewsIngestion trong services/ingest.py)
  workers/orchestrator.py  idempotency key + checkpoint, retry exponential backoff, lease theo story
  workers/executor.py    thread pool (inline) hoặc Celery (JOB_EXECUTOR=celery)
  workers/schedules.py   APScheduler: quét tin mỗi 3h + Autopilot; RefreshStatus mỗi 6h (chỉ khi bật)
  state_machine.py       DISCOVERED → … → EDITOR_REVIEW → APPROVED_PRIVATE / REVISION_REQUESTED / REJECTED / PUBLISHED_MANUALLY_OBSERVED
  services/              sources, ranker, fact_checker, claude_writer, script_qa, tts/, media/, storyboard,
                         subtitles, renderer, thumbnail, seo, media_qa, youtube/, telegram/
prompts/youtube_script_10min.md   Mục III nguyên văn (Claude đọc từ file, không hard-code)
prompts/pillars/P1|P2|P3.md       hồ sơ trụ cột
prompts/reference/…               Mục IV nguyên văn (tham khảo văn phong)
frontend/                         Next.js 15 + TypeScript: 6 màn hình
migrations/                       Alembic
tests/                            76 test
```

**Ba loại duyệt được tách riêng:**
- (a) Xác minh tin và claim: chặn ngay từ khâu sản xuất.
- (b) Media QA (14 kiểm tra: codec, 1080p, 30fps, AAC 48k, thời lượng, đen hình, đứng hình, clipping, giấy phép, nguồn, thumbnail, font, SHA-256…): chặn upload.
- (c) Chủ kênh duyệt video cuối trên Telegram hoặc dashboard: **không** kích hoạt công khai.

## 5. Vận hành
- **Backup:** dump Postgres (`pg_dump`) và thư mục artifacts (volume `artifacts`). Artifact là immutable, có checksum, và được ghi qua file tạm rồi đổi tên nguyên tử.
- **Gia hạn token:** client Google tự refresh rồi ghi đè token đã mã hoá. Nếu refresh thất bại, story chuyển `NEEDS_REVIEW` với lý do `BLOCKED_BY_CREDENTIALS`, và chủ kênh cần kết nối lại kênh.
- **Mất mạng:** retry theo backoff. Upload dùng resumable. Sau mỗi lần insert, video id được lưu ngay. Trước khi upload lại, hệ thống tìm video theo tag `evcs-<hash>` để không đăng trùng. Bấm **Chạy tiếp / thử lại** sẽ resume từ checkpoint.
- **Kiểm tra định kỳ:** `PYTHONPATH=backend python3 scripts/check_privacy.py` (sẽ fail nếu có video nào không private mà chưa được ghi nhận là chủ kênh tự công khai).
- **Chi phí** (ước tính, tham khảo):
  - Claude: mỗi video khoảng 15–30 nghìn token vào và 6–10 nghìn token ra (ResearchPack, kịch bản, tối đa 3 lần viết lại thời lượng). Xem `/api/costs`; đơn giá chỉnh trong `services/llm.py`.
  - VieNeu: chạy CPU, khoảng 1–3 lần thời gian thực cho 10 phút audio (tuỳ CPU). Có GPU thì nhanh hơn nhiều.
  - Render: khoảng 3–6 phút CPU cho mỗi video 10 phút 1080p (preset veryfast).
  - YouTube: quota `videos.insert` dùng bucket riêng từ 11/2026, xem số hiện hành trong Google Cloud Console.

## 6. Giới hạn đã biết (trung thực)
- Chưa chạy với credential thật (Anthropic, Google OAuth, Telegram, Pexels) và chưa có model weights VieNeu. Các adapter thật đã được viết đúng API và test bằng mock HTTP, nhưng nghiệm thu thật vẫn là **BLOCKED_BY_CREDENTIALS**.
- Chưa tích hợp forced alignment tiếng Việt. Phụ đề căn theo mốc thời gian thật của từng segment TTS.
- Chưa có vision tự kiểm tra mẫu xe trong ảnh. Ảnh gắn mẫu xe khác chủ đề tự động bị dán nhãn "minh họa"; ảnh không ghi mẫu xe cũng bị coi là minh họa.
- Ken Burns chỉ áp dụng cho cảnh ảnh thật. Chưa trộn nhạc nền.
- Dashboard dùng CSS thuần thay cho Tailwind, để giảm phụ thuộc build.
