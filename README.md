# EV Creator Studio

Tòa soạn video AI **có người duyệt cuối** cho kênh YouTube tiếng Việt về VinFast và xe điện (ba trụ cột P1, P2, P3).
Quy trình: quét tin hợp lệ → gom nguồn, tách claim, xác minh → chấm điểm → Claude viết ResearchPack và kịch bản **ít nhất 10 phút** (mục tiêu 11 phút) → VieNeu-TTS đọc (đo thời lượng bằng audio thật) → tư liệu có giấy phép hoặc đồ họa tự dựng → FFmpeg 1080p, phụ đề, thumbnail, SEO → **tải lên YouTube ở chế độ RIÊNG TƯ** → đọc lại để xác nhận private → nhắn Telegram → **chủ kênh tự công khai trên YouTube Studio**, hoặc app tự công khai sau thời gian chờ nếu bật Tự động hoàn toàn.

> 🔒 **Quyền riêng tư:** mọi upload dùng `privacyStatus="private"`, bị kiểm tra ba lớp (hàm tạo body, client ngay trước khi gọi API, ràng buộc CHECK trong DB) và được đọc lại bằng `videos.list`. Mặc định app **không bao giờ** công khai video: bấm "Đã kiểm duyệt" chỉ đổi trạng thái trong DB thành `APPROVED_PRIVATE`. Ngoại lệ duy nhất là chế độ **Tự động hoàn toàn** do chủ kênh tự bật (mục 3c): video được công khai sau thời gian chờ (mặc định 2 giờ), chỉ khi đã báo Telegram kèm nút **⛔ Dừng tự công khai**, chủ kênh không bấm Dừng, và kiểm chứng lại ngay trước đó vẫn đạt. Toàn bộ việc công khai nằm ở `services/youtube/publish.py`.

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

**Giọng đọc thật:** nếu chỉ chạy `start.bat`/`run.py` thường, máy chưa có VieNeu-TTS và audio là **âm giả lập (tiếng ù/bíp), không phải giọng nói**. Để có giọng thật: Windows bấm đúp **`start-giong-that.bat`**; macOS/Linux chạy `python run.py --with-tts`. Lần đọc đầu tiên cần Internet để tải model (vài trăm MB). Kiểm tra ở **Thiết lập → tab Giọng đọc VieNeu → Kiểm tra**: ứng dụng đọc thử một câu và phát cho bạn nghe, hoặc báo lỗi thật (chưa cài, không tải được model, sai tên giọng). Khi đã cài VieNeu, ứng dụng dùng giọng thật cả ở chế độ DEMO.

Lần đầu, ứng dụng ở chế độ **DEMO**: mọi dữ liệu đều giả lập và gắn nhãn, không upload thật. Bạn bấm **Săn tin → Quét tin ngay** để xem cả quy trình hoạt động.

**Thời lượng: video không bao giờ dưới 10 phút.** Mặc định tối thiểu 600 giây, mục tiêu 660 giây, tối đa 720 giây, đo bằng audio thật. Audio ngắn hơn 10 phút → Claude viết lại (tối đa 3 lần), vẫn ngắn → video dừng ở `NEEDS_REVIEW`, không render, không upload. Media QA có thêm kiểm tra cứng *Không dưới 10 phút* trên file MP4 cuối. Trang Thiết lập từ chối giá trị dưới 600 giây; file `.env` cũ còn `DURATION_MIN_SECONDS=570` được tự nâng lên 600/660/720.

**Quét tin ở chế độ production.** Lần quét đầu tiên khi chưa có nguồn RSS nào, ứng dụng tự thêm 4 nguồn mặc định: mục Xe của VnExpress, Tuổi Trẻ, Thanh Niên, Dân trí (chặn hoặc thêm nguồn ở **Thiết lập → Nguồn tin**). Sau mỗi lần bấm **Quét tin ngay**, trang hiện kết quả: số bài lấy được, bài mới, bài trùng, và **lý do của từng nguồn lỗi** (URL feed sai, trang trả 404, robots.txt chặn, mất mạng, vừa quét xong nên phải đợi giãn cách 60 giây). Một nguồn lỗi không làm hỏng các nguồn khác. Chưa nhập key Claude thì vẫn quét được; tin được lưu ở trạng thái `research` cho tới khi có Claude để tách và xác minh claim.

## 2. Cấu hình API ngay trong ứng dụng

