"""
Google Sheets logger – log comments and replies to a Google Sheet.
"""

import logging
from datetime import datetime

import gspread

logger = logging.getLogger(__name__)

SHEET_HEADERS = [
    "Thời gian",
    "Post ID",
    "Comment ID",
    "Người bình luận",
    "Nội dung comment",
    "Nội dung reply",
    "Trạng thái",
    "Like/React",
]


class SheetsLogger:
    """Manages Google Sheets logging for the bot."""

    def __init__(self, credentials_path: str, sheet_name: str):
        """
        Initialize the Sheets logger.

        Args:
            credentials_path: Path to Google service account JSON key file
            sheet_name: Name of the Google Sheet to use
        """
        self.sheet_name = sheet_name
        self._worksheet = None

        try:
            self._gc = gspread.service_account(filename=credentials_path)
            self._ensure_sheet_exists()
            logger.info(f"Google Sheets logger initialized: '{sheet_name}'")
        except Exception as e:
            logger.error(f"Failed to init Google Sheets: {e}")
            self._gc = None

    def _ensure_sheet_exists(self):
        """Open or create the sheet, ensure headers exist."""
        try:
            spreadsheet = self._gc.open(self.sheet_name)
        except gspread.SpreadsheetNotFound:
            spreadsheet = self._gc.create(self.sheet_name)
            logger.info(f"Created new spreadsheet: '{self.sheet_name}'")

        self._worksheet = spreadsheet.sheet1

        # Add headers if sheet is empty
        if not self._worksheet.get_all_values():
            self._worksheet.append_row(SHEET_HEADERS)
            logger.info("Added headers to sheet")

    def log_comment(
        self,
        post_id: str,
        comment_id: str,
        commenter_name: str,
        comment_text: str,
        reply_text: str,
        status: str,
        like_status: str = "",
    ):
        """
        Log a comment and its reply to Google Sheets.

        Args:
            post_id: Facebook post ID
            comment_id: Facebook comment ID
            commenter_name: Name of the commenter
            comment_text: Original comment text
            reply_text: Bot's reply text (empty if skipped)
            status: 'đã reply' | 'bỏ qua' | 'lỗi'
            like_status: 'đã LIKE' | 'đã LOVE' | 'lỗi LIKE' | ''
        """
        if not self._worksheet:
            logger.warning("Sheets not available, skipping log")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            now,
            post_id,
            comment_id,
            commenter_name,
            comment_text,
            reply_text,
            status,
            like_status,
        ]

        try:
            self._worksheet.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"Logged comment {comment_id} to sheet ({status})")
        except Exception as e:
            logger.error(f"Failed to log to sheet: {e}")

    def is_already_replied(self, comment_id: str) -> bool:
        """
        Check if a comment has already been processed.

        Args:
            comment_id: Facebook comment ID to check

        Returns:
            True if comment already exists in the sheet.
        """
        if not self._worksheet:
            return False

        try:
            # Search in the Comment ID column (column 3)
            # gspread find() returns None if not found
            cell = self._worksheet.find(comment_id, in_column=3)
            return cell is not None
        except Exception as e:
            logger.error(f"Error checking sheet: {e}")
            return False
