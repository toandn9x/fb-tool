# Facebook Fanpage Auto-Reply Bot

Bot Python tự động trả lời comment trên fanpage Facebook sử dụng AI (OpenRouter) và ghi log vào Google Sheets.

## 🤖 Tính năng
- **Tự động trả lời:** Sử dụng AI từ OpenRouter (hỗ trợ nhiều model miễn phí như Llama 3, Gemini 2.0).
- **Auto Like/React Comment:** Tự động Like hoặc React (LOVE, HAHA, WOW, SAD, ANGRY) mọi comment mới, thực hiện ngay lập tức trước khi reply.
- **Lọc comment thông minh:** Tự động bỏ qua emoji, comment quá ngắn hoặc comment trùng lặp.
- **Delay thông minh:** Cấu hình thời gian chờ giữa các lần reply để tránh bị Facebook đánh dấu spam.
- **Ghi log chuyên nghiệp:** Lưu lịch sử comment, reply và trạng thái like vào Google Sheets.
- **Tùy chỉnh Prompt:** Dễ dàng thay đổi "tính cách" của bot qua file `prompts.json`.

---

## 🛠 Cài đặt

### 1. Tải về và cài đặt thư viện
```bash
cd fb-tool
pip install -r requirements.txt
```

### 2. Cấu hình file `.env`
Tạo file `.env` từ file mẫu và điền các thông tin cần thiết:
```bash
cp .env.example .env
```

| Biến | Mô tả |
|---|---|
| `FB_PAGE_ACCESS_TOKEN` | Token của Fanpage (Lấy từ Facebook Developer) |
| `FB_VERIFY_TOKEN` | Mã xác minh tự đặt cho Webhook |
| `FB_APP_SECRET` | Mã bí mật của ứng dụng Facebook |
| `OPENROUTER_API_KEY` | API key từ OpenRouter (Có thể bỏ trống để dùng tin nhắn mặc định) |
| `OPENROUTER_MODEL` | ID Model AI (Mặc định: `meta-llama/llama-3.3-70b-instruct:free`) |
| `GOOGLE_SHEETS_CREDENTIALS` | Tên file JSON key (Mặc định: `credentials.json`) |
| `GOOGLE_SHEET_NAME` | Tên file Google Sheet để ghi log |
| `REPLY_DELAY_SECONDS` | Thời gian chờ giữa các reply (giây) |
| `AUTO_LIKE_ENABLED` | Bật/tắt Auto Like (Mặc định: `true`) |
| `AUTO_LIKE_REACTION_TYPE` | Loại reaction: `LIKE`, `LOVE`, `HAHA`, `WOW`, `SAD`, `ANGRY` (Mặc định: `LIKE`) |

---