Mở **Thiết lập**. Trang chia tab, mỗi tab là cấu hình của một phần (Chung · Kết nối dịch vụ · Sản xuất video · Vận hành); chấm màu cạnh tab cho biết dịch vụ đã kết nối (xanh) hay chưa nhập key (vàng), tab có thay đổi chưa lưu ghi "chưa lưu". Không cần sửa file nào. Key và token được mã hoá trước khi lưu vào database. Sau khi lưu, ứng dụng chỉ hiện lại 4 ký tự cuối. Giá trị nhập ở đây ghi đè file `.env`. Mỗi nhóm đều có nút **Kiểm tra** để thử kết nối.

| Nhóm | Bạn nhập | Nút "Kiểm tra" làm gì |
|---|---|---|
| Claude (Anthropic) | API key (tạo ở console.anthropic.com), chọn model | Liệt kê model mà tài khoản được dùng; bấm vào tên model để chọn |
| Giọng đọc VieNeu | `vieneu_local` (sau khi chạy `python run.py --with-tts`) hoặc `vieneu_http` + địa chỉ server GPU; chọn giọng | Kiểm tra thư viện đã cài hoặc server có phản hồi |
| YouTube | Dán hoặc tải lên `client_secret.json` (OAuth client loại *Web application*), ID kênh đích | Kiểm tra JSON và redirect URI. Trang hiển thị sẵn URI để bạn sao chép vào Google Cloud Console |
| Telegram | Bot token (lấy từ @BotFather) | Xác nhận bot hoạt động, liệt kê các chat vừa nhắn `/start` để bấm **Cho phép**, rồi gửi tin nhắn thử |
| Kho tư liệu | **Pixabay key** (miễn phí), Pexels key cũ (tuỳ chọn), bật/tắt Wikimedia | Tìm thử video + ảnh |
| Biên tập, Dựng video, Bảo mật | Thời lượng (không dưới 600 giây), ngưỡng Autopilot, preset FFmpeg, mật khẩu chủ kênh/biên tập viên | — |

Sau khi nhập key, đổi **Chế độ** sang `production`, rồi bấm **Kết nối kênh YouTube** để cấp quyền và chọn đúng kênh. Nếu thiếu key, video sẽ dừng ở trạng thái `BLOCKED_BY_CREDENTIALS` kèm đường dẫn mở trang Thiết lập, chứ không lặng lẽ chuyển sang dữ liệu giả lập.

**Chuyển sang chạy thật sẽ xoá hết dữ liệu DEMO.** Khi lưu **Chế độ** = `production`, ứng dụng hiện hộp xác nhận kèm số lượng sẽ xoá, rồi xoá trong cùng một lần lưu:
- Kênh, nguồn tin, tin fixture (kèm claim), mọi video DEMO và các bản viết lại, cùng ResearchPack, kịch bản, giọng đọc, cảnh, tư liệu, render, SEO, upload và tin Telegram giả lập, job, lượt duyệt và lỗi gắn với các video đó.
- Bài luận DEMO, chi phí ước tính DEMO, bộ nhớ AI học từ dữ liệu DEMO.
- File: `artifacts/<video>`, YouTube/Telegram giả lập, `media_library/demo`, cache giọng giả lập.
- **Autopilot bị tắt** nếu đang bật (thường do chạy thử DEMO). Bật lại ở **Thiết lập → tab Tự động hoàn toàn** khi đã sẵn sàng.

Giữ nguyên: cấu hình và key đã nhập, **sổ cái ngân sách** (tiền thật đã tiêu, kể cả khi thử Claude lúc DEMO), nhật ký audit, hồ sơ năng lực phòng ban và mọi dữ liệu thật. Không hoàn tác được. Nếu còn video DEMO đang chạy dở, ứng dụng từ chối đổi chế độ và báo đợi chạy xong. Nếu đặt `APP_MODE=production` trong `.env`, dữ liệu DEMO còn sót sẽ được xoá ngay khi khởi động.

**Truy cập:** khi mở trên chính máy chạy ứng dụng (127.0.0.1), bạn không cần mật khẩu. Khi truy cập từ máy khác, ứng dụng yêu cầu mật khẩu chủ kênh hoặc biên tập viên. Nếu ứng dụng nằm sau reverse proxy, các request có header proxy **không** được tin là truy cập cục bộ.

**Dành cho lập trình viên:** `make test` chạy 205 test. `make demo` chạy toàn tuyến DEMO 10 phút. Docker: `docker compose up -d --build` (Postgres + Redis + worker VieNeu), rồi mở http://localhost:8010.

