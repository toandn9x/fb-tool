"""
Comprehensive test suite for FB Auto-Reply Bot.
Uses mock data and unittest to verify all modules.
Run: python test_bot.py
"""

import asyncio
import json
import os
import sys
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
os.environ["OPENROUTER_MODEL"] = "meta-llama/llama-3.3-70b-instruct:free"
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
                    model="test-model:free",
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
                    api_key="key", model="m", system_prompt="s", user_message="u"
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
                    api_key="key", model="m", system_prompt="s", user_message="u"
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
        self.assertIn("quản trị", settings.prompts["system_prompt"])


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
        data = response.json()
        self.assertEqual(data["status"], "running")

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
