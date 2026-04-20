# Facebook Fanpage Auto-Reply Bot

Bot Python tự động trả lời comment trên fanpage Facebook sử dụng AI (OpenRouter) và ghi log vào Google Sheets.

## 🤖 Tính năng
- **Tự động trả lời:** Sử dụng AI từ OpenRouter (hỗ trợ nhiều model miễn phí và trả phí).
- **Auto-Switch Model:** Ưu tiên model miễn phí, tự động chuyển sang model trả phí khi bị rate limit, và tự thử lại free sau mỗi 2 tiếng.
- **Auto Like/React Comment:** Tự động Like hoặc React (LOVE, HAHA, WOW, SAD, ANGRY) mọi comment mới.
- **Queue tuần tự chống spam:** Mọi comment được đưa vào hàng đợi FIFO, xử lý lần lượt bởi 1 worker duy nhất. Delay `REPLY_DELAY_SECONDS` áp dụng **chung cho cả react và reply** → không bao giờ bắn nhiều request FB cùng lúc (tránh bị đánh dấu spam khi nhận comment dồn).
- **Worker Monitor real-time:** Trang `/worker` theo dõi trực tiếp comment đang xử lý, stage hiện tại (react/filter/AI/reply), elapsed time, counter (đang chờ / đã reply / bỏ qua / lỗi) và lịch sử comment trong ngày.
- **Dashboard trực quan:** Trang chủ và Dashboard dark-theme hiển thị thống kê model usage, lịch sử switch, comment gần nhất với link bài viết.
- **Auto reset theo ngày:** Counter và history của worker + recent comments tự reset 00:00 mỗi ngày để tiết kiệm bộ nhớ.
- **Lọc comment thông minh:** Tự động bỏ qua emoji, comment quá ngắn hoặc comment trùng lặp.
- **Lọc reasoning AI:** Tự động loại bỏ phần suy luận tiếng Anh của AI, chỉ giữ câu trả lời tiếng Việt.
- **Ghi log chuyên nghiệp:** Lưu lịch sử comment, reply và trạng thái like vào Google Sheets.
- **Tùy chỉnh Prompt:** Dễ dàng thay đổi "tính cách" của bot qua file `prompts.json`.

---

## 🛠 Cài đặt

### 1. Tải về và cài đặt thư viện
```bash
cd project
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
| `OPENROUTER_API_KEY` | API key từ OpenRouter |
| `OPENROUTER_MODEL_FREE` | Model miễn phí ưu tiên (Mặc định: `openai/gpt-oss-120b:free`) |
| `OPENROUTER_MODEL_PAID` | Model trả phí dự phòng (Mặc định: `deepseek/deepseek-chat`) |
| `MODEL_FALLBACK_COOLDOWN` | Thời gian chờ trước khi thử lại model free, tính bằng phút (Mặc định: `120`) |
| `GOOGLE_SHEETS_CREDENTIALS` | Tên file JSON key (Mặc định: `credentials.json`) |
| `GOOGLE_SHEET_NAME` | Tên file Google Sheet để ghi log |
| `REPLY_DELAY_SECONDS` | Thời gian chờ trước **mỗi** FB API call (react & reply). Rate-limit: 1 action / N giây (Mặc định: `5`) |
| `AUTO_LIKE_ENABLED` | Bật/tắt Auto Like (Mặc định: `true`) |
| `AUTO_LIKE_REACTION_TYPE` | Loại reaction: `LIKE`, `LOVE`, `HAHA`, `WOW`, `SAD`, `ANGRY` (Mặc định: `LIKE`) |
| `RECENT_COMMENTS_LIMIT` | Số comment gần nhất hiển thị trên trang chủ & worker page (Mặc định: `10`) |
| `HOMEPAGE_REFRESH_SECONDS` | Auto-refresh trang chủ (giây, `0` = tắt). Mặc định: `60` |
| `DASHBOARD_REFRESH_SECONDS` | Auto-refresh dashboard (giây, `0` = tắt). Mặc định: `30` |
| `WORKER_REFRESH_SECONDS` | Auto-refresh worker page (giây, `0` = tắt). Mặc định: `3` – tăng lên nếu muốn giảm tải server |

---

## 🔄 Auto-Switch Model (Free ↔ Paid)

Bot tự động quản lý việc chuyển đổi giữa model miễn phí và trả phí:

```
Comment mới → Gọi Model FREE
                 │
                 ├─ OK → Reply thành công ✓
                 │
                 └─ Lỗi 429/402/503 (rate limit)
                         │
                         ▼
                    Gọi Model PAID
                         │
                         ├─ OK → Reply thành công ✓
                         │
                         └─ Fail → Random fallback message ✓ (vẫn reply)