### Windows 11
Nên dùng WSL2 (Ubuntu) với Docker Desktop. Cách native cũng chạy được với Python 3.12, FFmpeg (gyan.dev, có libass) và `pip install vieneu` (CPU/ONNX). GPU CUDA trên Windows cần cài `torch==2.8.0` cu128 trước, theo README của VieNeu.

## 3. Hai chế độ
- **MANUAL:** chủ kênh chọn tin ở trang Săn tin. Pipeline dừng ở *Bản nháp kịch bản* để chờ duyệt, rồi dừng ở *Sẵn sàng tải RIÊNG TƯ* để chờ chủ kênh tick xác nhận và bấm upload.
- **AUTOPILOT_PRIVATE** (bật trong Settings): tự chọn tin có điểm ≥ 80/100 và có ít nhất một dữ kiện trọng yếu có nguồn (xem **Chế độ phân tích** bên dưới), tối đa 3 tin/ngày và chỉ khi Ban Tài chính còn đủ ngân sách. Pipeline chạy đến upload RIÊNG TƯ và Telegram, nhưng **dừng và chuyển sang `NEEDS_REVIEW`** nếu thiếu nguồn (ở chế độ chặt: cả khi claim đang tranh cãi), sai thời lượng (dưới 10 phút hoặc quá dài) sau 3 lần viết lại, QA không đạt, sai kênh, hoặc thiếu credential (`BLOCKED_BY_CREDENTIALS`).

## 3c. Tự động hoàn toàn
Một công tắc trên **Bảng điều khiển** (hoặc Thiết lập → Tự động hoàn toàn). Khi bật, mỗi 10 phút app tự làm:
- **Quét tin và Autopilot**: lịch quét và Autopilot được coi là đang bật. Thiếu tin ≥ 80 điểm thì lấy tin trong shortlist (≥ 75 điểm), vẫn bắt buộc có dữ kiện trọng yếu có nguồn. Số video/ngày theo Ban Tăng trưởng, luôn trong trần ngân sách AI.

### Chế độ phân tích (mặc định từ 27/11/2026)

Thiết lập → tab **Biên tập & thời lượng** → **Cách xử lý tin chưa xác minh**:

- **analysis** (mặc định): video là **bài phân tích/bình luận tin báo chí đã đăng**, không khẳng định tin đó là sự thật. Thông tin mới có **một báo** đưa (chưa có nguồn thứ hai) có trạng thái **"đã đưa tin"** (`reported`) và **được dùng**, với điều kiện câu nói phải ghi nguồn ngay trong câu ("Theo VnExpress, …", "Tuổi Trẻ đưa tin…"). Script QA chặn câu dùng tin chưa xác minh mà không ghi nguồn. Tin có các báo đưa số khác nhau không còn làm dừng video; số liệu đó chỉ không được nói như dữ kiện. Mô tả video có thêm dòng: video phân tích dựa trên thông tin các báo đã đăng, một số chưa được nguồn thứ hai xác nhận.
- **Không bịa (ở cả hai chế độ)**: thông tin không có nguồn, hoặc trích dẫn không có trong bài báo gốc, bị loại. Con số, trích dẫn, tên người, ngày tháng chỉ lấy từ hồ sơ tư liệu; nhận định phải nói rõ là nhận định.
- **strict**: như trước: chỉ dùng thông tin có nguồn chính thức hoặc ≥ 2 báo uy tín xác nhận; tin chưa xác minh/tranh cãi chặn dựng và tải lên.

Đổi chế độ xong, lần quét tin hoặc lượt Autopilot kế tiếp tự xếp lại tin trong 14 ngày gần nhất theo chế độ mới.
- **Tự sửa lỗi**: video bị dừng (`NEEDS_REVIEW`, `FAILED_*`) được chạy lại tối đa `auto_heal_max_attempts` lần (mặc định 2; kịch bản trượt QA thì viết bản mới). Vẫn hỏng → **bỏ tin đó** (`REJECTED`, ghi lý do) để Autopilot chọn tin khác. Không đụng video dừng vì hết ngân sách (đợi sang ngày mới) hoặc thiếu key/OAuth (chủ kênh phải sửa).
- **Tự công khai có hẹn giờ** (`auto_publish_delay_minutes`, mặc định 120; 0 = không tự công khai): tin nhắn Telegram ghi giờ công khai và có nút **⛔ Dừng tự công khai**. Ngay trước giờ hẹn, app kiểm chứng lại claim và hạn hiệu lực, kiểm tra Media QA, độ dài ≥ 10 phút, đã báo Telegram. Không đạt → giữ riêng tư và nhắn lý do. Đạt → `videos.update` sang public, đọc lại, chuyển `PUBLISHED_AUTO`, nhắn link.
- **Cần quyền mới**: `youtube.force-ssl` (đổi trạng thái video). Bấm **Kết nối kênh YouTube** lại sau khi cập nhật.

