# EV Creator Studio

Tòa soạn video AI **có người duyệt cuối** cho kênh YouTube tiếng Việt về VinFast và xe điện (ba trụ cột P1, P2, P3).
Quy trình: quét tin hợp lệ → gom nguồn, tách claim, xác minh → chấm điểm → Claude viết ResearchPack và kịch bản khoảng 10 phút → VieNeu-TTS đọc (đo thời lượng bằng audio thật) → tư liệu có giấy phép hoặc đồ họa tự dựng → FFmpeg 1080p, phụ đề, thumbnail, SEO → **tải lên YouTube ở chế độ RIÊNG TƯ** → đọc lại để xác nhận private → nhắn Telegram → **chủ kênh tự công khai trên YouTube Studio**.

> 🔒 **Các bất biến không tắt được:** mọi upload dùng `privacyStatus="private"`, bị kiểm tra ba lớp (hàm tạo body, client ngay trước khi gọi API, ràng buộc CHECK trong DB) và được đọc lại bằng `videos.list`. Ứng dụng không có endpoint, nút hay job nào để công khai video. Bấm "Đã kiểm duyệt" (trên dashboard hoặc Telegram) chỉ đổi trạng thái trong DB thành `APPROVED_PRIVATE`.

Đặc tả gốc: [`docs/EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md`](docs/EV_CREATOR_STUDIO_MASTER_ALL_IN_ONE.md). Kế hoạch: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md). Bảng nghiệm thu: [`ACCEPTANCE.md`](ACCEPTANCE.md).

---

## 1. Chạy bằng một lệnh

Cần có: **Python 3.11 trở lên** và **FFmpeg**. Trên Windows, nếu chưa có FFmpeg, lệnh chạy sẽ hỏi để tự cài bằng winget.

```bash
python run.py          # Windows: bấm đúp start.bat (hoặc: py run.py) · macOS/Linux: ./start.sh
```

**Lần chạy đầu**, lệnh này sẽ: tạo môi trường `.venv` riêng → cài thư viện → tạo database và khoá mã hoá (`data/.secret_key`) → chạy server → mở **http://localhost:8010**.
**Các lần sau**, ứng dụng khởi động ngay. Giao diện web và API chạy chung một cổng, không cần Node.js.

| Tuỳ chọn | Ý nghĩa |
|---|---|
| `--with-tts` | Cài thêm VieNeu-TTS để dùng giọng đọc thật (CPU) |
| `--port 8020` | Đổi cổng (mặc định 8010) |
| `--host 0.0.0.0` | Cho máy khác trong mạng truy cập. Khi đó **bắt buộc** đặt mật khẩu chủ kênh |
| `--no-browser` | Không tự mở trình duyệt |
| `--build-web` | Build lại giao diện từ `frontend/` (cần Node.js 20+, chỉ dành cho lập trình viên) |

**Giọng đọc thật:** nếu chỉ chạy `start.bat`/`run.py` thường, máy chưa có VieNeu-TTS và audio là **âm giả lập (tiếng ù/bíp), không phải giọng nói**. Để có giọng thật: Windows bấm đúp **`start-giong-that.bat`**; macOS/Linux chạy `python run.py --with-tts`. Lần đọc đầu tiên cần Internet để tải model (vài trăm MB). Kiểm tra ở **Thiết lập → Giọng đọc VieNeu-TTS → Kiểm tra**: ứng dụng đọc thử một câu và phát cho bạn nghe, hoặc báo lỗi thật (chưa cài, không tải được model, sai tên giọng). Khi đã cài VieNeu, ứng dụng dùng giọng thật cả ở chế độ DEMO.

Lần đầu, ứng dụng ở chế độ **DEMO**: mọi dữ liệu đều giả lập và gắn nhãn, không upload thật. Bạn bấm **Săn tin → Quét tin ngay** để xem cả quy trình hoạt động.

## 2. Cấu hình API ngay trong ứng dụng

