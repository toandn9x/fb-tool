"""
Comment filter – decide whether a comment should be replied to.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Regex: string that contains ONLY emojis, whitespace, or common reaction chars
EMOJI_ONLY_PATTERN = re.compile(
    r"^[\s\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
    r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
    r"\U00002702-\U000027B0\U0000FE00-\U0000FE0F"
    r"\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F"
    r"\U00002600-\U000026FF\U0000200D\U00002764"
    r"\U0000FE0F\U0000203C-\U00003299"
    r"❤️💙💚💛🧡💜🖤🤍💔👍👎😀😂🥰😍🤔👏🔥💯✅❌🎉]+$"
)

MIN_COMMENT_LENGTH = 2


def should_reply(
    comment_text: str,
    commenter_id: str,
    page_id: str,
    comment_id: str,
    sheets_logger=None,
) -> tuple[bool, str]:
    """
    Determine if the bot should reply to this comment.

    Args:
        comment_text: The comment message text
        commenter_id: ID of the person who commented
        page_id: The Page's own ID
        comment_id: The comment ID
        sheets_logger: SheetsLogger instance to check duplicates

    Returns:
        Tuple of (should_reply: bool, reason: str)
    """
    # 1. Skip comments from the Page itself (avoid infinite loop)
    if commenter_id == page_id:
        return False, "comment từ chính Page"

    # 2. Skip empty or missing text
    if not comment_text or not comment_text.strip():
        return False, "comment rỗng hoặc chỉ có media"

    text = comment_text.strip()

    # 3. Skip very short comments
    if len(text) < MIN_COMMENT_LENGTH:
        return False, f"comment quá ngắn ({len(text)} ký tự)"

    # 4. Skip emoji-only comments
    if EMOJI_ONLY_PATTERN.match(text):
        return False, "comment chỉ có emoji"

    # 5. Skip already-replied comments (check Google Sheets)
    if sheets_logger and sheets_logger.is_already_replied(comment_id):
        return False, "đã reply trước đó"

    return True, "OK"