⚠️ **Project Google Cloud chưa qua audit YouTube API**: video tải lên qua API bị YouTube khoá riêng tư; app sẽ báo "YouTube giữ video ở chế độ RIÊNG TƯ" và nhắn Telegram. Làm theo [`docs/youtube-api-audit.md`](docs/youtube-api-audit.md): bật GitHub Pages (trang chủ + chính sách quyền riêng tư có sẵn trong `docs/`), Publish app OAuth (hết phải kết nối lại mỗi 7 ngày), rồi nộp đơn audit.

## 3d. Hình ảnh video
- **Phòng Đạo diễn hình ảnh** (Claude Haiku, ~0,002–0,005 USD/video; không có Claude thì dùng từ điển chủ đề xe điện) lên danh sách cảnh quay: mỗi cảnh 3–9 giây có một từ khoá tiếng Anh để tìm tư liệu, một câu chữ nhấn mạnh, và loại cảnh (video nền / chữ nhấn / câu hỏi / số liệu). Số liệu trên màn hình **chỉ lấy từ claim đã xác minh** (trường value/unit), không để AI tự viết số.
- **Tư liệu theo từng cảnh**: **video B-roll Pixabay** trước (bản ngang ~1920×1080), rồi video Pexels nếu còn key cũ, ảnh Pixabay (chỉ cho từ khoá chưa có video), ảnh Pexels, Wikimedia Commons, thư viện của chủ kênh (`media_library/`, ảnh hoặc video kèm `.license.json`). Mọi tư liệu qua license gate. Clip tải về lưu ở `artifacts/_stock_cache/` để video sau dùng lại; kết quả tìm Pixabay được lưu đệm 24 giờ đúng điều khoản API. **Cần Pixabay API key** (miễn phí, cấp ngay: đăng ký pixabay.com → đăng nhập → pixabay.com/api/docs, key nằm trong mục Parameters) ở Thiết lập → tab Kho tư liệu. **Pexels đã tạm dừng cấp key mới** (từ cuối 9/2026): ai còn key cũ thì vẫn nhập được. Thiếu cả hai thì chỉ có Wikimedia, thư viện chủ kênh, ảnh AI và nền đồ họa.
- **Dựng nhiều lớp**: nền luôn chuyển động (video B-roll, ảnh có Ken Burns 4 hướng, hoặc nền đồ họa có Ken Burns), lớp chữ trượt vào (chữ nhấn góc trên, thẻ dữ kiện có nguồn, số liệu cỡ lớn, câu hỏi), logo kênh góc trên phải, dải tối dưới đáy để phụ đề luôn rõ. Cùng một tư liệu không lặp ở cảnh liền kề.
- **Ảnh minh họa AI** (FLUX.1 schnell): chỉ lấp những từ khoá mà kho tư liệu không có video/ảnh thật, tối đa `ai_images_per_video` (mặc định 12) ảnh/video. Nguồn `auto`: **Cloudflare Workers AI** trước (miễn phí trong hạn mức 10.000 neurons/ngày; app dừng ở `cloudflare_daily_images` = 150 ảnh/ngày), hết lượt hoặc thiếu key thì **fal.ai** (~0,003 USD/ảnh, mỗi ảnh được Ban Tài chính giữ chỗ trong trần ngân sách). Prompt bỏ tên thương hiệu/mẫu xe/số, yêu cầu không chữ, không logo, không biển số, không khuôn mặt nhận ra được; ảnh được đánh dấu AI, góc màn hình ghi "Ảnh minh họa AI", mô tả video ghi công. Cấu hình ở **Thiết lập → tab Ảnh AI (Cloudflare · fal.ai)** (nút Kiểm tra tạo thử 1 ảnh). Ảnh đã tạo lưu ở `artifacts/_ai_cache/` để dùng lại.
- **Nhạc nền**: đặt file nhạc vào `media_library/music/` kèm `<tên file>.license.json`, ví dụ nhạc tải từ YouTube Studio → Thư viện âm thanh: `{"license_name": "YouTube Audio Library", "allowed_commercial": true, "source_url": "…", "attribution_text": "Tên bài — Tác giả"}`. Nhạc ở mức `music_volume_db` (mặc định -20 dB) và tự giảm thêm khi có giọng đọc; ghi công tự thêm vào mô tả video.
- **Luôn 16:9**: video 1920×1080 (Media QA kiểm tra cả DAR 16:9 và SAR 1:1), thumbnail 1280×720, ảnh AI đưa về 1920×1080. Tư liệu dọc hoặc vuông (rộng/cao < 1,3) không được dùng làm nền vì cắt sang 16:9 sẽ mất chủ thể; Wikimedia chỉ lấy ảnh ngang.
- Trang **Tư liệu & cảnh** hiện từng cảnh (ảnh xem trước có lớp chữ), loại cảnh, video hay ảnh, và từ khoá đã tìm.