Mở **Thiết lập → Cấu hình API & dịch vụ**. Không cần sửa file nào. Key và token được mã hoá trước khi lưu vào database. Sau khi lưu, ứng dụng chỉ hiện lại 4 ký tự cuối. Giá trị nhập ở đây ghi đè file `.env`. Mỗi nhóm đều có nút **Kiểm tra** để thử kết nối.

| Nhóm | Bạn nhập | Nút "Kiểm tra" làm gì |
|---|---|---|
| Claude (Anthropic) | API key (tạo ở console.anthropic.com), chọn model | Liệt kê model mà tài khoản được dùng; bấm vào tên model để chọn |
| Giọng đọc VieNeu | `vieneu_local` (sau khi chạy `python run.py --with-tts`) hoặc `vieneu_http` + địa chỉ server GPU; chọn giọng | Kiểm tra thư viện đã cài hoặc server có phản hồi |
| YouTube | Dán hoặc tải lên `client_secret.json` (OAuth client loại *Web application*), ID kênh đích | Kiểm tra JSON và redirect URI. Trang hiển thị sẵn URI để bạn sao chép vào Google Cloud Console |
| Telegram | Bot token (lấy từ @BotFather) | Xác nhận bot hoạt động, liệt kê các chat vừa nhắn `/start` để bấm **Cho phép**, rồi gửi tin nhắn thử |
| Kho tư liệu | Pexels key (tuỳ chọn), bật/tắt Wikimedia | Tìm thử một ảnh |
| Biên tập, Dựng video, Bảo mật | Thời lượng, ngưỡng Autopilot, preset FFmpeg, mật khẩu chủ kênh/biên tập viên | — |

Sau khi nhập key, đổi **Chế độ** sang `production`, rồi bấm **Kết nối kênh YouTube** để cấp quyền và chọn đúng kênh. Nếu thiếu key, video sẽ dừng ở trạng thái `BLOCKED_BY_CREDENTIALS` kèm đường dẫn mở trang Thiết lập, chứ không lặng lẽ chuyển sang dữ liệu giả lập.

**Chuyển sang chạy thật sẽ xoá hết dữ liệu DEMO.** Khi lưu **Chế độ** = `production`, ứng dụng hiện hộp xác nhận kèm số lượng sẽ xoá, rồi xoá trong cùng một lần lưu:
- Kênh, nguồn tin, tin fixture (kèm claim), mọi video DEMO và các bản viết lại, cùng ResearchPack, kịch bản, giọng đọc, cảnh, tư liệu, render, SEO, upload và tin Telegram giả lập, job, lượt duyệt và lỗi gắn với các video đó.
- Bài luận DEMO, chi phí ước tính DEMO, bộ nhớ AI học từ dữ liệu DEMO.
- File: `artifacts/<video>`, YouTube/Telegram giả lập, `media_library/demo`, cache giọng giả lập.
- **Autopilot bị tắt** nếu đang bật (thường do chạy thử DEMO). Bật lại ở **Thiết lập → Tự động hoá** khi đã sẵn sàng.

Giữ nguyên: cấu hình và key đã nhập, **sổ cái ngân sách** (tiền thật đã tiêu, kể cả khi thử Claude lúc DEMO), nhật ký audit, hồ sơ năng lực phòng ban và mọi dữ liệu thật. Không hoàn tác được. Nếu còn video DEMO đang chạy dở, ứng dụng từ chối đổi chế độ và báo đợi chạy xong. Nếu đặt `APP_MODE=production` trong `.env`, dữ liệu DEMO còn sót sẽ được xoá ngay khi khởi động.

**Truy cập:** khi mở trên chính máy chạy ứng dụng (127.0.0.1), bạn không cần mật khẩu. Khi truy cập từ máy khác, ứng dụng yêu cầu mật khẩu chủ kênh hoặc biên tập viên. Nếu ứng dụng nằm sau reverse proxy, các request có header proxy **không** được tin là truy cập cục bộ.

**Dành cho lập trình viên:** `make test` chạy 133 test. `make demo` chạy toàn tuyến DEMO 10 phút. Docker: `docker compose up -d --build` (Postgres + Redis + worker VieNeu), rồi mở http://localhost:8010.

