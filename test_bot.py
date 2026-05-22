"""
Comprehensive test suite for FB Auto-Reply Bot.
Uses mock data and unittest to verify all modules.
Run: python test_bot.py
"""

import asyncio
import json
import os
import sys
import time
import hmac
import hashlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════
# Set up dummy .env BEFORE importing any module
# ═══════════════════════════════════════════════════════════════════════════
os.environ["FB_PAGE_ACCESS_TOKEN"] = "MOCK_PAGE_TOKEN_123"
os.environ["FB_VERIFY_TOKEN"] = "my_secret_verify_token"
os.environ["FB_APP_SECRET"] = "mock_app_secret_abc"
os.environ["OPENROUTER_API_KEY"] = "mock_openrouter_key_xyz"
os.environ["OPENROUTER_MODEL_FREE"] = "meta-llama/llama-3.3-70b-instruct:free"
os.environ["OPENROUTER_MODEL_PAID"] = "deepseek/deepseek-chat"
os.environ["REPLY_DELAY_SECONDS"] = "0"  # No delay for tests
os.environ["GOOGLE_SHEETS_CREDENTIALS"] = "mock_credentials.json"
os.environ["GOOGLE_SHEET_NAME"] = "Test Sheet"


# ═══════════════════════════════════════════════════════════════════════════
# MOCK DATA
# ═══════════════════════════════════════════════════════════════════════════

MOCK_PAGE_ID = "111222333444"

MOCK_WEBHOOK_COMMENT = {
    "object": "page",
    "entry": [
        {
            "id": MOCK_PAGE_ID,
            "time": 1712100000,
            "changes": [
                {
                    "field": "feed",
                    "value": {
                        "item": "comment",
                        "verb": "add",
                        "comment_id": "999_888",
                        "post_id": "999",
                        "message": "Sản phẩm này giá bao nhiêu vậy shop?",
                        "from": {
                            "id": "555666777",
                            "name": "Nguyễn Văn Test",
                        },
                    },
                }
            ],
        }
    ],
}

MOCK_WEBHOOK_EMOJI_COMMENT = {
    "object": "page",
    "entry": [
        {
            "id": MOCK_PAGE_ID,
            "time": 1712100001,
            "changes": [
                {
                    "field": "feed",
                    "value": {
                        "item": "comment",
                        "verb": "add",
                        "comment_id": "999_001",
                        "post_id": "999",
                        "message": "😀👍🔥",
                        "from": {"id": "555666778", "name": "Emoji User"},
                    },
                }
            ],
        }
    ],
}

MOCK_WEBHOOK_PAGE_SELF_COMMENT = {
    "object": "page",
    "entry": [
        {
            "id": MOCK_PAGE_ID,
            "time": 1712100002,
            "changes": [
                {
                    "field": "feed",
                    "value": {
                        "item": "comment",
                        "verb": "add",
                        "comment_id": "999_002",
                        "post_id": "999",
                        "message": "Cảm ơn bạn!",
                        "from": {"id": MOCK_PAGE_ID, "name": "My Page"},
                    },
                }
            ],
        }
    ],
}

MOCK_WEBHOOK_VERIFY_PARAMS = {
    "hub.mode": "subscribe",
    "hub.verify_token": "my_secret_verify_token",
    "hub.challenge": "challenge_12345",
}

MOCK_OPENROUTER_RESPONSE = {
    "choices": [
        {
            "message": {
                "content": "Chào bạn, cảm ơn bạn đã quan tâm đến sản phẩm của shop!"
            }
        }
    ]
}