## 3b. Ngân sách AI và bộ não phòng ban

### Ban Tài chính — trần chi tiêu cứng
Mặc định **50 USD/tháng** cho Claude + ChatGPT (chỉnh ở **Thiết lập → Ngân sách AI**). Trước *mọi* lần gọi AI, ứng dụng giữ chỗ chi phí xấu nhất (token vào ước tính dư + toàn bộ `max_tokens` ra). Chạm bất kỳ trần nào thì **không gọi**, video dừng ở `NEEDS_REVIEW` với lý do `BLOCKED_BY_BUDGET`, bấm **Chạy tiếp** khi có ngân sách.

| Trần | Mặc định | Ghi chú |
|---|---|---|
| Tháng | 50 USD, giữ lại 5% phòng sai số → chi tối đa 47,5 USD | cứng |
| Ngày | phần còn lại của tháng ÷ số ngày còn lại, **không quá 2× bình quân** (~3,2 USD) | chống một ngày tiêu quá tay |
| Phòng ban | Săn tin 14%, Nghiên cứu 22%, Biên kịch 35%, Biên tập 6%, Bài luận 10%, Đào tạo 3%, Tăng trưởng 2%, Đạo diễn hình ảnh 8% (gồm ảnh fal.ai) | một phòng không ăn hết phần của phòng khác |
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

### Ban Tăng trưởng — theo dõi kênh và chạy theo KPI doanh thu tháng
Trang **Tăng trưởng & KPI**: nhập KPI doanh thu quảng cáo mỗi tháng (USD). Ban Tăng trưởng đọc **YouTube Analytics** của kênh qua OAuth chính thức (chỉ đọc), mỗi sáng 6:23 hoặc khi bấm **Đồng bộ & lập kế hoạch**:

1. **Đồng bộ** ($0): lượt xem, giờ xem, người đăng ký, doanh thu quảng cáo ước tính (`estimatedAdRevenue`) theo ngày ~62 ngày gần nhất; top 200 video trong 28 ngày; giờ xem 365 ngày.
2. **Tính KPI** ($0): đã kiếm trong tháng, dự báo cuối tháng (tốc độ 7 ngày gần nhất), còn thiếu, RPM 28 ngày, lượt xem/ngày cần có, lượt xem trung bình của một video mới.
3. **Tự chỉnh kế hoạch** ($0, khi bật *Ban Tăng trưởng tự áp dụng kế hoạch*): số video/ngày cho Autopilot (đang thiếu KPI thì không bao giờ giảm; vượt rõ thì có thể giảm để tiết kiệm), và ưu tiên trụ cột P1/P2/P3 theo doanh thu (hoặc lượt xem) mỗi video, mỗi trụ cột giữ tối thiểu 15% để tiếp tục thử. Luôn nằm trong: trần ngân sách AI, *Trần số video/ngày Ban Tăng trưởng được đặt* (mặc định 6) và *Tối đa video/tuần*. Ưu tiên trụ cột chỉ đổi thứ tự tin; ngưỡng điểm và kiểm chứng claim giữ nguyên.
4. **Việc chỉ chủ kênh làm được**: công khai video đang riêng tư (video riêng tư không có doanh thu), nâng trần đang chặn KPI (ghi rõ trần nào), bật Autopilot/lịch quét, kết nối lại kênh để cấp quyền Analytics.
5. **Bản ghi nhớ chiến lược** (tối đa 1 lần/tuần, Claude Haiku, phần ngân sách 2% ≈ 0,95 USD/tháng): chẩn đoán, hành động (agent/chủ kênh), thử nghiệm, rủi ro, và *định hướng cho Phòng Biên kịch*. Định hướng này được đưa vào kịch bản như ghi chú biên tập, không phải dữ kiện. Prompt cấm mọi cách vi phạm chính sách YouTube (mua lượt xem, sub4sub, nội dung lặp hàng loạt, tiêu đề sai sự thật).