### Windows 11
Nên dùng WSL2 (Ubuntu) với Docker Desktop. Cách native cũng chạy được với Python 3.12, FFmpeg (gyan.dev, có libass) và `pip install vieneu` (CPU/ONNX). GPU CUDA trên Windows cần cài `torch==2.8.0` cu128 trước, theo README của VieNeu.

## 3. Hai chế độ
- **MANUAL:** chủ kênh chọn tin ở trang Săn tin. Pipeline dừng ở *Bản nháp kịch bản* để chờ duyệt, rồi dừng ở *Sẵn sàng tải RIÊNG TƯ* để chờ chủ kênh tick xác nhận và bấm upload.
- **AUTOPILOT_PRIVATE** (bật trong Settings): tự chọn tin có điểm ≥ 80/100, mọi claim trọng yếu đã xác minh, tối đa 3 tin/ngày và chỉ khi Ban Tài chính còn đủ ngân sách. Pipeline chạy đến upload RIÊNG TƯ và Telegram, nhưng **dừng và chuyển sang `NEEDS_REVIEW`** nếu thiếu nguồn, claim đang tranh cãi, sai thời lượng sau 3 lần viết lại, QA không đạt, sai kênh, hoặc thiếu credential (`BLOCKED_BY_CREDENTIALS`).

## 3b. Ngân sách AI và bộ não phòng ban

### Ban Tài chính — trần chi tiêu cứng
Mặc định **50 USD/tháng** cho Claude + ChatGPT (chỉnh ở **Thiết lập → Ngân sách AI**). Trước *mọi* lần gọi AI, ứng dụng giữ chỗ chi phí xấu nhất (token vào ước tính dư + toàn bộ `max_tokens` ra). Chạm bất kỳ trần nào thì **không gọi**, video dừng ở `NEEDS_REVIEW` với lý do `BLOCKED_BY_BUDGET`, bấm **Chạy tiếp** khi có ngân sách.

| Trần | Mặc định | Ghi chú |
|---|---|---|
| Tháng | 50 USD, giữ lại 5% phòng sai số → chi tối đa 47,5 USD | cứng |
| Ngày | phần còn lại của tháng ÷ số ngày còn lại, **không quá 2× bình quân** (~3,2 USD) | chống một ngày tiêu quá tay |
| Phòng ban | Săn tin 14%, Nghiên cứu 22%, Biên kịch 45%, Biên tập 6%, Bài luận 10%, Đào tạo 3% | một phòng không ăn hết phần của phòng khác |
| Mỗi video | 0,75 USD (đủ cho ResearchPack + kịch bản + 3 lần viết lại) | |
| Số tin | Autopilot tối đa **3 tin/ngày**, chỉ mở tin mới khi còn đủ tiền cho một video | |

Sổ cái (`budget_ledger`) được ghi bằng session riêng và commit ngay, nên job lỗi hay rollback cũng không làm mất dấu tiền đã tiêu. Xem ở trang **Bộ não AI & Ngân sách**.

Cách tiết kiệm đã bật sẵn: Phòng Săn tin dùng **Claude Haiku 5.5** (0,10/0,50 USD mỗi 1 triệu token), Nghiên cứu và Biên kịch dùng **Sonnet 5.5** (2/10 USD); prompt caching cho system prompt dài (đọc cache chỉ 5% giá); bỏ qua tin không thuộc chủ đề xe điện trước khi gọi AI; câu hỏi lặp lại lấy từ bộ nhớ ($0).

**Ước tính** (bảng giá 11/2026, chưa đo với key thật): một video khoảng 0,25–0,35 USD (ResearchPack ~0,04 + kịch bản ~0,12 + trung bình một lần viết lại), tách claim ~0,001 USD/bài. 3 video/ngày × 30 ngày ≈ **25–32 USD/tháng**, dưới trần 50 USD.

### Bộ não phòng ban — học từ Claude/ChatGPT rồi tự viết
Mỗi phòng (Săn tin, Nghiên cứu, Biên kịch, Biên tập, Bài luận, Ban Đào tạo) là một bộ não độc lập: bộ nhớ riêng, bài học riêng, hồ sơ năng lực riêng, trần ngân sách riêng. Mỗi đề bài đi theo thứ tự:

