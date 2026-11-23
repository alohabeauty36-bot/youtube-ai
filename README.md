# EV Creator Studio

Tòa soạn video AI **có người duyệt cuối** cho kênh YouTube tiếng Việt về VinFast và xe điện (ba trụ cột P1, P2, P3).
Quy trình: quét tin hợp lệ → gom nguồn, tách claim, xác minh → chấm điểm → Claude viết ResearchPack và kịch bản khoảng 10 phút → VieNeu-TTS đọc (đo thời lượng bằng audio thật) → hình cho **từng đoạn**: ảnh có giấy phép tìm trên mạng trước, không có mới nhờ Gemini tạo ảnh minh họa, cuối cùng là đồ họa tự dựng → FFmpeg 1080p, phụ đề, thumbnail, SEO → **tải lên YouTube ở chế độ RIÊNG TƯ** → đọc lại để xác nhận private → nhắn Telegram → **chủ kênh tự công khai trên YouTube Studio**.

> 🔒 **Các bất biến không tắt được:** mọi upload dùng `privacyStatus="private"`, bị kiểm tra ba lớp (hàm tạo body, client ngay trước khi gọi API, ràng buộc CHECK trong DB) và được đọc lại bằng `videos.list`. Ứng dụng không có endpoint, nút hay job nào để công khai video. Bấm "Đã kiểm duyệt" (trên dashboard hoặc Telegram) chỉ đổi trạng thái trong DB thành `APPROVED_PRIVATE`.

Đặc tả gốc: [`docs/EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md`](docs/EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md). Kế hoạch: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). Bảng nghiệm thu: [`ACCEPTANCE.md`](ACCEPTANCE.md).

---

## 1. Chạy bằng một lệnh

Cần có: **Python 3.11 trở lên** và **FFmpeg**. Trên Windows, nếu chưa có FFmpeg, lệnh chạy sẽ hỏi để tự cài bằng winget.

```bash
python run.py          # Windows: bấm đúp start.bat (hoặc: py run.py) · macOS/Linux: ./start.sh
```