```

### Chu kỳ tự động thử lại

```
[0h] FREE fail → chuyển sang PAID (bắt đầu đếm 2 tiếng)
     Trong 2 tiếng tiếp theo: mọi comment dùng PAID

[2h] ⏰ Cooldown hết → tự động quay về FREE
     FREE OK   → tiếp tục dùng FREE ✓
     FREE fail → lại chuyển sang PAID (đếm 2 tiếng tiếp)

     ↻ Lặp lại vô hạn
```

> [!NOTE]
> **Bot không bao giờ bỏ sót comment.** Nếu cả FREE lẫn PAID đều fail, bot sẽ reply bằng 1 câu ngẫu nhiên từ danh sách ~70 fallback message (cảm ơn + mời follow page) đã cấu hình sẵn trong `openrouter_client.py`.

**Monitoring:** Truy cập `http://localhost:8686/dashboard` để xem thống kê model usage theo ngày và lịch sử switch.

---

## ⚙️ Cơ chế Queue & Worker (chống spam)

Để tránh bị Facebook đánh dấu là bot/spam khi nhận nhiều comment dồn dập, bot dùng kiến trúc **single-worker FIFO queue**:

```
Webhook ──► comment_queue.put(item)  (trả 200 OK ngay, <1s)
                   │
                   ▼
            [ asyncio.Queue ]
                   │
                   ▼
   ┌─── 1 Worker duy nhất (xử lý tuần tự) ───┐
   │                                          │
   │   delay → react                          │
   │   filter                                 │
   │   fetch post                             │
   │   AI generate                            │
   │   delay → reply                          │
   │   log (Sheets + Stats)                   │
   └──────────────────────────────────────────┘
```

### Đặc điểm
- **Tuần tự tuyệt đối:** 1 worker pop 1 item → xử lý xong mới pop tiếp. Không có chuyện 10 comment cùng react/reply một lúc.
- **Delay chung:** `REPLY_DELAY_SECONDS` áp dụng trước **cả react và reply**. Rate effective: tối đa 1 FB API call / N giây.
- **Không miss comment:** queue không giới hạn, lỗi 1 comment không chết worker (comment kế tiếp vẫn được xử lý).
- **Quick-ack webhook:** request từ FB trả 200 OK ngay, xử lý thực tế chạy nền → FB không retry.

### Worker Monitor (`/worker`)

Trang real-time (auto refresh 3s) hiển thị:
- 🟢 Status worker (Running/Stopped) + uptime + số comment đang chờ trong queue
- **Item đang xử lý:** commenter, comment text, stage hiện tại (react/filter/AI/reply/log), elapsed time
- **Counter ngày:** Đang chờ / Tổng nhận / Đã reply / Bỏ qua / Lỗi (tự reset 00:00 mỗi ngày)
- **Lịch sử hôm nay:** N comment gần nhất (theo `RECENT_COMMENTS_LIMIT`) kèm reply, model, link bài viết + link comment

> [!TIP]
> Nếu vẫn bị FB cảnh báo spam, **tăng `REPLY_DELAY_SECONDS` lên 10–15s**. Ví dụ `REPLY_DELAY_SECONDS=10` → tối đa 6 action/phút.

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
Mở bót tại máy local (Cổng mặc định là 8686 hoặc theo cấu hình `.env`):
```bash
python main.py
```

Nếu dùng ngrok để test:
```bash
ngrok http 8686
```

### API Endpoints