1. **Bộ nhớ** — đề y hệt đã hỏi thầy → dùng lại, $0.
2. **Trò tự làm** — model mã nguồn mở chạy trên máy bạn qua [Ollama](https://ollama.com) (mặc định `qwen2.5:7b-instruct`), $0. Chỉ khi phòng đã **tốt nghiệp**; vẫn gửi thầy kiểm tra ngẫu nhiên 15% số việc.
3. **Thầy** — Claude (hoặc ChatGPT nếu bật, có thể luân phiên cả hai), tốn tiền, luôn qua Ban Tài chính. Hết ngân sách → nhờ trò; trò không làm được → dừng.
4. **Học** — câu trả lời của thầy được lưu. Trò làm thử cùng đề (miễn phí, chạy nền) và được chấm: đúng schema, qua QA nghiệp vụ, nội dung đủ giống thầy. Đạt ≥ 80% trong 20 lần gần nhất (tối thiểu 10) → tốt nghiệp. Trượt nhiều → tự hạ cấp.

Cấp bậc: Học việc → Tập sự → Thành thạo (tốt nghiệp) → Chuyên gia → **Bậc thầy** (≥ 40 lần đạt, tỷ lệ đạt ≥ 95% trên đủ 20 lần gần nhất). Ở cấp Bậc thầy phòng tự làm hoàn toàn: không gửi thầy kiểm tra ngẫu nhiên nữa (`agent_master_audit_rate`, mặc định 0); thầy chỉ được gọi khi bài của trò không qua QA. Đặt phòng ở *Chỉ trò tự làm* nếu muốn không bao giờ gọi thầy.

Nguồn học khác, không tốn tiền: kịch bản trượt Script QA (thành bài học, bản lỗi không được dùng lại), chủ kênh duyệt (bản duyệt thành ví dụ mẫu), chủ kênh yêu cầu sửa/loại (thành bài học), tốc độ đọc đo từ audio thật, claim đã xác minh (thành kho kiến thức). Mỗi Chủ nhật, Ban Đào tạo dùng Haiku đúc kết bài học thành **sổ tay văn phong** cho từng phòng.

**Kỳ vọng thực tế:** model 7B chạy trên máy viết kém Claude một bậc rõ rệt, nhất là kịch bản 10 phút. Hệ thống không "biến thành Claude"; nó giảm số lần phải gọi Claude ở những việc trò đã làm đạt chuẩn, và mọi kịch bản do trò viết vẫn phải qua Script QA và chủ kênh duyệt. Thường Phòng Săn tin tốt nghiệp trước, Biên kịch sau cùng (hoặc không). Máy cần khoảng 8 GB RAM trống cho model 7B; chạy CPU mất vài phút cho một kịch bản. Ứng dụng **không fine-tune** model từ câu trả lời của Claude/ChatGPT (điều khoản của cả hai hạn chế việc này); "học" là bộ nhớ + truy xuất + bài học + ví dụ mẫu đưa vào prompt.

Cài trò: cài Ollama → `ollama pull qwen2.5:7b-instruct` → **Thiết lập → Bộ não phòng ban → Địa chỉ Ollama** = `http://localhost:11434`. Mỗi phòng có thể đặt *Tự động*, *Chỉ nhờ thầy* hoặc *Chỉ trò tự làm* ở trang **Bộ não AI & Ngân sách**.

Dùng thêm ChatGPT: nhập OpenAI API key, tên model, và **bắt buộc** khai giá trong *Bảng giá bổ sung*, ví dụ `{"ten-model": [gia_vao, gia_ra]}` (USD/1 triệu token). Không biết giá thì Ban Tài chính từ chối gọi.

### Phòng Bài luận — tự research web và viết bài luận theo chủ đề
Trang **Bài luận**: nhập chủ đề, yêu cầu, kiểu bài (nghị luận, phân tích, thuyết minh, so sánh), số từ.

1. **Lập kế hoạch tìm kiếm** — tách chủ đề thành 4–6 truy vấn (trò gợi ý nếu có Ollama, không thì dùng khuôn), $0.
2. **Research** — Wikipedia (API chính thức, không cần key), thêm SearXNG tự cài hoặc Brave Search API (gói miễn phí) nếu cấu hình ở **Thiết lập → Phòng Bài luận**. Trang web được tải với robots.txt, giãn cách, giới hạn dung lượng, không vượt paywall, không gọi địa chỉ nội bộ. Các đoạn liên quan nhất (BM25) được gom thành **hồ sơ tư liệu** đánh số `S1, S2…`. Không gọi AI ở bước này, $0.
3. **Viết** — qua bộ não phòng ban. Thầy mặc định **luân phiên Claude và ChatGPT** để trò học cách viết của cả hai; thầy và trò nhận cùng một hồ sơ tư liệu. Trò được đưa một bài mẫu của mỗi thầy cho chủ đề gần nhất và “mục tiêu cấu trúc” là trung bình các bài của thầy (số phần, độ dài đoạn, mật độ trích dẫn, có phản biện). Trang Bộ não hiển thị bảng so sánh cách viết Claude / ChatGPT / trò.
4. **QA không dùng AI** — đúng cấu trúc và độ dài; mọi `[S…]` trỏ tới nguồn có thật trong hồ sơ; đủ số nguồn; mỗi đoạn thân bài có trích dẫn; đoạn văn bám nguồn (≥ 45% từ ngữ có trong nguồn được trích); **mọi con số phải có trong tư liệu**; không lời dẫn kiểu “dưới đây là”, không chèn link. Bài của trò trượt QA → thầy viết lại, trò bị trừ điểm.
5. **Chủ kênh** duyệt (hoặc sửa rồi duyệt → bản sửa thành bài mẫu tốt nhất), góp ý (thành bài học), loại (không dùng làm mẫu). Tải bài kèm danh mục nguồn dạng `.md`.

Chi phí: một bài 1.200 từ do Sonnet 5.5 viết khoảng 0,05–0,10 USD; phần ngân sách của phòng là 10% trần tháng (~4,75 USD ≈ 50–70 bài). Ở cấp Bậc thầy: $0. Chế độ DEMO dùng tư liệu giả lập `fixtures/research_demo.json` (bật *DEMO vẫn tìm trên mạng thật* để thử với Wikipedia); chưa có thầy lẫn trò thì ra bản nháp ghép trích đoạn gắn nhãn DEMO.

**Kỳ vọng thực tế:** bài luận dài do model 7B viết sẽ kém Claude/ChatGPT về lập luận và câu chữ; QA chỉ bảo đảm bài đúng khuôn và bám nguồn, không bảo đảm hay. Muốn lên Bậc thầy nhanh và chắc hơn, dùng model trò lớn hơn nếu máy đủ mạnh (ví dụ `qwen2.5:14b-instruct`, cần ~16 GB RAM) và duyệt/sửa bài thường xuyên.

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
frontend/                         Next.js 15 + TypeScript (xuất tĩnh → backend/ev_studio/web_dist, backend phục vụ)
migrations/                       Alembic
tests/                            133 test
run.py, start.bat, start.sh       chạy bằng một lệnh
backend/ev_studio/runtime_config.py  cấu hình API lưu mã hoá trong DB
backend/ev_studio/agents/            Ban Tài chính (budget, pricing) + bộ não phòng ban (brain, memory, teachers, learning, writer)
backend/ev_studio/error_log.py       Nhật ký lỗi: WARNING+ của ev_studio.* → bảng error_logs (che secret, gộp lỗi trùng)
```

**Ba loại duyệt được tách riêng:**
- (a) Xác minh tin và claim: chặn ngay từ khâu sản xuất.
- (b) Media QA (14 kiểm tra: codec, 1080p, 30fps, AAC 48k, thời lượng, đen hình, đứng hình, clipping, giấy phép, nguồn, thumbnail, font, SHA-256…): chặn upload.
- (c) Chủ kênh duyệt video cuối trên Telegram hoặc dashboard: **không** kích hoạt công khai.

## 5. Vận hành
- **Backup:** dump Postgres (`pg_dump`) và thư mục artifacts (volume `artifacts`). Artifact là immutable, có checksum, và được ghi qua file tạm rồi đổi tên nguyên tử.
- **Gia hạn token:** client Google tự refresh rồi ghi đè token đã mã hoá. Nếu refresh thất bại, story chuyển `NEEDS_REVIEW` với lý do `BLOCKED_BY_CREDENTIALS`, và chủ kênh cần kết nối lại kênh.
- **Mất mạng:** retry theo backoff. Upload dùng resumable. Sau mỗi lần insert, video id được lưu ngay. Trước khi upload lại, hệ thống tìm video theo tag `evcs-<hash>` để không đăng trùng. Bấm **Chạy tiếp / thử lại** sẽ resume từ checkpoint.
- **Nhật ký lỗi** (menu bên trái): mọi cảnh báo và lỗi của tool — quét tin, Claude, giọng đọc, tư liệu, dựng video, YouTube, Telegram, lịch chạy, API — được ghi lại kèm thời gian, số lần lặp, video và job liên quan, traceback và **gợi ý xử lý** bằng tiếng Việt. Lọc theo mức độ, nguồn, thời gian hoặc tìm kiếm. Bấm **Đã xử lý** khi đã khắc phục (nếu lỗi tái phát sẽ mở nhóm mới). **Sao chép báo cáo lỗi** để gửi cho người hỗ trợ. Key và token luôn được che trước khi lưu. Bảng giữ tối đa 5.000 nhóm lỗi gần nhất; nút **Xoá lỗi đã xử lý** chỉ xoá lỗi đã đánh dấu. Bảng điều khiển hiện cảnh báo khi còn lỗi chưa xử lý, và trang video có đường dẫn tới lỗi của riêng video đó.
- **Kiểm tra định kỳ:** `PYTHONPATH=backend python3 scripts/check_privacy.py` (sẽ fail nếu có video nào không private mà chưa được ghi nhận là chủ kênh tự công khai).
- **Chi phí** (ước tính, tham khảo):
  - Claude/ChatGPT: xem mục 3b. Trần cứng theo tháng/ngày/phòng ban/video; sổ cái ở `/api/budget`, bảng giá ở `agents/pricing.py` (bổ sung qua `model_prices_json`).
  - VieNeu: chạy CPU, khoảng 1–3 lần thời gian thực cho 10 phút audio (tuỳ CPU). Có GPU thì nhanh hơn nhiều.
  - Render: khoảng 3–6 phút CPU cho mỗi video 10 phút 1080p (preset veryfast).
  - YouTube: quota `videos.insert` dùng bucket riêng từ 11/2026, xem số hiện hành trong Google Cloud Console.

## 6. Giới hạn đã biết (trung thực)
- Chưa chạy với credential thật (Anthropic, Google OAuth, Telegram, Pexels) và chưa có model weights VieNeu. Các adapter thật đã được viết đúng API và test bằng mock HTTP, nhưng nghiệm thu thật vẫn là **BLOCKED_BY_CREDENTIALS**.
- Chưa tích hợp forced alignment tiếng Việt. Phụ đề căn theo mốc thời gian thật của từng segment TTS.
- Chưa có vision tự kiểm tra mẫu xe trong ảnh. Ảnh gắn mẫu xe khác chủ đề tự động bị dán nhãn "minh họa"; ảnh không ghi mẫu xe cũng bị coi là minh họa.
- Ken Burns chỉ áp dụng cho cảnh ảnh thật. Chưa trộn nhạc nền.
- Dashboard dùng CSS thuần thay cho Tailwind, để giảm phụ thuộc build.
- `run.py` đã được chạy thử trên Linux từ thư mục sạch (tạo `.venv`, cài thư viện, migrate, phục vụ giao diện). Chưa thử trên máy Windows thật; đường dẫn FFmpeg, font và venv đã được viết để chạy được trên Windows.