**Lần chạy đầu**, lệnh này sẽ: tạo môi trường `.venv` riêng → cài thư viện → tạo database và khoá mã hoá (`data/.secret_key`) → chạy server → mở **http://localhost:8000**.
**Các lần sau**, ứng dụng khởi động ngay. Giao diện web và API chạy chung một cổng, không cần Node.js.

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--with-tts` | Cài thêm VieNeu-TTS để dùng giọng đọc thật (CPU) |
| `--port 8010` | Đổi cổng |
| `--host 0.0.0.0` | Cho máy khác trong mạng truy cập. Khi đó **bắt buộc** đặt mật khẩu chủ kênh |
| `--no-browser` | Không tự mở trình duyệt |
| `--build-web` | Build lại giao diện từ `frontend/` (cần Node.js 20+, chỉ dành cho lập trình viên) |

Lần đầu, ứng dụng ở chế độ **DEMO**: mọi dữ liệu đều giả lập và gắn nhãn, không upload thật. Bạn bấm **Săn tin → Quét tin ngay** để xem cả quy trình hoạt động.

## 2. Cấu hình API ngay trong ứng dụng

Mở **Thiết lập → Cấu hình API & dịch vụ**. Không cần sửa file nào. Key và token được mã hoá trước khi lưu vào database. Sau khi lưu, ứng dụng chỉ hiện lại 4 ký tự cuối. Giá trị nhập ở đây ghi đè file `.env`. Mỗi nhóm đều có nút **Kiểm tra** để thử kết nối.

| Nhóm | Bạn nhập | Nút "Kiểm tra" làm gì |
|---|---|---|
| Claude (Anthropic) | API key (tạo ở console.anthropic.com), chọn model | Liệt kê model mà tài khoản được dùng; bấm vào tên model để chọn |
| Giọng đọc VieNeu | `vieneu_local` (sau khi chạy `python run.py --with-tts`) hoặc `vieneu_http` + địa chỉ server GPU; chọn giọng | Kiểm tra thư viện đã cài hoặc server có phản hồi |
| YouTube | Dán hoặc tải lên `client_secret.json` (OAuth client loại *Web application*), ID kênh đích | Kiểm tra JSON và redirect URI. Trang hiển thị sẵn URI để bạn sao chép vào Google Cloud Console |
| Telegram | Bot token (lấy từ @BotFather) | Xác nhận bot hoạt động, liệt kê các chat vừa nhắn `/start` để bấm **Cho phép**, rồi gửi tin nhắn thử |
| Kho tư liệu (tìm ảnh trên mạng) | Bật/tắt Openverse và Wikimedia (không cần key), Pexels key, Pixabay key (tuỳ chọn), số ảnh mỗi đoạn | Tìm thử trên từng nguồn, báo nguồn nào trả ảnh hợp quyền |
| Ảnh AI dự phòng (Gemini) | Gemini API key (aistudio.google.com), model ảnh, trần ảnh AI mỗi video | Liệt kê model tạo ảnh tài khoản được dùng; bấm để chọn |
| Biên tập, Dựng video, Bảo mật | Thời lượng, ngưỡng Autopilot, preset FFmpeg, mật khẩu chủ kênh/biên tập viên | — |

Sau khi nhập key, đổi **Chế độ** sang `production`, rồi bấm **Kết nối kênh YouTube** để cấp quyền và chọn đúng kênh. Nếu thiếu key, video sẽ dừng ở trạng thái `BLOCKED_BY_CREDENTIALS` kèm đường dẫn mở trang Thiết lập, chứ không lặng lẽ chuyển sang dữ liệu giả lập.

**Truy cập:** khi mở trên chính máy chạy ứng dụng (127.0.0.1), bạn không cần mật khẩu. Khi truy cập từ máy khác, ứng dụng yêu cầu mật khẩu chủ kênh hoặc biên tập viên. Nếu ứng dụng nằm sau reverse proxy, các request có header proxy **không** được tin là truy cập cục bộ.

**Dành cho lập trình viên:** `make test` chạy 109 test. `make demo` chạy toàn tuyến DEMO 10 phút. Docker: `docker compose up -d --build` (Postgres + Redis + worker VieNeu), rồi mở http://localhost:8000.

### Windows 11
Nên dùng WSL2 (Ubuntu) với Docker Desktop. Cách native cũng chạy được với Python 3.12, FFmpeg (gyan.dev, có libass) và `pip install vieneu` (CPU/ONNX). GPU CUDA trên Windows cần cài `torch==2.8.0` cu128 trước, theo README của VieNeu.

## 3. Hình ảnh cho từng đoạn

Sau khi có lời đọc, `MediaJob` chia kịch bản theo **đoạn** và tìm hình cho từng đoạn:

1. **Lập kế hoạch:** Claude đọc các đoạn và đề xuất 1–3 cụm từ khoá tiếng Anh để tìm ảnh, một mô tả cảnh cho ảnh AI, mẫu xe được nhắc tới (nếu có), và đánh dấu đoạn nên dùng đồ họa (nhiều số liệu, hoặc nói về sự cố/sự kiện có thật). Prompt nằm ở `prompts/visual_plan.md`. Khi chưa có key Claude, ứng dụng dùng bộ từ khoá dựng sẵn.
2. **Tìm trên mạng trước:** lần lượt thư viện của bạn (`media_library/`), **Openverse** (công cụ tìm ảnh giấy phép mở trên toàn web: Flickr, Wikimedia, bảo tàng…), Wikimedia Commons, Pexels, Pixabay. Chỉ nhận ảnh cho phép **dùng thương mại và chỉnh sửa**. Mỗi ảnh qua license gate rồi mới tải. Ảnh ghi tên mẫu xe khác chủ đề bị hạ xuống "minh họa". Một ảnh không dùng lại cho hai đoạn.
3. **Không có ảnh hợp quyền mới gọi Gemini:** chỉ đoạn **không tìm được ảnh nào** mới được tạo ảnh AI (khung 16:9). Prompt luôn kèm ràng buộc: không logo, không biển số, không người thật/người nổi tiếng, không chữ, không tai nạn. Đoạn nói về sự cố hoặc sự kiện có thật **không** được dựng bằng ảnh AI. Mỗi video có trần ảnh AI (mặc định 12), chia đều trên toàn video. Ảnh AI luôn có nhãn **"MINH HỌA AI"** trên khung hình, không dùng làm thumbnail, và video có ảnh AI luôn được khai báo *nội dung tổng hợp* khi upload.
4. **Không có ảnh:** cảnh của đoạn dùng đồ họa tự dựng như trước.

Trang **Dựng video** hiển thị ảnh theo từng đoạn (đoạn số mấy, từ khoá đã dùng, nguồn, giấy phép, nhãn AI). Nút **Tìm ảnh cho từng đoạn** chạy lại bước này: đoạn đã có ảnh được giữ nguyên, ảnh bạn đã **Loại** sẽ không bị chọn lại. Ở chế độ DEMO, ứng dụng không gọi mạng: ảnh AI là ảnh giả lập có nhãn DEMO.

## 4. Hai chế độ
- **MANUAL:** chủ kênh chọn tin ở trang Săn tin. Pipeline dừng ở *Bản nháp kịch bản* để chờ duyệt, rồi dừng ở *Sẵn sàng tải RIÊNG TƯ* để chờ chủ kênh tick xác nhận và bấm upload.
- **AUTOPILOT_PRIVATE** (bật trong Settings): tự chọn tin có điểm ≥ 80/100, mọi claim trọng yếu đã xác minh, tối đa 3 video/tuần. Pipeline chạy đến upload RIÊNG TƯ và Telegram, nhưng **dừng và chuyển sang `NEEDS_REVIEW`** nếu thiếu nguồn, claim đang tranh cãi, sai thời lượng sau 3 lần viết lại, QA không đạt, sai kênh, hoặc thiếu credential (`BLOCKED_BY_CREDENTIALS`).

## 5. Kiến trúc

```
backend/ev_studio/
  api/main.py            FastAPI — các route trong mục L (không có route publish)
  workers/jobs.py        13 job: FactCheck, Research, Script, TTS, Media, Storyboard, Render, SEO, MediaQA,
                         UploadPrivate, NotifyTelegram, RefreshStatus (+ NewsIngestion trong services/ingest.py)
  workers/orchestrator.py  idempotency key + checkpoint, retry exponential backoff, lease theo story
  workers/executor.py    thread pool (inline) hoặc Celery (JOB_EXECUTOR=celery)
  workers/schedules.py   APScheduler: quét tin mỗi 3h + Autopilot; RefreshStatus mỗi 6h (chỉ khi bật)
  state_machine.py       DISCOVERED → … → EDITOR_REVIEW → APPROVED_PRIVATE / REVISION_REQUESTED / REJECTED / PUBLISHED_MANUALLY_OBSERVED
  services/media/        finders (thư viện, Openverse, Wikimedia, Pexels, Pixabay), license_gate, visual_plan (kế hoạch ảnh theo đoạn),
                         sourcing (web trước → Gemini → đồ họa), generators (Gemini/DEMO), graphics
  services/              sources, ranker, fact_checker, claude_writer, script_qa, tts/, media/, storyboard,
                         subtitles, renderer, thumbnail, seo, media_qa, youtube/, telegram/
