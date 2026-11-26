# Gỡ khoá "video tải lên qua API bị riêng tư": Publish app + đơn audit YouTube API

## Vì sao cần làm
Theo tài liệu YouTube Data API (`videos.insert`), mọi video tải lên qua API từ **project Google Cloud chưa qua kiểm định, tạo sau 28/07/2020**
bị giới hạn ở chế độ **riêng tư**. Muốn gỡ, project phải qua **audit** để xác nhận tuân thủ Điều khoản YouTube API.
Khi chưa audit, EV Creator Studio vẫn làm video và tải lên được, nhưng cả app lẫn chủ kênh đều không công khai được các video đó.
App sẽ báo "YouTube giữ video ở chế độ RIÊNG TƯ" và nhắn Telegram.

## Bước 1 — Bật GitHub Pages (1 lần, ~1 phút)
1. Mở https://github.com/alohabeauty36-bot/youtube-ai/settings/pages
2. **Source**: Deploy from a branch → **Branch**: `main`, thư mục **/docs** → **Save**.
3. Đợi 1–2 phút, mở thử:
   - Trang chủ: https://alohabeauty36-bot.github.io/youtube-ai/
   - Chính sách quyền riêng tư: https://alohabeauty36-bot.github.io/youtube-ai/privacy.html

## Bước 2 — Điền Branding và Publish app (Google Cloud)
Google Auth Platform → **Branding**:
- App name: `EV Creator Studio`
- User support email: email của bạn
- Application home page: `https://alohabeauty36-bot.github.io/youtube-ai/`
- Application privacy policy link: `https://alohabeauty36-bot.github.io/youtube-ai/privacy.html`
- Authorized domains: `alohabeauty36-bot.github.io`
- Developer contact: email của bạn → **Save**

Google Auth Platform → **Data Access** → thêm đủ 5 quyền:
`youtube.upload`, `youtube.readonly`, `youtube.force-ssl`, `yt-analytics.readonly`, `yt-analytics-monetary.readonly`.

Google Auth Platform → **Audience** → **Publish app**. Sau đó vào app EV Creator Studio, bấm **Kết nối kênh YouTube** lại
(để cấp quyền `youtube.force-ssl` mới và lấy token không hết hạn sau 7 ngày). Màn hình "Google chưa xác minh ứng dụng" → Nâng cao → Đi tới ứng dụng.

## Bước 3 — Nộp đơn audit YouTube API
Mẫu đơn: https://support.google.com/youtube/contact/yt_api_form (chọn loại yêu cầu audit / gỡ giới hạn cho project).
Thông tin cần có:
- **Project number**: `947709004118`
- **Tên ứng dụng**: EV Creator Studio
- **Link**: trang chủ và trang chính sách quyền riêng tư ở Bước 1
- **Kênh**: Vựa Tin 365 — ID `UC6-0esZ-9mdYTUXshi3iU_g`
- Ảnh chụp màn hình hoặc video ngắn quay quy trình: kết nối OAuth → tải video lên → video ở trạng thái riêng tư.

### Nội dung mô tả soạn sẵn (tiếng Anh — dán vào đơn, sửa nếu cần)
> EV Creator Studio is a self-hosted internal tool used by one person, the owner of the YouTube channel "Vựa Tin 365"
> (channel ID UC6-0esZ-9mdYTUXshi3iU_g), to produce and publish videos on that channel only. It has no other users and
> never accesses any other channel.
>
> API usage:
> - YouTube Data API `videos.insert` (resumable upload): uploads videos produced by the tool to the owner's channel as private.
> - `videos.list` (status): verifies each upload is private and confirms the authorized channel matches the configured channel.
> - `videos.update` (status only): when the owner enables full automation, switches the tool's own videos from private to
>   public after a waiting period (default 2 hours) during which the owner is notified on Telegram and can cancel.
> - `channels.list` (mine): confirms which channel is authorized.
> - YouTube Analytics API `reports.query` (channel==MINE): reads the owner's own daily and per-video views, watch time and
>   estimated ad revenue to plan content against a monthly revenue goal.
>
> Videos are long-form (≥ 10 minutes) Vietnamese commentary on electric-vehicle news. Every factual claim is checked against
> the cited public source before rendering, sources are listed in the description, and AI voice-over is disclosed with
> `status.containsSyntheticMedia = true`. OAuth tokens are encrypted and stored only on the owner's computer. Expected volume:
> about 2–6 uploads per day. Privacy policy: https://alohabeauty36-bot.github.io/youtube-ai/privacy.html

## Lưu ý
- Audit do Google xét, thời gian tuỳ Google; có thể được hỏi thêm. Trả lời trung thực theo đúng cách app hoạt động.
- Chính sách YouTube về nội dung lặp lại/hàng loạt vẫn áp dụng cho kênh dùng giọng AI. Mỗi video cần có phân tích riêng.
