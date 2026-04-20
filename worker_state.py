"""
Worker State Tracker – theo dõi real-time trạng thái của comment queue worker.

- Thông tin item đang xử lý (stage, elapsed)
- Counter tổng: enqueued / completed / skipped / failed
- Lịch sử N item gần nhất đã hoàn tất
"""

import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

# Cap tuyệt đối tránh buffer grow vô hạn.
# Giới hạn hiển thị thực tế do caller (main.py) truyền vào snapshot(limit=...),
# mặc định lấy theo settings.RECENT_COMMENTS_LIMIT.
MAX_HISTORY = 500

# Các stage khi xử lý 1 comment. Key = internal code, value = label hiển thị.
STAGES = {
    "idle": "Rảnh (chờ comment)",
    "starting": "Bắt đầu",
    "react_delay": "Chờ delay trước react",
    "reacting": "Đang react lên FB",
    "filtering": "Lọc comment",
    "fetching_post": "Fetch nội dung bài viết",
    "generating_ai": "AI đang tạo câu trả lời",
    "reply_delay": "Chờ delay trước reply",
    "replying": "Đang reply lên FB",
    "logging": "Ghi log Sheets",
}


class WorkerState:
    """Singleton theo dõi trạng thái worker."""

    def __init__(self):
        self.worker_started_at: datetime | None = None
        self.current_item: dict | None = None
        self.current_stage: str = "idle"
        self.current_started_at: datetime | None = None

        self.total_enqueued = 0
        self.total_completed = 0   # reply thành công
        self.total_skipped = 0     # filter bỏ qua
        self.total_failed = 0      # exception hoặc reply thất bại

        self.history: list[dict] = []
        # Ngày hiện tại – dùng để auto reset history/counter sang 0 qua ngày mới,
        # giống logic recent_comments ở model_stats.py
        self._history_date: str = self._today()

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    def _check_day_rollover(self):
        """Reset history + counter nếu đã sang ngày mới."""
        today = self._today()
        if self._history_date != today:
            logger.info(
                f"WorkerState: day rollover "
                f"{self._history_date} → {today}, reset history & counters"
            )
            self.history = []
            self.total_enqueued = 0
            self.total_completed = 0
            self.total_skipped = 0
            self.total_failed = 0
            self._history_date = today

    # ── Worker lifecycle ────────────────────────────────────────────────
    def on_worker_start(self):
        self.worker_started_at = datetime.now()
        self.current_stage = "idle"
        logger.info("WorkerState: worker started")

    # ── Enqueue (từ webhook) ────────────────────────────────────────────
    def on_enqueue(self, item: dict):
        self._check_day_rollover()
        self.total_enqueued += 1

    # ── Bắt đầu xử lý 1 item ────────────────────────────────────────────
    def on_start_processing(self, item: dict):
        self._check_day_rollover()
        self.current_item = {
            "comment_id": item.get("comment_id", ""),
            "commenter_name": item.get("commenter_name", ""),
            "comment_text": item.get("comment_text", ""),
            "post_id": item.get("post_id", ""),
        }
        self.current_started_at = datetime.now()
        self.current_stage = "starting"

    # ── Chuyển stage ────────────────────────────────────────────────────
    def set_stage(self, stage: str):
        self.current_stage = stage

    # ── Hoàn tất 1 item ─────────────────────────────────────────────────
    def on_complete(
        self,
        status: str,
        reply_text: str = "",
        model_used: str = "",
    ):
        self._check_day_rollover()
        if self.current_item and self.current_started_at:
            elapsed = (datetime.now() - self.current_started_at).total_seconds()
            self.history.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "comment_id": self.current_item.get("comment_id", ""),
                "commenter": self.current_item.get("commenter_name", ""),
                "comment": self.current_item.get("comment_text", "")[:150],
                "reply": reply_text[:200] if reply_text else "",
                "model": model_used,
                "post_id": self.current_item.get("post_id", ""),
                "status": status,
                "elapsed_sec": round(elapsed, 2),
            })
            if len(self.history) > MAX_HISTORY:
                self.history = self.history[-MAX_HISTORY:]

            if status == "đã reply":
                self.total_completed += 1
            elif status.startswith("bỏ qua"):
                self.total_skipped += 1
            else:
                self.total_failed += 1

        self.current_item = None
        self.current_started_at = None
        self.current_stage = "idle"

    # ── Snapshot cho dashboard/API ──────────────────────────────────────
    def snapshot(self, queue_size: int = 0, history_limit: int = 20) -> dict:
        self._check_day_rollover()
        current = None
        if self.current_item and self.current_started_at:
            elapsed = (datetime.now() - self.current_started_at).total_seconds()
            current = {
                **self.current_item,
                "stage": self.current_stage,
                "stage_label": STAGES.get(self.current_stage, self.current_stage),
                "elapsed_sec": round(elapsed, 2),
                "started_at": self.current_started_at.strftime("%H:%M:%S"),
            }

        uptime = None
        if self.worker_started_at:
            secs = int((datetime.now() - self.worker_started_at).total_seconds())
            d, secs = divmod(secs, 86400)
            h, secs = divmod(secs, 3600)
            m, s = divmod(secs, 60)
            parts = []
            if d:
                parts.append(f"{d}d")
            if h or d:
                parts.append(f"{h}h")
            parts.append(f"{m}m {s}s")
            uptime = " ".join(parts)

        return {
            "worker_running": self.worker_started_at is not None,
            "worker_started_at": (
                self.worker_started_at.strftime("%Y-%m-%d %H:%M:%S")
                if self.worker_started_at else None
            ),
            "worker_uptime": uptime,
            "queue_size": queue_size,
            "current": current,
            "counters": {
                "enqueued": self.total_enqueued,
                "completed": self.total_completed,
                "skipped": self.total_skipped,
                "failed": self.total_failed,
                "in_queue": queue_size,
            },
            "history": list(reversed(self.history[-history_limit:])),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


# Singleton
state = WorkerState()