prompts/youtube_script_10min.md   Mục III nguyên văn (Claude đọc từ file, không hard-code)
prompts/visual_plan.md            kế hoạch hình ảnh cho từng đoạn (từ khoá tìm ảnh + mô tả cảnh cho ảnh AI)
prompts/pillars/P1|P2|P3.md       hồ sơ trụ cột
prompts/reference/…               Mục IV nguyên văn (tham khảo văn phong)
frontend/                         Next.js 15 + TypeScript (xuất tĩnh → backend/ev_studio/web_dist, backend phục vụ)
migrations/                       Alembic
tests/                            109 test
run.py, start.bat, start.sh       chạy bằng một lệnh
backend/ev_studio/runtime_config.py  cấu hình API lưu mã hoá trong DB
```

**Ba loại duyệt được tách riêng:**
- (a) Xác minh tin và claim: chặn ngay từ khâu sản xuất.
- (b) Media QA (14 kiểm tra: codec, 1080p, 30fps, AAC 48k, thời lượng, đen hình, đứng hình, clipping, giấy phép, nguồn, thumbnail, font, SHA-256…): chặn upload.
- (c) Chủ kênh duyệt video cuối trên Telegram hoặc dashboard: **không** kích hoạt công khai.

## 6. Vận hành
- **Backup:** dump Postgres (`pg_dump`) và thư mục artifacts (volume `artifacts`). Artifact là immutable, có checksum, và được ghi qua file tạm rồi đổi tên nguyên tử.
- **Gia hạn token:** client Google tự refresh rồi ghi đè token đã mã hoá. Nếu refresh thất bại, story chuyển `NEEDS_REVIEW` với lý do `BLOCKED_BY_CREDENTIALS`, và chủ kênh cần kết nối lại kênh.
- **Mất mạng:** retry theo backoff. Upload dùng resumable. Sau mỗi lần insert, video id được lưu ngay. Trước khi upload lại, hệ thống tìm video theo tag `evcs-<hash>` để không đăng trùng. Bấm **Chạy tiếp / thử lại** sẽ resume từ checkpoint.
- **Kiểm tra định kỳ:** `PYTHONPATH=backend python3 scripts/check_privacy.py` (sẽ fail nếu có video nào không private mà chưa được ghi nhận là chủ kênh tự công khai).
- **Chi phí** (ước tính, tham khảo):
  - Claude: mỗi video khoảng 15–30 nghìn token vào và 6–10 nghìn token ra (ResearchPack, kịch bản, tối đa 3 lần viết lại thời lượng), cộng khoảng 3–5 nghìn token cho kế hoạch hình ảnh. Xem `/api/costs`; đơn giá chỉnh trong `services/llm.py`.
  - Gemini: chỉ tính cho đoạn không tìm được ảnh, tối đa `GEMINI_MAX_IMAGES_PER_VIDEO` ảnh mỗi video; đơn giá ước tính chỉnh ở Thiết lập. Openverse, Wikimedia, Pexels, Pixabay miễn phí.
  - VieNeu: chạy CPU, khoảng 1–3 lần thời gian thực cho 10 phút audio (tuỳ CPU). Có GPU thì nhanh hơn nhiều.
  - Render: khoảng 3–6 phút CPU cho mỗi video 10 phút 1080p (preset veryfast).
  - YouTube: quota `videos.insert` dùng bucket riêng từ 11/2026, xem số hiện hành trong Google Cloud Console.

## 7. Giới hạn đã biết (trung thực)
- Chưa chạy với credential thật (Anthropic, Google OAuth, Telegram, Pexels) và chưa có model weights VieNeu. Các adapter thật đã được viết đúng API và test bằng mock HTTP, nhưng nghiệm thu thật vẫn là **BLOCKED_BY_CREDENTIALS**.
- Chưa tích hợp forced alignment tiếng Việt. Phụ đề căn theo mốc thời gian thật của từng segment TTS.
- Chưa có vision tự kiểm tra mẫu xe trong ảnh. Mẫu xe được đọc từ tiêu đề/tag của ảnh; ảnh gắn mẫu xe khác chủ đề tự động bị dán nhãn "minh họa"; ảnh không ghi mẫu xe cũng bị coi là minh họa.
- Openverse, Pixabay và Gemini mới được kiểm thử bằng mock HTTP (định dạng request/response theo tài liệu hiện hành), chưa gọi bằng key thật. Giấy phép do Openverse cung cấp lấy từ nguồn gốc; ứng dụng lưu bản ghi API làm bằng chứng, nhưng chủ kênh vẫn nên xem trang gốc khi duyệt.
- Ken Burns chỉ áp dụng cho cảnh ảnh thật. Chưa trộn nhạc nền.
- Dashboard dùng CSS thuần thay cho Tailwind, để giảm phụ thuộc build.
- `run.py` đã được chạy thử trên Linux từ thư mục sạch (tạo `.venv`, cài thư viện, migrate, phục vụ giao diện). Chưa thử trên máy Windows thật; đường dẫn FFmpeg, font và venv đã được viết để chạy được trên Windows.
