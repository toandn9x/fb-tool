"""
Configuration module – loads settings from .env file.
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    def __init__(self):
        # Facebook
        self.FB_PAGE_ACCESS_TOKEN = self._require("FB_PAGE_ACCESS_TOKEN")
        self.FB_VERIFY_TOKEN = self._require("FB_VERIFY_TOKEN")
        self.FB_APP_SECRET = os.getenv("FB_APP_SECRET", "")

        # OpenRouter
        self.OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
        self.OPENROUTER_MODEL_FREE = os.getenv(
            "OPENROUTER_MODEL_FREE", "openai/gpt-oss-120b:free"
        )
        self.OPENROUTER_MODEL_PAID = os.getenv(
            "OPENROUTER_MODEL_PAID", "deepseek/deepseek-chat"
        )
        # Thời gian chờ trước khi thử lại model free (phút)
        self.MODEL_FALLBACK_COOLDOWN = int(os.getenv(
            "MODEL_FALLBACK_COOLDOWN", "120"
        ))

        # Google Sheets
        self.GOOGLE_SHEETS_CREDENTIALS = os.getenv(
            "GOOGLE_SHEETS_CREDENTIALS", "credentials.json"
        )
        self.GOOGLE_SHEET_NAME = os.getenv(
            "GOOGLE_SHEET_NAME", "FB Auto Reply Log"
        )

        # Bot settings
        self.REPLY_DELAY_SECONDS = int(os.getenv("REPLY_DELAY_SECONDS", "5"))
        self.SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

        # Auto Like/React
        self.AUTO_LIKE_ENABLED = os.getenv(
            "AUTO_LIKE_ENABLED", "true"
        ).lower() in ("true", "1", "yes")
        self.AUTO_LIKE_REACTION_TYPE = os.getenv(
            "AUTO_LIKE_REACTION_TYPE", "LIKE"
        ).upper()

        # Prompts
        self.prompts = self._load_prompts()

    def _require(self, key: str) -> str:
        """Get a required environment variable or raise an error."""
        value = os.getenv(key)
        if not value:
            raise ValueError(f"Missing required env variable: {key}")
        return value

    def _load_prompts(self) -> dict:
        """Load prompt templates from prompts.json."""
        prompts_path = os.path.join(os.path.dirname(__file__), "prompts.json")
        try:
            with open(prompts_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {
                "system_prompt": "Bạn là quản trị 1 page truyền thông. Hãy trả lời ngắn gọn 1-2 câu nếu thực sự nắm chắc chắn câu trả lời, còn không thì hãy trả lời ngẫu nhiên 1 trong các câu sau: 'Cảm ơn bạn đã quan tâm, follow page để theo dõi các bài viết hấp dẫn tiếp theo nhé.', 'Cảm ơn bạn đã theo dõi. Tiếp tục đồng hành cùng CSAV bạn nhé.'",
                "reply_instruction": "Bình luận: {comment_text}\nNgười bình luận: {commenter_name}",
                "max_tokens": 150,
            }


# Singleton instance
settings = Settings()