# ═══════════════════════════════════════════════════════════════════════════
# TEST: comment_filter.py
# ═══════════════════════════════════════════════════════════════════════════
class TestCommentFilter(unittest.TestCase):
    """Test the comment_filter module."""

    def setUp(self):
        from comment_filter import should_reply
        self.should_reply = should_reply

    def test_normal_comment_passes(self):
        ok, reason = self.should_reply(
            comment_text="Sản phẩm này giá bao nhiêu?",
            commenter_id="USER_123",
            page_id=MOCK_PAGE_ID,
            comment_id="c001",
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")

    def test_page_self_comment_rejected(self):
        ok, reason = self.should_reply(
            comment_text="Cảm ơn bạn!",
            commenter_id=MOCK_PAGE_ID,
            page_id=MOCK_PAGE_ID,
            comment_id="c002",
        )
        self.assertFalse(ok)
        self.assertIn("chính Page", reason)

    def test_empty_comment_rejected(self):
        ok, reason = self.should_reply(
            comment_text="",
            commenter_id="USER_123",
            page_id=MOCK_PAGE_ID,
            comment_id="c003",
        )
        self.assertFalse(ok)
        self.assertIn("rỗng", reason)

    def test_short_comment_rejected(self):
        ok, reason = self.should_reply(
            comment_text="a",
            commenter_id="USER_123",
            page_id=MOCK_PAGE_ID,
            comment_id="c004",
        )
        self.assertFalse(ok)
        self.assertIn("ngắn", reason)

    def test_emoji_only_rejected(self):
        ok, reason = self.should_reply(
            comment_text="😀👍🔥",
            commenter_id="USER_123",
            page_id=MOCK_PAGE_ID,
            comment_id="c005",
        )
        self.assertFalse(ok)
        self.assertIn("emoji", reason)

    def test_fast_filter_page_self(self):
        from comment_filter import fast_filter
        ok, reason = fast_filter("Cảm ơn", MOCK_PAGE_ID, MOCK_PAGE_ID)
        self.assertFalse(ok)
        self.assertIn("chính Page", reason)

    def test_fast_filter_emoji_only(self):
        from comment_filter import fast_filter
        ok, reason = fast_filter("😀👍", "USER_1", MOCK_PAGE_ID)
        self.assertFalse(ok)
        self.assertIn("emoji", reason)

    def test_fast_filter_valid_comment(self):
        from comment_filter import fast_filter
        ok, reason = fast_filter("Sản phẩm giá bao nhiêu?", "USER_1", MOCK_PAGE_ID)
        self.assertTrue(ok)

    def test_already_replied_rejected(self):
        mock_sheets = MagicMock()
        mock_sheets.is_already_replied.return_value = True
        ok, reason = self.should_reply(
            comment_text="Hỏi thêm nào",
            commenter_id="USER_123",
            page_id=MOCK_PAGE_ID,
            comment_id="c006",
            sheets_logger=mock_sheets,
        )
        self.assertFalse(ok)
        self.assertIn("reply trước đó", reason)

    def test_whitespace_only_rejected(self):
        ok, reason = self.should_reply(
            comment_text="   ",
            commenter_id="USER_123",
            page_id=MOCK_PAGE_ID,
            comment_id="c007",
        )
        self.assertFalse(ok)

    def test_none_comment_rejected(self):
        ok, reason = self.should_reply(
            comment_text=None,
            commenter_id="USER_123",
            page_id=MOCK_PAGE_ID,
            comment_id="c008",
        )
        self.assertFalse(ok)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: facebook_client.py
# ═══════════════════════════════════════════════════════════════════════════
class TestFacebookClient(unittest.TestCase):
    """Test the facebook_client module."""

    def test_verify_signature_valid(self):
        from facebook_client import verify_signature

        payload = b'{"test": "data"}'
        secret = "test_secret"
        sig = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()

        result = verify_signature(payload, sig, secret)
        self.assertTrue(result)

    def test_verify_signature_invalid(self):
        from facebook_client import verify_signature

        payload = b'{"test": "data"}'
        result = verify_signature(payload, "sha256=invalid_hash", "test_secret")
        self.assertFalse(result)

    def test_verify_signature_skip_when_no_secret(self):
        from facebook_client import verify_signature

        result = verify_signature(b"data", "", "")
        self.assertTrue(result)

    def test_reply_comment_success(self):
        from facebook_client import reply_comment

        async def run():
            with patch("facebook_client.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.raise_for_status = MagicMock()
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                result = await reply_comment("123_456", "Hello!", "TOKEN")
                self.assertTrue(result)
                mock_instance.post.assert_called_once()

        asyncio.run(run())

    def test_get_post_content_success(self):
        from facebook_client import get_post_content

        async def run():
            with patch("facebook_client.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.raise_for_status = MagicMock()
                mock_response.json.return_value = {"message": "Test post content"}
                mock_instance = AsyncMock()
                mock_instance.get.return_value = mock_response
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                content = await get_post_content("999", "TOKEN")
                self.assertEqual(content, "Test post content")

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════════════
# TEST: openrouter_client.py
# ═══════════════════════════════════════════════════════════════════════════
class TestOpenRouterClient(unittest.TestCase):
    """Test the openrouter_client module."""

    def setUp(self):
        from openrouter_client import init_model_manager
        init_model_manager(
            free_model="meta-llama/llama-3.3-70b-instruct:free",
            paid_model="deepseek/deepseek-chat",
            cooldown_minutes=120,
        )

    def test_generate_reply_success(self):
        from openrouter_client import generate_reply

        async def run():
            with patch("openrouter_client.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.raise_for_status = MagicMock()
                mock_response.json.return_value = MOCK_OPENROUTER_RESPONSE
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                reply = await generate_reply(
                    api_key="test_key",
                    system_prompt="You are helpful.",
                    user_message="Hello",
                )
                self.assertIn("cảm ơn", reply.lower())
                mock_instance.post.assert_called_once()

        asyncio.run(run())

    def test_generate_reply_timeout_returns_fallback(self):
        from openrouter_client import generate_reply, FALLBACK_MESSAGE
        import httpx

        async def run():
            with patch("openrouter_client.httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.side_effect = httpx.TimeoutException("timeout")
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                reply = await generate_reply(
                    api_key="key", system_prompt="s", user_message="u"
                )
                self.assertIn(reply, FALLBACK_MESSAGE)

        asyncio.run(run())

    def test_generate_reply_http_error_returns_fallback(self):
        from openrouter_client import generate_reply, FALLBACK_MESSAGE
        import httpx

        async def run():
            with patch("openrouter_client.httpx.AsyncClient") as mock_client:
                mock_response = MagicMock()
                mock_response.status_code = 429
                mock_response.text = "Rate limited"
                error = httpx.HTTPStatusError(
                    "rate limit", request=MagicMock(), response=mock_response
                )
                mock_instance = AsyncMock()
                mock_instance.post.side_effect = error
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=False)
                mock_client.return_value = mock_instance

                reply = await generate_reply(
                    api_key="key", system_prompt="s", user_message="u"
                )
                self.assertIn(reply, FALLBACK_MESSAGE)

        asyncio.run(run())


# ═══════════════════════════════════════════════════════════════════════════
# TEST: sheets_logger.py (mocked gspread)
# ═══════════════════════════════════════════════════════════════════════════
class TestSheetsLogger(unittest.TestCase):
    """Test the sheets_logger module with mocked gspread."""

    @patch("sheets_logger.gspread.service_account")
    def test_init_creates_sheet(self, mock_sa):
        from sheets_logger import SheetsLogger
        import gspread

        mock_gc = MagicMock()
        mock_sa.return_value = mock_gc
        mock_gc.open.side_effect = gspread.SpreadsheetNotFound
        mock_spreadsheet = MagicMock()
        mock_gc.create.return_value = mock_spreadsheet
        mock_worksheet = MagicMock()
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_worksheet.get_all_values.return_value = []

        logger = SheetsLogger("creds.json", "Test Sheet")

        mock_gc.create.assert_called_once_with("Test Sheet")
        mock_worksheet.append_row.assert_called_once()  # headers

    @patch("sheets_logger.gspread.service_account")
    def test_log_comment(self, mock_sa):
        from sheets_logger import SheetsLogger

        mock_gc = MagicMock()
        mock_sa.return_value = mock_gc
        mock_spreadsheet = MagicMock()
        mock_gc.open.return_value = mock_spreadsheet
        mock_worksheet = MagicMock()
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_worksheet.get_all_values.return_value = [["header"]]

        logger = SheetsLogger("creds.json", "Test Sheet")
        logger.log_comment(
            post_id="999",
            comment_id="999_888",
            commenter_name="Test User",
            comment_text="Hello",
            reply_text="Hi there!",
            status="đã reply",
        )

        mock_worksheet.append_row.assert_called_once()
        call_args = mock_worksheet.append_row.call_args
        row = call_args[0][0]
        self.assertEqual(row[1], "999")           # post_id
        self.assertEqual(row[2], "999_888")        # comment_id
        self.assertEqual(row[3], "Test User")      # commenter
        self.assertEqual(row[6], "đã reply")       # status

    @patch("sheets_logger.gspread.service_account")
    def test_is_already_replied_found(self, mock_sa):
        from sheets_logger import SheetsLogger

        mock_gc = MagicMock()
        mock_sa.return_value = mock_gc
        mock_spreadsheet = MagicMock()
        mock_gc.open.return_value = mock_spreadsheet
        mock_worksheet = MagicMock()
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_worksheet.get_all_values.return_value = [["header"]]
        mock_worksheet.find.return_value = MagicMock()  # found

        logger = SheetsLogger("creds.json", "Test Sheet")
        result = logger.is_already_replied("999_888")
        self.assertTrue(result)

    @patch("sheets_logger.gspread.service_account")
    def test_is_already_replied_not_found(self, mock_sa):
        from sheets_logger import SheetsLogger

        mock_gc = MagicMock()
        mock_sa.return_value = mock_gc
        mock_spreadsheet = MagicMock()
        mock_gc.open.return_value = mock_spreadsheet
        mock_worksheet = MagicMock()
        mock_spreadsheet.sheet1 = mock_worksheet
        mock_worksheet.get_all_values.return_value = [["header"]]
        mock_worksheet.find.return_value = None  # Not found

        logger = SheetsLogger("creds.json", "Test Sheet")
        result = logger.is_already_replied("nonexistent")
        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: config.py
# ═══════════════════════════════════════════════════════════════════════════
class TestConfig(unittest.TestCase):
    """Test the config module."""

    def test_settings_loaded(self):
        from config import settings

        self.assertEqual(settings.FB_PAGE_ACCESS_TOKEN, "MOCK_PAGE_TOKEN_123")
        self.assertEqual(settings.FB_VERIFY_TOKEN, "my_secret_verify_token")
        self.assertEqual(settings.OPENROUTER_API_KEY, "mock_openrouter_key_xyz")
        self.assertEqual(settings.REPLY_DELAY_SECONDS, 0)

    def test_prompts_loaded(self):
        from config import settings

        self.assertIn("system_prompt", settings.prompts)
        self.assertIn("reply_instruction", settings.prompts)
        self.assertIn("fanpage", settings.prompts["system_prompt"].lower())


# ═══════════════════════════════════════════════════════════════════════════
# TEST: FastAPI Webhook Endpoints
# ═══════════════════════════════════════════════════════════════════════════
class TestWebhookEndpoints(unittest.TestCase):
    """Test FastAPI webhook endpoints using TestClient."""

    @classmethod
    def setUpClass(cls):
        # Patch sheets logger before importing main
        cls.sheets_patcher = patch("main.SheetsLogger")
        cls.mock_sheets_class = cls.sheets_patcher.start()
        cls.mock_sheets_instance = MagicMock()
        cls.mock_sheets_class.return_value = cls.mock_sheets_instance
        cls.mock_sheets_instance.is_already_replied.return_value = False

        from fastapi.testclient import TestClient
        from main import app
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.sheets_patcher.stop()

    def test_health_check(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        # Trang chủ giờ trả HTML dark-theme
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("FB Auto-Reply Bot", response.text)

    def test_webhook_verify_success(self):
        response = self.client.get("/webhook", params=MOCK_WEBHOOK_VERIFY_PARAMS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "challenge_12345")

    def test_webhook_verify_fail(self):
        params = {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "12345",
        }
        response = self.client.get("/webhook", params=params)
        self.assertEqual(response.status_code, 403)

    @patch("main.process_comment", new=AsyncMock(return_value=None))
    def test_webhook_post_comment(self):
        response = self.client.post("/webhook", json=MOCK_WEBHOOK_COMMENT)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

    def test_webhook_post_non_page_ignored(self):
        response = self.client.post(
            "/webhook", json={"object": "not_a_page", "entry": []}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ignored")

    def test_worker_page_returns_html(self):
        """Trang /worker trả HTML với các counter và stage label."""
        response = self.client.get("/worker")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("Worker Monitor", response.text)
        self.assertIn("Đang chờ", response.text)

    def test_refresh_meta_helper(self):
        """_refresh_meta trả meta tag khi > 0, rỗng khi 0/None (tắt auto-refresh)."""
        from main import _refresh_meta, _refresh_label
        self.assertIn('content="30"', _refresh_meta(30))
        self.assertEqual(_refresh_meta(0), "")
        self.assertEqual(_refresh_meta(None), "")
        self.assertIn("30s", _refresh_label(30))
        self.assertIn("tắt", _refresh_label(0))

    def test_api_worker_returns_json_snapshot(self):
        """/api/worker trả JSON với các field bắt buộc."""
        response = self.client.get("/api/worker")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("worker_running", data)
        self.assertIn("queue_size", data)
        self.assertIn("counters", data)
        self.assertIn("history", data)
        for key in ("enqueued", "completed", "skipped", "failed", "in_queue"):
            self.assertIn(key, data["counters"])


# ═══════════════════════════════════════════════════════════════════════════
# TEST: Comment Queue (xử lý tuần tự + delay chung react & reply)
# ═══════════════════════════════════════════════════════════════════════════
class TestCommentQueue(unittest.TestCase):
    """Verify queue-based sequential processing và delay behavior."""

    def setUp(self):
        # ModelManager cần init vì process_comment/generate_reply phụ thuộc
        from openrouter_client import init_model_manager
        init_model_manager(
            free_model="free-m", paid_model="paid-m", cooldown_minutes=120,
        )

    # ── _fb_delay ───────────────────────────────────────────────────────
    def test_fb_delay_sleeps_when_configured(self):
        """_fb_delay thực sự sleep khi REPLY_DELAY_SECONDS > 0."""
        from main import _fb_delay
        from config import settings as s

        orig = s.REPLY_DELAY_SECONDS
        s.REPLY_DELAY_SECONDS = 0.2
        try:
            async def run():
                t0 = time.monotonic()
                await _fb_delay()
                return time.monotonic() - t0
            elapsed = asyncio.run(run())
            self.assertGreaterEqual(elapsed, 0.18)
        finally:
            s.REPLY_DELAY_SECONDS = orig

    def test_fb_delay_skip_when_zero(self):
        """_fb_delay không sleep khi delay = 0."""
        from main import _fb_delay

        async def run():
            t0 = time.monotonic()
            await _fb_delay()
            return time.monotonic() - t0
        self.assertLess(asyncio.run(run()), 0.05)

    # ── process_comment ─────────────────────────────────────────────────
    def test_process_comment_delays_before_react_and_reply(self):
        """process_comment gọi _fb_delay 2 lần: trước react và trước reply."""
        import main as m
        from config import settings as s

        delay_calls = []

        async def fake_delay():
            delay_calls.append("delay")

        orig_auto = s.AUTO_LIKE_ENABLED
        s.AUTO_LIKE_ENABLED = True
        try:
            with patch("main._fb_delay", side_effect=fake_delay), \
                 patch("main.like_comment", AsyncMock(return_value=True)), \
                 patch("main.reply_comment", AsyncMock(return_value=True)), \
                 patch("main.get_post_content", AsyncMock(return_value="post content")), \
                 patch("main.generate_reply", AsyncMock(return_value="reply text")), \
                 patch("main.should_reply", return_value=(True, "OK")), \
                 patch("main.sheets", None):
                asyncio.run(m.process_comment(
                    page_id=MOCK_PAGE_ID, post_id="999", comment_id="c1",
                    comment_text="câu hỏi", commenter_id="u1",
                    commenter_name="User",
                ))
        finally:
            s.AUTO_LIKE_ENABLED = orig_auto

        self.assertEqual(
            len(delay_calls), 2,
            "delay phải chạy đúng 2 lần (1 trước react, 1 trước reply)",
        )

    def test_process_comment_filtered_only_delays_react(self):
        """Comment bị filter skip → chỉ delay+react, không delay+reply."""
        import main as m
        from config import settings as s

        delay_calls = []

        async def fake_delay():
            delay_calls.append("delay")

        reply_mock = AsyncMock(return_value=True)
        orig_auto = s.AUTO_LIKE_ENABLED
        s.AUTO_LIKE_ENABLED = True
        try:
            with patch("main._fb_delay", side_effect=fake_delay), \
                 patch("main.like_comment", AsyncMock(return_value=True)), \
                 patch("main.reply_comment", reply_mock), \
                 patch("main.should_reply", return_value=(False, "emoji only")), \
                 patch("main.sheets", None):
                asyncio.run(m.process_comment(
                    page_id=MOCK_PAGE_ID, post_id="999", comment_id="c1",
                    comment_text="😀", commenter_id="u1", commenter_name="U",
                ))
        finally:
            s.AUTO_LIKE_ENABLED = orig_auto

        self.assertEqual(len(delay_calls), 1, "chỉ được delay 1 lần (react)")
        reply_mock.assert_not_called()

    def test_process_comment_no_react_when_auto_like_disabled(self):
        """AUTO_LIKE_ENABLED=False → không delay+react, chỉ delay+reply."""
        import main as m
        from config import settings as s

        delay_calls = []

        async def fake_delay():
            delay_calls.append("delay")

        like_mock = AsyncMock(return_value=True)
        orig_auto = s.AUTO_LIKE_ENABLED
        s.AUTO_LIKE_ENABLED = False
        try:
            with patch("main._fb_delay", side_effect=fake_delay), \
                 patch("main.like_comment", like_mock), \
                 patch("main.reply_comment", AsyncMock(return_value=True)), \
                 patch("main.get_post_content", AsyncMock(return_value="")), \
                 patch("main.generate_reply", AsyncMock(return_value="reply")), \
                 patch("main.should_reply", return_value=(True, "OK")), \
                 patch("main.sheets", None):
                asyncio.run(m.process_comment(
                    page_id=MOCK_PAGE_ID, post_id="999", comment_id="c1",
                    comment_text="hỏi gì đó", commenter_id="u1",
                    commenter_name="U",
                ))
        finally:
            s.AUTO_LIKE_ENABLED = orig_auto

        self.assertEqual(len(delay_calls), 1, "chỉ delay 1 lần (reply)")
        like_mock.assert_not_called()

    # ── Worker sequential processing ────────────────────────────────────
    def test_worker_processes_queue_sequentially(self):
        """N comment vào queue cùng lúc → worker xử lý lần lượt, không chồng chéo."""
        import main as m

        processing_log = []

        async def fake_process(**kw):
            processing_log.append(("start", kw["comment_id"]))
            await asyncio.sleep(0.03)
            processing_log.append(("end", kw["comment_id"]))

        async def run():
            m.comment_queue = asyncio.Queue()
            with patch("main.process_comment", side_effect=fake_process):
                worker = asyncio.create_task(m._comment_worker())
                for cid in ["c1", "c2", "c3"]:
                    await m.comment_queue.put({
                        "page_id": MOCK_PAGE_ID, "post_id": "999",
                        "comment_id": cid, "comment_text": "x",
                        "commenter_id": "u", "commenter_name": "U",
                    })
                await m.comment_queue.join()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())

        # Thứ tự phải là: start+end c1, rồi start+end c2, rồi start+end c3
        self.assertEqual(processing_log, [
            ("start", "c1"), ("end", "c1"),
            ("start", "c2"), ("end", "c2"),
            ("start", "c3"), ("end", "c3"),
        ])

    def test_worker_no_miss_all_comments_processed(self):
        """Không miss comment nào – tất cả N item đều được xử lý."""
        import main as m

        processed = []

        async def fake_process(**kw):
            processed.append(kw["comment_id"])

        async def run():
            m.comment_queue = asyncio.Queue()
            with patch("main.process_comment", side_effect=fake_process):
                worker = asyncio.create_task(m._comment_worker())
                ids = [f"c{i}" for i in range(10)]
                for cid in ids:
                    await m.comment_queue.put({
                        "page_id": MOCK_PAGE_ID, "post_id": "999",
                        "comment_id": cid, "comment_text": "x",
                        "commenter_id": "u", "commenter_name": "U",
                    })
                await m.comment_queue.join()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
                return ids

        expected_ids = asyncio.run(run())
        self.assertEqual(processed, expected_ids)

    def test_worker_continues_after_error(self):
        """1 comment ném exception → worker vẫn xử lý comment tiếp theo."""
        import main as m

        processed = []

        async def fake_process(**kw):
            if kw["comment_id"] == "c_bad":
                raise RuntimeError("simulated failure")
            processed.append(kw["comment_id"])

        async def run():
            m.comment_queue = asyncio.Queue()
            with patch("main.process_comment", side_effect=fake_process):
                worker = asyncio.create_task(m._comment_worker())
                for cid in ["c1", "c_bad", "c3"]:
                    await m.comment_queue.put({
                        "page_id": MOCK_PAGE_ID, "post_id": "999",
                        "comment_id": cid, "comment_text": "x",
                        "commenter_id": "u", "commenter_name": "U",
                    })
                await m.comment_queue.join()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())
        self.assertEqual(
            processed, ["c1", "c3"],
            "c1 và c3 phải được xử lý dù c_bad ném lỗi",
        )

    # ── Webhook → queue integration ─────────────────────────────────────
    def test_webhook_enqueues_not_executes_directly(self):
        """POST webhook → item vào queue (xử lý async, request trả về ngay)."""
        from fastapi.testclient import TestClient
        import main as m

        call_args = []

        async def slow_process(**kw):
            call_args.append(kw)
            await asyncio.sleep(0.05)

        # Mock queue_db để test không phụ thuộc SQLite thực tế (tránh nạp
        # lại item cũ từ DB khi lifespan chạy load_unfinished).
        with patch("main.SheetsLogger"), \
             patch("main.process_comment", side_effect=slow_process), \
             patch("main.queue_db.init_db"), \
             patch("main.queue_db.cleanup_old_done"), \
             patch("main.queue_db.load_unfinished", return_value=[]), \
             patch("main.queue_db.save_pending", return_value=True), \
             patch("main.queue_db.save_filtered", return_value=True), \
             patch("main.queue_db.mark_processing"), \
             patch("main.queue_db.mark_done"):
            with TestClient(m.app) as client:
                response = client.post("/webhook", json=MOCK_WEBHOOK_COMMENT)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "ok")
                # Đợi worker tiêu thụ
                time.sleep(0.2)

        self.assertEqual(len(call_args), 1)
        self.assertEqual(call_args[0]["comment_id"], "999_888")
        self.assertEqual(
            call_args[0]["comment_text"],
            "Sản phẩm này giá bao nhiêu vậy shop?",
        )

    def test_webhook_emoji_bypasses_queue(self):
        """Comment chỉ có emoji: fast-filter → KHÔNG enqueue, chỉ like."""
        from fastapi.testclient import TestClient
        import main as m

        process_calls = []
        like_calls = []

        async def fake_process(**kw):
            process_calls.append(kw)

        async def fake_like(**kw):
            like_calls.append(kw)
            return True

        fake_ws = MagicMock()

        with patch("main.SheetsLogger"), \
             patch("main.process_comment", side_effect=fake_process), \
             patch("main.like_comment", side_effect=fake_like), \
             patch("main.worker_state", fake_ws), \
             patch("main.queue_db.init_db"), \
             patch("main.queue_db.cleanup_old_done"), \
             patch("main.queue_db.load_unfinished", return_value=[]), \
             patch("main.queue_db.save_pending") as mock_save_pending, \
             patch("main.queue_db.save_filtered", return_value=True) as mock_save_filtered, \
             patch("main.queue_db.mark_processing"), \
             patch("main.queue_db.mark_done"):
            with TestClient(m.app) as client:
                response = client.post(
                    "/webhook", json=MOCK_WEBHOOK_EMOJI_COMMENT
                )
                self.assertEqual(response.status_code, 200)
                time.sleep(0.15)  # đợi fire-and-forget

        # process_comment KHÔNG được gọi (bypass queue)
        self.assertEqual(len(process_calls), 0)
        # save_pending KHÔNG được gọi, save_filtered mới được gọi
        mock_save_pending.assert_not_called()
        mock_save_filtered.assert_called_once()
        # like_comment ĐƯỢC gọi (fast-path vẫn like)
        self.assertEqual(len(like_calls), 1)
        # worker_state.record_skipped_fast KHÔNG được gọi – fast-filter là
        # path riêng, không tính vào counter/history của worker monitor
        fake_ws.record_skipped_fast.assert_not_called()
        # worker_state.on_enqueue KHÔNG được gọi (không vào queue)
        fake_ws.on_enqueue.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# TEST: worker_state tracking
# ═══════════════════════════════════════════════════════════════════════════
class TestWorkerState(unittest.TestCase):
    """Verify WorkerState track đúng các counter và stage."""

    def setUp(self):
        from worker_state import WorkerState
        self.ws = WorkerState()

    def _sample_item(self, cid="c1"):
        return {
            "comment_id": cid, "commenter_name": "User",
            "comment_text": "hello", "post_id": "999",
        }

    def test_full_lifecycle_completed(self):
        self.ws.on_worker_start()
        self.ws.on_enqueue(self._sample_item())
        self.ws.on_start_processing(self._sample_item())
        self.ws.set_stage("replying")
        self.assertEqual(self.ws.current_stage, "replying")
        self.assertIsNotNone(self.ws.current_item)
        self.ws.on_complete("đã reply")

        self.assertEqual(self.ws.total_enqueued, 1)
        self.assertEqual(self.ws.total_completed, 1)
        self.assertEqual(self.ws.total_skipped, 0)
        self.assertEqual(self.ws.total_failed, 0)
        self.assertIsNone(self.ws.current_item)
        self.assertEqual(self.ws.current_stage, "idle")
        self.assertEqual(len(self.ws.history), 1)
        self.assertEqual(self.ws.history[0]["status"], "đã reply")

    def test_skipped_counter(self):
        self.ws.on_start_processing(self._sample_item())
        self.ws.on_complete("bỏ qua – emoji")
        self.assertEqual(self.ws.total_skipped, 1)
        self.assertEqual(self.ws.total_completed, 0)

    def test_failed_counter(self):
        self.ws.on_start_processing(self._sample_item())
        self.ws.on_complete("lỗi – timeout")
        self.assertEqual(self.ws.total_failed, 1)
        self.assertEqual(self.ws.total_completed, 0)

    def test_snapshot_includes_queue_size_and_current(self):
        self.ws.on_worker_start()
        self.ws.on_start_processing(self._sample_item("c42"))
        self.ws.set_stage("generating_ai")

        snap = self.ws.snapshot(queue_size=5)
        self.assertTrue(snap["worker_running"])
        self.assertEqual(snap["queue_size"], 5)
        self.assertEqual(snap["counters"]["in_queue"], 5)
        self.assertIsNotNone(snap["current"])
        self.assertEqual(snap["current"]["comment_id"], "c42")
        self.assertEqual(snap["current"]["stage"], "generating_ai")
        # Label phải được resolve từ STAGES dict
        self.assertIn("AI", snap["current"]["stage_label"])

    def test_history_capped(self):
        """History không vượt MAX_HISTORY."""
        from worker_state import MAX_HISTORY
        for i in range(MAX_HISTORY + 10):
            self.ws.on_start_processing(self._sample_item(f"c{i}"))
            self.ws.on_complete("đã reply")
        self.assertEqual(len(self.ws.history), MAX_HISTORY)

    def test_day_rollover_resets_history_and_counters(self):
        """Sang ngày mới → history + counter tự reset về 0 (tiết kiệm bộ nhớ)."""
        # Ghi vài item ở "hôm qua"
        self.ws.on_enqueue(self._sample_item("c_yesterday"))
        self.ws.on_start_processing(self._sample_item("c_yesterday"))
        self.ws.on_complete("đã reply", reply_text="xin chào", model_used="m1")
        self.assertEqual(self.ws.total_completed, 1)
        self.assertEqual(len(self.ws.history), 1)

        # Giả lập sang ngày mới bằng cách đổi _history_date
        self.ws._history_date = "1970-01-01"

        # Bất kỳ entry point nào cũng trigger rollover
        self.ws.on_enqueue(self._sample_item("c_today"))

        self.assertEqual(self.ws.total_enqueued, 1, "counter phải reset trước khi +1")
        self.assertEqual(self.ws.total_completed, 0)
        self.assertEqual(self.ws.total_skipped, 0)
        self.assertEqual(self.ws.total_failed, 0)
        self.assertEqual(len(self.ws.history), 0)
        self.assertEqual(self.ws._history_date, self.ws._today())

    def test_snapshot_triggers_rollover(self):
        """Chỉ call snapshot (không có enqueue/complete) cũng phải rollover."""
        self.ws.on_start_processing(self._sample_item("c1"))
        self.ws.on_complete("đã reply")
        self.assertEqual(len(self.ws.history), 1)

        self.ws._history_date = "1970-01-01"
        snap = self.ws.snapshot(queue_size=0)
        self.assertEqual(snap["counters"]["completed"], 0)
        self.assertEqual(len(snap["history"]), 0)

    def test_snapshot_throughput_and_eta(self):
        """Snapshot trả throughput (avg, per_minute, eta) dựa trên history."""
        # Inject history có elapsed_sec cố định
        for i in range(5):
            self.ws.on_start_processing(self._sample_item(f"c{i}"))
            self.ws.on_complete("đã reply")
        # Override elapsed_sec để test toán chính xác
        for h in self.ws.history:
            h["elapsed_sec"] = 60.0

        snap = self.ws.snapshot(queue_size=10)
        tp = snap["throughput"]
        self.assertEqual(tp["avg_elapsed_sec"], 60.0)
        self.assertEqual(tp["per_minute"], 1.0)           # 60/60 = 1
        # remaining = queue 10 + in_flight 0, eta = 10*60 = 600s
        self.assertEqual(tp["remaining"], 10)
        self.assertEqual(tp["eta_seconds"], 600)

    def test_snapshot_throughput_empty_history(self):
        """History rỗng → avg = 0, eta = 0, per_minute = 0 (không lỗi chia 0)."""
        snap = self.ws.snapshot(queue_size=5)
        tp = snap["throughput"]
        self.assertEqual(tp["avg_elapsed_sec"], 0.0)
        self.assertEqual(tp["per_minute"], 0.0)
        self.assertEqual(tp["eta_seconds"], 0)

    def test_stage_elapsed_resets_on_set_stage(self):
        """set_stage reset stage timer → stage_elapsed phản ánh đúng stage hiện tại."""
        self.ws.on_start_processing(self._sample_item("c1"))
        time.sleep(0.05)
        self.ws.set_stage("generating_ai")  # reset stage timer

        snap = self.ws.snapshot(queue_size=0)
        # total elapsed ≥ 0.05s, stage elapsed < total (vì vừa reset)
        self.assertGreaterEqual(snap["current"]["elapsed_sec"], 0.05)
        self.assertLess(
            snap["current"]["stage_elapsed_sec"],
            snap["current"]["elapsed_sec"],
        )

    def test_record_skipped_fast_adds_history_without_touching_current(self):
        """Fast-path skip: tăng counter + history, KHÔNG ghi đè current_item."""
        # Worker đang xử lý c_running (in-flight)
        self.ws.on_start_processing(self._sample_item("c_running"))
        self.assertIsNotNone(self.ws.current_item)

        # Parallel: fast-filter skip 1 comment khác
        fast_item = {
            "comment_id": "c_fast",
            "commenter_name": "Emoji User",
            "comment_text": "😀😀",
            "post_id": "999",
        }
        self.ws.record_skipped_fast(fast_item, "comment chỉ có emoji", "đã LIKE")

        # current_item phải còn nguyên (worker chưa xong c_running)
        self.assertEqual(self.ws.current_item["comment_id"], "c_running")
        # History có entry fast skip
        self.assertEqual(len(self.ws.history), 1)
        self.assertEqual(self.ws.history[0]["comment_id"], "c_fast")
        self.assertIn("emoji", self.ws.history[0]["status"])
        self.assertEqual(self.ws.history[0]["elapsed_sec"], 0)
        # Counter skipped tăng
        self.assertEqual(self.ws.total_skipped, 1)

    def test_sheets_crash_does_not_break_reply_flow(self):
        """Sheets ném exception ở dedup + log → reply vẫn thành công."""
        import main as m
        from config import settings as s

        sheets_mock = MagicMock()
        # is_already_replied ném → bị swallow → trả False (coi như chưa reply)
        sheets_mock.is_already_replied.side_effect = RuntimeError("Sheets API down")
        # log_comment ném → bị swallow
        sheets_mock.log_comment.side_effect = RuntimeError("Sheets write fail")

        reply_mock = AsyncMock(return_value=True)
        orig_auto = s.AUTO_LIKE_ENABLED
        s.AUTO_LIKE_ENABLED = False  # giảm bớt mock

        try:
            with patch("main._fb_delay", AsyncMock(return_value=None)), \
                 patch("main.like_comment", AsyncMock(return_value=True)), \
                 patch("main.reply_comment", reply_mock), \
                 patch("main.get_post_content", AsyncMock(return_value="post")), \
                 patch("main.generate_reply", AsyncMock(return_value="câu reply")), \
                 patch("main.queue_db.is_replied", return_value=False), \
                 patch("main.queue_db.mark_replied"), \
                 patch("main.sheets", sheets_mock):
                asyncio.run(m.process_comment(
                    page_id=MOCK_PAGE_ID, post_id="999", comment_id="c1",
                    comment_text="câu hỏi dài đủ ý", commenter_id="u1",
                    commenter_name="U",
                ))
        finally:
            s.AUTO_LIKE_ENABLED = orig_auto

        # Reply vẫn được gọi → bot vẫn hoạt động khi Sheets sập
        reply_mock.assert_called_once()
        # Sheets được thử gọi nhưng bị exception (được swallow)
        sheets_mock.is_already_replied.assert_called_once()

    def test_sheets_timeout_falls_back_to_false(self):
        """Sheets treo → asyncio.TimeoutError → _sheets_is_replied trả False."""
        import main as m

        async def raise_timeout(coro, *args, **kwargs):
            coro.close()  # dọn inner coroutine để tránh RuntimeWarning
            raise asyncio.TimeoutError()

        sheets_mock = MagicMock()
        with patch("main.sheets", sheets_mock), \
             patch("main.asyncio.wait_for", side_effect=raise_timeout):
            result = asyncio.run(m._sheets_is_replied("c1"))

        # TimeoutError bị catch, trả False → worker tiếp tục không treo
        self.assertFalse(result)

    def test_sheets_log_timeout_does_not_raise(self):
        """_sheets_log timeout → nuốt exception, worker không crash."""
        import main as m

        async def raise_timeout(coro, *args, **kwargs):
            coro.close()  # dọn inner coroutine để tránh RuntimeWarning
            raise asyncio.TimeoutError()

        sheets_mock = MagicMock()
        with patch("main.sheets", sheets_mock), \
             patch("main.asyncio.wait_for", side_effect=raise_timeout):
            # Không ném exception dù wait_for timeout
            asyncio.run(m._sheets_log(
                post_id="p", comment_id="c", commenter_name="N",
                comment_text="t", reply_text="r", status="s", like_status="",
            ))

    def test_post_content_cache_ttl(self):
        """_get_post_cached chỉ fetch 1 lần trong TTL window."""
        import main as m

        fetch_calls = []

        async def fake_fetch(post_id, token):
            fetch_calls.append(post_id)
            return f"content of {post_id}"

        # Clear cache trước test
        m._post_cache.clear()
        with patch("main.get_post_content", side_effect=fake_fetch):
            async def run():
                a = await m._get_post_cached("P1")
                b = await m._get_post_cached("P1")  # cache hit
                c = await m._get_post_cached("P2")  # miss
                d = await m._get_post_cached("P1")  # cache hit
                return a, b, c, d

            a, b, c, d = asyncio.run(run())

        # Chỉ 2 fetch thực tế (P1 1 lần, P2 1 lần)
        self.assertEqual(fetch_calls, ["P1", "P2"])
        self.assertEqual(a, "content of P1")
        self.assertEqual(b, "content of P1")
        self.assertEqual(c, "content of P2")

    def test_post_content_cache_expires(self):
        """Cache entry quá TTL → refetch."""
        import main as m

        fetch_calls = []

        async def fake_fetch(post_id, token):
            fetch_calls.append(post_id)
            return "v1" if len(fetch_calls) == 1 else "v2"

        m._post_cache.clear()
        orig_ttl = m._POST_CACHE_TTL
        m._POST_CACHE_TTL = 0.05  # 50ms
        try:
            with patch("main.get_post_content", side_effect=fake_fetch):
                async def run():
                    a = await m._get_post_cached("P1")
                    await asyncio.sleep(0.08)  # hết TTL
                    b = await m._get_post_cached("P1")
                    return a, b
                a, b = asyncio.run(run())
        finally:
            m._POST_CACHE_TTL = orig_ttl

        self.assertEqual(len(fetch_calls), 2)
        self.assertEqual(a, "v1")
        self.assertEqual(b, "v2")

    def test_counters_include_in_flight_and_total_done(self):
        """Counters có thêm in_flight và total_done."""
        self.ws.on_start_processing(self._sample_item("c1"))
        self.ws.on_complete("đã reply")
        self.ws.on_start_processing(self._sample_item("c2"))
        self.ws.on_complete("bỏ qua – emoji")
        self.ws.on_start_processing(self._sample_item("c3"))  # còn đang xử lý

        snap = self.ws.snapshot(queue_size=0)
        self.assertEqual(snap["counters"]["in_flight"], 1)
        self.assertEqual(snap["counters"]["total_done"], 2)  # completed + skipped


# ═══════════════════════════════════════════════════════════════════════════
# TEST: queue_db dedup local (mark_replied / is_replied)
# ═══════════════════════════════════════════════════════════════════════════
class TestQueueDbDedup(unittest.TestCase):
    """Verify SQLite local dedup – thay cho Sheets full-scan chậm."""

    def setUp(self):
        import tempfile
        import queue_db as qdb
        # Dùng DB riêng cho từng test, không đụng file prod
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self._orig_path = qdb.DB_PATH
        qdb.DB_PATH = self._tmp.name
        qdb.init_db()
        self.qdb = qdb

    def tearDown(self):
        self.qdb.DB_PATH = self._orig_path
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def _item(self, cid="c1"):
        return {
            "comment_id": cid, "page_id": "P", "post_id": "POST1",
            "comment_text": "hi", "commenter_id": "U", "commenter_name": "N",
        }

    def test_mark_replied_and_is_replied(self):
        self.qdb.save_pending(self._item("c1"))
        self.assertFalse(self.qdb.is_replied("c1"))

        self.qdb.mark_replied("c1")
        self.assertTrue(self.qdb.is_replied("c1"))

    def test_is_replied_nonexistent(self):
        self.assertFalse(self.qdb.is_replied("not_saved"))

    def test_save_filtered_dedup(self):
        """save_filtered: INSERT OR IGNORE – duplicate webhook → False."""
        first = self.qdb.save_filtered(self._item("c2"))
        second = self.qdb.save_filtered(self._item("c2"))
        self.assertTrue(first)
        self.assertFalse(second)


# ═══════════════════════════════════════════════════════════════════════════
# TEST: model_stats debounced save
# ═══════════════════════════════════════════════════════════════════════════
class TestModelStatsDebounce(unittest.TestCase):
    """Debounce giảm IO: N updates nhanh → 1 write."""

    def setUp(self):
        import tempfile
        import model_stats as ms
        self._tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmp.close()
        self._orig = ms.STATS_FILE
        ms.STATS_FILE = self._tmp.name
        self.ms = ms
        self.s = ms.ModelStats()

    def tearDown(self):
        self.ms.STATS_FILE = self._orig
        try:
            os.unlink(self._tmp.name)
        except Exception:
            pass

    def test_rapid_updates_trigger_at_most_one_write(self):
        """10 updates trong <debounce → chỉ 1 write thực tế (hoặc 0 nếu lần đầu trong window)."""
        write_count = [0]
        orig_write = self.s._write_file

        def counted_write():
            write_count[0] += 1
            orig_write()

        self.s._write_file = counted_write
        # Đánh dấu đã vừa save (để các _save tiếp theo bị debounce)
        self.s._last_save_time = time.time()

        for _ in range(10):
            self.s.record_free_success()

        # Tất cả 10 bị debounce, không write lần nào. Dirty flag = True.
        self.assertEqual(write_count[0], 0)
        self.assertTrue(self.s._dirty)

    def test_flush_writes_if_dirty(self):
        """flush() ghi nếu có dirty update chờ."""
        write_count = [0]
        orig_write = self.s._write_file

        def counted_write():
            write_count[0] += 1
            orig_write()

        self.s._write_file = counted_write
        self.s._last_save_time = time.time()

        self.s.record_free_success()  # dirty=True
        self.s.flush()
        self.assertEqual(write_count[0], 1)
        self.assertFalse(self.s._dirty)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN: Run tests and generate report
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Run with verbosity
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestCommentFilter))
    suite.addTests(loader.loadTestsFromTestCase(TestFacebookClient))
    suite.addTests(loader.loadTestsFromTestCase(TestOpenRouterClient))
    suite.addTests(loader.loadTestsFromTestCase(TestConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestSheetsLogger))
    suite.addTests(loader.loadTestsFromTestCase(TestWebhookEndpoints))
    suite.addTests(loader.loadTestsFromTestCase(TestCommentQueue))
    suite.addTests(loader.loadTestsFromTestCase(TestWorkerState))
    suite.addTests(loader.loadTestsFromTestCase(TestQueueDbDedup))
    suite.addTests(loader.loadTestsFromTestCase(TestModelStatsDebounce))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"  Tests run   : {result.testsRun}")
    print(f"  Passed      : {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  Failures    : {len(result.failures)}")
    print(f"  Errors      : {len(result.errors)}")
    print("=" * 60)

    if result.failures:
        print("\nFAILURES:")
        for test, trace in result.failures:
            print(f"\n  [FAIL] {test}")
            print(f"     {trace.strip().split(chr(10))[-1]}")

    if result.errors:
        print("\nERRORS:")
        for test, trace in result.errors:
            print(f"\n  [ERROR] {test}")
            print(f"     {trace.strip().split(chr(10))[-1]}")

    if not result.failures and not result.errors:
        print("\nALL TESTS PASSED!")
    
    sys.exit(0 if result.wasSuccessful() else 1)