| Endpoint | Mô tả |
|---|---|
| `GET /` | 🏠 Trang chủ trực quan – trạng thái bot, cấu hình, uptime, comment gần nhất |
| `GET /dashboard` | 📊 Dashboard – thống kê model usage theo ngày, lịch sử switch |
| `GET /worker` | 🔧 Worker Monitor – real-time item đang xử lý, stage, queue, lịch sử trong ngày |
| `GET /model-status` | Xem trạng thái model (free/paid, thời gian switch, số lần fail) |
| `GET /api/stats` | JSON API – toàn bộ dữ liệu thống kê (daily summary + switch history + comments) |
| `GET /api/worker` | JSON API – snapshot real-time trạng thái worker (current, counters, history) |
| `GET /webhook` | Facebook webhook verification |
| `POST /webhook` | Nhận sự kiện comment từ Facebook |

---

## 💡 Kinh nghiệm & Lưu ý (Troubleshooting)

> [!TIP]
> **Lỗi không nhận Webhook:** Nếu nút "Test" trên Facebook hoạt động nhưng comment thật không chạy, hãy kiểm tra xem bạn đã chạy lệnh `POST me/subscribed_apps` chưa. Đây là bước bắt buộc để Fanpage gửi dữ liệu sang App.

> [!IMPORTANT]
> **Về Token:** Hãy đảm bảo bạn dùng **Page Access Token** (Token của Trang) chứ không phải User Token. Nếu dùng User Token, bạn sẽ gặp lỗi `publish_actions are not available`.

> [!NOTE]
> **Tại sao phải dùng nick khác để test?** Bot được lập trình để tự động bỏ qua các bình luận từ chính Fanpage gửi đi (để tránh vòng lặp vô tận). Hãy dùng một tài khoản cá nhân khác để thử nghiệm.

> [!WARNING]
> **Lỗi 429 (Too Many Requests):** Khi model free bị rate limit, bot sẽ **tự động chuyển sang model paid** và thử lại free sau thời gian cooldown (mặc định 2 tiếng). Nếu cả 2 model đều fail, bot sẽ dùng tin nhắn fallback ngẫu nhiên.

> [!TIP]
> **Hướng dẫn lấy Token vĩnh viễn (Long-lived Page Token):**
> 1. **Bước 1 (Lấy Token 60 ngày):** Vào [Graph API Explorer](https://developers.facebook.com/tools/explorer/), nhấn vào biểu tượng **(i)** cạnh mã Token > **Open in Access Token Tool** > **Extend Access Token**.
> 2. **Bước 2 (Lấy Token vĩnh viễn):** Copy mã 60 ngày quay lại Graph API Explorer, dán vào ô Access Token. Nhập URL: `me/accounts?access_token=LONG_LIVED_TOKEN` > nhấn **Submit (GET)**.
> 3. **Bước 3:** Tìm đúng Fanpage của bạn trong danh sách hiện ra, copy mã `access_token` của Page đó và dán vào file `.env`. (Token này sẽ không bao giờ hết hạn trừ khi bạn đổi mật khẩu Facebook).

---

## 📂 Cấu trúc thư mục
- `main.py`: Server FastAPI – webhook, comment queue + worker, Trang chủ, Dashboard, Worker Monitor, API.
- `config.py`: Quản lý cấu hình và biến môi trường.
- `prompts.json`: Nơi tùy chỉnh nội dung AI trả lời.
- `facebook_client.py`: Các hàm tương tác với Facebook Graph API (reply, like/react, get post, verify signature).
- `openrouter_client.py`: Kết nối OpenRouter API + `ModelManager` (auto-switch free/paid) + lọc reasoning.
- `model_stats.py`: Thống kê model usage + recent comments theo ngày, persist vào `model_stats.json`.
- `worker_state.py`: Tracker real-time trạng thái worker (current item, stage, counter, history) – auto reset theo ngày.
- `sheets_logger.py`: Xử lý ghi log vào Google Sheets.
- `comment_filter.py`: Bộ lọc thông minh cho bình luận (emoji-only, quá ngắn, đã reply, self-Page).
- `test_bot.py`: Bộ test toàn diện (45 test) cho tất cả module.
- `.gitignore`: Bỏ qua các file nhạy cảm: `.env`, `credentials.json`, `model_stats.json`.