**Kết nối:** kênh đã kết nối trước bản này cần bấm **Kết nối kênh YouTube** lại để cấp thêm quyền `yt-analytics.readonly` và `yt-analytics-monetary.readonly`. Doanh thu chỉ đọc được khi kênh đã vào YouTube Partner Program; nếu chưa, Ban Tăng trưởng đặt sản lượng ở mức cao nhất trong trần để tăng giờ xem và người đăng ký, và hiện tiến độ điều kiện YPP (1.000 người đăng ký + 4.000 giờ xem công khai/12 tháng; người đăng ký mới từ 01/02/2027 cần 8.000 giờ).

**Không làm:** không tự công khai video, không sửa tiêu đề/thumbnail video trên kênh, không tự tăng trần chi tiêu. Mọi lần chạy được lưu ở *Lịch sử chạy* và audit log.

**Kỳ vọng thực tế:** KPI là mục tiêu để điều phối, không phải cam kết. Mô hình "cần bao nhiêu video/ngày" là ước tính thô từ số liệu 28 ngày; cuối tháng, video mới chưa kịp có nhiều lượt xem. Kênh dùng giọng AI cần mỗi video có giá trị phân tích riêng để giữ điều kiện kiếm tiền.

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
  workers/schedules.py   APScheduler: quét tin mỗi 3h + Autopilot; RefreshStatus mỗi 6h (chỉ khi bật); Ban Tăng trưởng 6:23 hằng ngày
  state_machine.py       DISCOVERED → … → EDITOR_REVIEW → APPROVED_PRIVATE / REVISION_REQUESTED / REJECTED / PUBLISHED_MANUALLY_OBSERVED
  services/              sources, ranker, fact_checker, claude_writer, script_qa, tts/, media/, storyboard,
                         subtitles, renderer, thumbnail, seo, media_qa, youtube/, telegram/
prompts/youtube_script_10min.md   Mục III nguyên văn (Claude đọc từ file, không hard-code)
prompts/pillars/P1|P2|P3.md       hồ sơ trụ cột
prompts/reference/…               Mục IV nguyên văn (tham khảo văn phong)
frontend/                         Next.js 15 + TypeScript (xuất tĩnh → backend/ev_studio/web_dist, backend phục vụ)
migrations/                       Alembic
tests/                            205 test
run.py, start.bat, start.sh       chạy bằng một lệnh
backend/ev_studio/runtime_config.py  cấu hình API lưu mã hoá trong DB
backend/ev_studio/agents/            Ban Tài chính (budget, pricing) + bộ não phòng ban (brain, memory, teachers, learning, writer) + Ban Tăng trưởng (growth)
backend/ev_studio/error_log.py       Nhật ký lỗi: WARNING+ của ev_studio.* → bảng error_logs (che secret, gộp lỗi trùng)
```

**Ba loại duyệt được tách riêng:**
- (a) Xác minh tin và claim: chặn ngay từ khâu sản xuất.
- (b) Media QA (15 kiểm tra, gồm mức nhạc nền: codec, 1080p, 30fps, AAC 48k, thời lượng, không dưới 10 phút, đen hình, đứng hình, clipping, giấy phép, nguồn, thumbnail, font, SHA-256…): chặn upload.
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
- Chưa chạy với credential thật (Anthropic, Google OAuth, Telegram, Pixabay, Pexels) và chưa có model weights VieNeu. Các adapter thật đã được viết đúng API và test bằng mock HTTP, nhưng nghiệm thu thật vẫn là **BLOCKED_BY_CREDENTIALS**.
- Chưa tích hợp forced alignment tiếng Việt. Phụ đề căn theo mốc thời gian thật của từng segment TTS.
- Chưa có vision tự kiểm tra mẫu xe trong ảnh. Ảnh gắn mẫu xe khác chủ đề tự động bị dán nhãn "minh họa"; ảnh không ghi mẫu xe cũng bị coi là minh họa.
- Ken Burns chỉ áp dụng cho cảnh ảnh thật. Chưa trộn nhạc nền.
- Dashboard dùng CSS thuần thay cho Tailwind, để giảm phụ thuộc build.
- `run.py` đã được chạy thử trên Linux từ thư mục sạch (tạo `.venv`, cài thư viện, migrate, phục vụ giao diện). Chưa thử trên máy Windows thật; đường dẫn FFmpeg, font và venv đã được viết để chạy được trên Windows.