## 📊 Hướng dẫn thiết lập Google Sheets
1.  Truy cập [Google Cloud Console](https://console.cloud.google.com/).
2.  Bật **Google Sheets API** và **Google Drive API**.
3.  Vào **IAM & Admin** > **Service Accounts** > Tạo một Service Account mới.
4.  Tạo **Key** mới (định dạng JSON), tải về và đổi tên thành `credentials.json`, đặt vào thư mục gốc của dự án.
5.  Tạo một Google Sheet mới, đặt tên trùng với `GOOGLE_SHEET_NAME` trong `.env`.
6.  Nhấn nút **Chia sẻ** (Share) trong Google Sheet và cấp quyền **Editor** cho email của Service Account vừa tạo.

---

## 🔗 Hướng dẫn thiết lập Facebook Webhook

### Bước 1: Cấu hình Webhook trên App Facebook
1. Truy cập [developers.facebook.com](https://developers.facebook.com/).
2. Thêm sản phẩm **Webhooks** > Chọn đối tượng là **Page**.
3. **Callback URL:** `https://[link-ngrok-cua-ban].ngrok-free.app/webhook` (Lưu ý phải có `/webhook` ở cuối).
4. **Verify Token:** Nhập đúng mã bạn đã đặt trong file `.env`.
5. Cuộn xuống tìm trường **feed**, nhấn **Subscribe** (Đăng ký).

### Bước 2: Cấp quyền và Đăng ký Page (Quan trọng)
Sử dụng [Graph API Explorer](https://developers.facebook.com/tools/explorer/) để bót có quyền hoạt động:

1.  **Chọn App** và **Lấy Page Access Token** với các quyền sau:
    *   `pages_show_list`
    *   `pages_read_engagement`
    *   `pages_manage_metadata`
    *   `pages_manage_engagement`
2.  **Đăng ký Page vào App:**
    *   Chọn phương thức **POST**.
    *   Nhập URL: `me/subscribed_apps?subscribed_fields=feed`
    *   Nhấn **Submit**. Nếu nhận được `{"success": true}` là thành công.

---

## 🚀 Chạy Bot
Mở bót tại máy local (Cổng mặc định là 8000 hoặc theo cấu hình `.env`):
```bash
python main.py
```

Nếu dùng ngrok để test:
```bash
ngrok http 8000
```

---

## 💡 Kinh nghiệm & Lưu ý (Troubleshooting)

> [!TIP]
> **Lỗi không nhận Webhook:** Nếu nút "Test" trên Facebook hoạt động nhưng comment thật không chạy, hãy kiểm tra xem bạn đã chạy lệnh `POST me/subscribed_apps` chưa. Đây là bước bắt buộc để Fanpage gửi dữ liệu sang App.

> [!IMPORTANT]
> **Về Token:** Hãy đảm bảo bạn dùng **Page Access Token** (Token của Trang) chứ không phải User Token. Nếu dùng User Token, bạn sẽ gặp lỗi `publish_actions are not available`.

> [!NOTE]
> **Tại sao phải dùng nick khác để test?** Bot được lập trình để tự động bỏ qua các bình luận từ chính Fanpage gửi đi (để tránh vòng lặp vô tận). Hãy dùng một tài khoản cá nhân khác để thử nghiệm.

> [!WARNING]
> **Lỗi 429 (Too Many Requests):** Nếu sử dụng model AI miễn phí, đôi khi bạn sẽ gặp lỗi này do quá tải. Bot sẽ tự động chuyển sang tin nhắn mặc định: *"Cảm ơn bạn đã quan tâm, follow page để theo dõi các bài viết hấp dẫn tiếp theo nhé."*

> [!TIP]
> **Hướng dẫn lấy Token vĩnh viễn (Long-lived Page Token):**
> 1. **Bước 1 (Lấy Token 60 ngày):** Vào [Graph API Explorer](https://developers.facebook.com/tools/explorer/), nhấn vào biểu tượng **(i)** cạnh mã Token > **Open in Access Token Tool** > **Extend Access Token**.
> 2. **Bước 2 (Lấy Token vĩnh viễn):** Copy mã 60 ngày quay lại Graph API Explorer, dán vào ô Access Token. Nhập URL: `me/accounts?access_token=LONG_LIVED_TOKEN` > nhấn **Submit (GET)**.
> 3. **Bước 3:** Tìm đúng Fanpage của bạn trong danh sách hiện ra, copy mã `access_token` của Page đó và dán vào file `.env`. (Token này sẽ không bao giờ hết hạn trừ khi bạn đổi mật khẩu Facebook).
---

## 📂 Cấu trúc thư mục
- `main.py`: Server chính xử lý webhook.
- `config.py`: Quản lý cấu hình và biến môi trường.
- `prompts.json`: Nơi tùy chỉnh nội dung AI trả lời.
- `facebook_client.py`: Các hàm tương tác với Facebook Graph API.
- `openrouter_client.py`: Kết nối với API AI.
- `sheets_logger.py`: Xử lý ghi log vào Google Sheets.
- `comment_filter.py`: Bộ lọc thông minh cho bình luận.
- `.gitignore`: Đã cấu hình để bỏ qua các file nhạy cảm như `.env` và `credentials.json`.
