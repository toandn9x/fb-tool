# Facebook Fanpage Auto-Reply Bot – Plan

## Mô tả
Bot Python tự động trả lời comment trên fanpage Facebook.
- **Facebook Graph API** (webhook) → nhận comment mới
- **OpenRouter** → gọi AI miễn phí sinh câu trả lời
- **Google Sheets** → lưu log comment đã đọc / đã reply

## Cấu trúc Project

```
fb-tool/
├── main.py                 # FastAPI server – webhook endpoint
├── config.py               # Đọc config từ .env
├── prompts.json            # Mẫu prompt gửi lên AI
├── openrouter_client.py    # Gọi OpenRouter API
├── facebook_client.py      # Reply comment qua Graph API
├── sheets_logger.py        # Ghi log vào Google Sheets
├── comment_filter.py       # Lọc comment trước khi reply
├── .env.example            # Mẫu biến môi trường
├── credentials.json        # (user tự thêm) Google service account key
├── requirements.txt
└── README.md
```

## Flow xử lý

1. Facebook gửi webhook khi có comment mới
2. Server parse payload → lấy comment_id, text, post_id
3. **Filter**: bỏ qua comment của Page, emoji/sticker, đã reply
4. **Delay**: chờ `REPLY_DELAY_SECONDS` giây
5. **AI**: gọi OpenRouter với prompt template → sinh câu trả lời
6. **Reply**: gửi reply qua Graph API
7. **Log**: ghi vào Google Sheets

## Yêu cầu setup

1. Tạo Facebook App → cấu hình Webhooks
2. Lấy Page Access Token (quyền: `pages_manage_metadata`, `pages_read_engagement`, `pages_manage_engagement`)
3. Tạo OpenRouter API key tại https://openrouter.ai/keys
4. Tạo Google Cloud Service Account → bật Sheets API & Drive API → tải JSON key
5. Share Google Sheet cho email service account
6. Copy `.env.example` → `.env` → điền thông tin
7. Chạy: `pip install -r requirements.txt && uvicorn main:app --port 8000`
8. Dùng ngrok để expose localhost ra internet
