"""
Model Stats Tracker – ghi log thống kê model usage theo ngày.
Lưu vào file JSON để persist qua restart.
"""

import json
import logging
import os
from datetime import datetime, date
from collections import defaultdict

logger = logging.getLogger(__name__)

STATS_FILE = os.path.join(os.path.dirname(__file__), "model_stats.json")


class ModelStats:
    """Theo dõi và thống kê việc sử dụng model theo ngày."""

    def __init__(self):
        # Thống kê theo ngày: {"2026-04-14": {"free_success": 5, ...}}
        self.daily: dict[str, dict] = {}
        # Lịch sử switch events (giữ tối đa 200 events gần nhất)
        self.switch_history: list[dict] = []
        # 10 comment gần nhất (tự reset theo ngày)
        self._recent_comments: list[dict] = []
        self._recent_comments_date: str = ""
        # Load từ file nếu có
        self._load()

    def _today(self) -> str:
        return date.today().isoformat()

    def _ensure_day(self, day: str):
        if day not in self.daily:
            self.daily[day] = {
                "free_success": 0,
                "free_fail": 0,
                "paid_success": 0,
                "paid_fail": 0,
                "fallback_used": 0,
                "switch_to_paid": 0,
                "switch_to_free": 0,
                "total_replies": 0,
            }

    def record_free_success(self):
        day = self._today()
        self._ensure_day(day)
        self.daily[day]["free_success"] += 1
        self.daily[day]["total_replies"] += 1
        self._save()

    def record_free_fail(self):
        day = self._today()
        self._ensure_day(day)
        self.daily[day]["free_fail"] += 1
        self._save()

    def record_paid_success(self):
        day = self._today()
        self._ensure_day(day)
        self.daily[day]["paid_success"] += 1
        self.daily[day]["total_replies"] += 1
        self._save()

    def record_paid_fail(self):
        day = self._today()
        self._ensure_day(day)
        self.daily[day]["paid_fail"] += 1
        self._save()

    def record_fallback(self):
        day = self._today()
        self._ensure_day(day)
        self.daily[day]["fallback_used"] += 1
        self.daily[day]["total_replies"] += 1
        self._save()

    def record_switch_to_paid(self, reason: str = ""):
        day = self._today()
        self._ensure_day(day)
        self.daily[day]["switch_to_paid"] += 1
        self.switch_history.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": "FREE → PAID",
            "reason": reason,
        })
        # Giữ tối đa 200 events
        if len(self.switch_history) > 200:
            self.switch_history = self.switch_history[-200:]
        self._save()

    def record_switch_to_free(self):
        day = self._today()
        self._ensure_day(day)
        self.daily[day]["switch_to_free"] += 1
        self.switch_history.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": "PAID → FREE",
            "reason": "cooldown hết, thử lại free",
        })
        if len(self.switch_history) > 200:
            self.switch_history = self.switch_history[-200:]
        self._save()

    def record_comment(
        self,
        commenter_name: str,
        comment_text: str,
        reply_text: str,
        status: str,
        model_used: str = "",
        post_id: str = "",
        max_items: int = 10,
    ):
        """Ghi nhận comment và câu trả lời. Tự reset khi sang ngày mới."""
        today = self._today()
        # Reset nếu sang ngày mới
        if self._recent_comments_date != today:
            self._recent_comments = []
            self._recent_comments_date = today

        self._recent_comments.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "commenter": commenter_name,
            "comment": comment_text[:100],  # Cắt ngắn để tiết kiệm bộ nhớ
            "reply": reply_text[:150],
            "status": status,
            "model": model_used,
            "post_id": post_id,
        })

        # Chỉ giữ N comment gần nhất
        if len(self._recent_comments) > max_items:
            self._recent_comments = self._recent_comments[-max_items:]

        self._save()

    def get_today(self) -> dict:
        day = self._today()
        self._ensure_day(day)
        return {"date": day, **self.daily[day]}

    def get_daily_summary(self, last_n_days: int = 14) -> list[dict]:
        """Lấy thống kê N ngày gần nhất."""
        sorted_days = sorted(self.daily.keys(), reverse=True)[:last_n_days]
        result = []
        for day in sorted_days:
            result.append({"date": day, **self.daily[day]})
        return result

    def get_recent_switches(self, limit: int = 50) -> list[dict]:
        """Lấy N switch events gần nhất."""
        return list(reversed(self.switch_history[-limit:]))

    def get_recent_comments(self) -> list[dict]:
        """Lấy 10 comment gần nhất của hôm nay."""
        today = self._today()
        if self._recent_comments_date != today:
            return []
        return list(reversed(self._recent_comments))

    def get_all_data(self) -> dict:
        """Trả về toàn bộ data cho API/dashboard."""
        return {
            "today": self.get_today(),
            "daily_summary": self.get_daily_summary(),
            "recent_switches": self.get_recent_switches(),
            "recent_comments": self.get_recent_comments(),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _save(self):
        try:
            data = {
                "daily": self.daily,
                "switch_history": self.switch_history,
                "recent_comments": self._recent_comments,
                "recent_comments_date": self._recent_comments_date,
            }
            with open(STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save model stats: {e}")

    def _load(self):
        if not os.path.exists(STATS_FILE):
            return
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.daily = data.get("daily", {})
            self.switch_history = data.get("switch_history", [])
            self._recent_comments = data.get("recent_comments", [])
            self._recent_comments_date = data.get("recent_comments_date", "")
            logger.info(
                f"Loaded model stats: {len(self.daily)} days, "
                f"{len(self.switch_history)} switch events, "
                f"{len(self._recent_comments)} recent comments"
            )
        except Exception as e:
            logger.error(f"Failed to load model stats: {e}")


# Singleton
stats = ModelStats()
