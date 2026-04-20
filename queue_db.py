"""
Queue Database – Persist comment queue sang SQLite để không bị mất khi restart/crash.

Luồng:
  1. Webhook nhận comment → save_pending()    (status = 'pending')
  2. Worker lấy item    → mark_processing()   (status = 'processing')
  3. Xử lý xong        → mark_done()          (status = 'done')
  4. Khi startup        → load_unfinished()   (load lại pending + processing → queue)

Chỉ giữ record 'done' trong 7 ngày gần nhất để tránh DB phình to.
"""

import sqlite3
import json
import logging
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "queue.db")

# Số ngày giữ record 'done' trước khi xóa
DONE_RETENTION_DAYS = 7


def _get_conn() -> sqlite3.Connection:
    """Tạo connection với WAL mode để tránh lock khi đọc/ghi đồng thời."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def _conn_ctx():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Tạo bảng nếu chưa có. Gọi 1 lần khi startup."""
    with _conn_ctx() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comment_queue (
                comment_id   TEXT PRIMARY KEY,
                page_id      TEXT NOT NULL,
                post_id      TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                commenter_id TEXT NOT NULL,
                commenter_name TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_status ON comment_queue(status)"
        )
    logger.info(f"QueueDB initialized: {DB_PATH}")


def save_pending(item: dict) -> bool:
    """
    Lưu comment mới vào DB với status='pending'.
    Nếu comment_id đã tồn tại (duplicate webhook) → bỏ qua, trả về False.
    """
    now = datetime.now().isoformat(timespec="seconds")
    try:
        with _conn_ctx() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO comment_queue
                    (comment_id, page_id, post_id, comment_text,
                     commenter_id, commenter_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    item["comment_id"],
                    item["page_id"],
                    item["post_id"],
                    item["comment_text"],
                    item["commenter_id"],
                    item["commenter_name"],
                    now,
                    now,
                ),
            )
        return True
    except Exception as e:
        logger.error(f"QueueDB save_pending error: {e}")
        return False


def mark_processing(comment_id: str):
    """Đánh dấu comment đang được worker xử lý."""
    _update_status(comment_id, "processing")


def mark_done(comment_id: str):
    """Đánh dấu comment đã xử lý xong (reply / skip / error đều là 'done')."""
    _update_status(comment_id, "done")


def _update_status(comment_id: str, status: str):
    now = datetime.now().isoformat(timespec="seconds")
    try:
        with _conn_ctx() as conn:
            conn.execute(
                "UPDATE comment_queue SET status=?, updated_at=? WHERE comment_id=?",
                (status, now, comment_id),
            )
    except Exception as e:
        logger.error(f"QueueDB _update_status({status}) error: {e}")


def load_unfinished() -> list[dict]:
    """
    Đọc tất cả record có status IN ('pending', 'processing') để recovery khi restart.
    Trả về list[dict] theo thứ tự created_at ASC (cũ nhất xử lý trước).
    """
    try:
        with _conn_ctx() as conn:
            rows = conn.execute(
                """
                SELECT comment_id, page_id, post_id, comment_text,
                       commenter_id, commenter_name
                FROM comment_queue
                WHERE status IN ('pending', 'processing')
                ORDER BY created_at ASC
                """
            ).fetchall()
        items = [dict(row) for row in rows]
        if items:
            logger.info(
                f"QueueDB: phục hồi {len(items)} comment chưa xử lý từ lần chạy trước"
            )
        return items
    except Exception as e:
        logger.error(f"QueueDB load_unfinished error: {e}")
        return []


def cleanup_old_done(days: int = DONE_RETENTION_DAYS):
    """
    Xóa các record 'done' cũ hơn `days` ngày để tránh DB phình to.
    Gọi tự động khi startup, không ảnh hưởng logic chính.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    try:
        with _conn_ctx() as conn:
            cur = conn.execute(
                "DELETE FROM comment_queue WHERE status='done' AND updated_at < ?",
                (cutoff,),
            )
        if cur.rowcount > 0:
            logger.info(f"QueueDB: đã xóa {cur.rowcount} record 'done' cũ hơn {days} ngày")
    except Exception as e:
        logger.error(f"QueueDB cleanup error: {e}")


def queue_stats() -> dict:
    """Trả về thống kê nhanh số record theo status (dùng cho API/debug)."""
    try:
        with _conn_ctx() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM comment_queue GROUP BY status"
            ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}
    except Exception as e:
        logger.error(f"QueueDB queue_stats error: {e}")
        return {}
