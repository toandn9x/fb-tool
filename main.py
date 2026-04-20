"""
Facebook Fanpage Auto-Reply Bot
Main FastAPI server – handles Facebook webhook events.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from config import settings
from comment_filter import should_reply
from openrouter_client import generate_reply, init_model_manager, model_manager
from facebook_client import reply_comment, like_comment, get_post_content, verify_signature
from sheets_logger import SheetsLogger
from worker_state import state as worker_state
import queue_db

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot")

# ── Globals ──────────────────────────────────────────────────────────────
sheets: SheetsLogger | None = None
comment_queue: asyncio.Queue | None = None
_worker_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    global sheets, comment_queue, _worker_task
    try:
        sheets = SheetsLogger(
            credentials_path=settings.GOOGLE_SHEETS_CREDENTIALS,
            sheet_name=settings.GOOGLE_SHEET_NAME,
        )
        logger.info("Google Sheets logger ready")
    except Exception as e:
        logger.warning(f"Google Sheets not available: {e}")
        sheets = None

    logger.info("=" * 50)
    logger.info("Facebook Auto-Reply Bot started")
    # Khởi tạo ModelManager
    init_model_manager(
        free_model=settings.OPENROUTER_MODEL_FREE,
        paid_model=settings.OPENROUTER_MODEL_PAID,
        cooldown_minutes=settings.MODEL_FALLBACK_COOLDOWN,
    )
    logger.info(f"  Free    : {settings.OPENROUTER_MODEL_FREE}")
    logger.info(f"  Paid    : {settings.OPENROUTER_MODEL_PAID}")
    logger.info(f"  Cooldown: {settings.MODEL_FALLBACK_COOLDOWN} phút")
    logger.info(f"  Delay   : {settings.REPLY_DELAY_SECONDS}s (áp dụng cho cả react & reply, tuần tự)")
    logger.info(f"  AutoLike: {settings.AUTO_LIKE_ENABLED} ({settings.AUTO_LIKE_REACTION_TYPE})")
    logger.info(f"  Sheet   : {settings.GOOGLE_SHEET_NAME}")
    logger.info("-" * 50)
    port = settings.SERVER_PORT
    logger.info(f"  http://localhost:{port}/           → Trang chủ")
    logger.info(f"  http://localhost:{port}/dashboard   → Dashboard")
    logger.info(f"  http://localhost:{port}/worker      → Worker Monitor")
    logger.info(f"  http://localhost:{port}/model-status → Model Status")
    logger.info(f"  http://localhost:{port}/api/stats   → Stats API")
    logger.info(f"  http://localhost:{port}/api/worker  → Worker API")
    logger.info("=" * 50)

    # ── Khởi tạo SQLite persistent queue ──────────────────────────────
    queue_db.init_db()
    queue_db.cleanup_old_done()  # dọn record 'done' cũ hơn 7 ngày

    # Khởi tạo queue + worker xử lý comment tuần tự
    comment_queue = asyncio.Queue()
    _worker_task = asyncio.create_task(_comment_worker())
    logger.info("Comment queue worker started (xử lý tuần tự)")

    # ── Recovery: nạp lại các comment chưa xử lý từ lần chạy trước ───
    recovered = queue_db.load_unfinished()
    if recovered:
        for item in recovered:
            await comment_queue.put(item)
            worker_state.on_enqueue(item)
        logger.info(
            f"Recovery: đã nạp lại {len(recovered)} comment vào queue"
        )

    try:
        yield
    finally:
        if _worker_task:
            _worker_task.cancel()
            try:
                await _worker_task
            except (asyncio.CancelledError, Exception):
                pass
            logger.info("Comment queue worker stopped")


app = FastAPI(title="FB Auto-Reply Bot", lifespan=lifespan)


# ── Webhook Verification (GET) ───────────────────────────────────────────
@app.get("/webhook")
async def webhook_verify(request: Request):
    """Facebook webhook verification endpoint."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == settings.FB_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return PlainTextResponse(content=challenge)

    logger.warning(f"Webhook verification failed (token: {token})")
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Webhook Handler (POST) ──────────────────────────────────────────────
@app.post("/webhook")
async def webhook_handler(request: Request):
    """Handle incoming webhook events from Facebook."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    # Verify signature
    if not verify_signature(body, signature, settings.FB_APP_SECRET):
        logger.warning("Invalid signature, rejecting request")
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = await request.json()

    if data.get("object") != "page":
        return {"status": "ignored"}

    # Process each entry
    logger.info(f"Đã nhận tín hiệu từ Facebook (Object: {data.get('object')})")
    for entry in data.get("entry", []):
        page_id = entry.get("id", "")

        for change in entry.get("changes", []):
            if change.get("field") != "feed":
                continue

            value = change.get("value", {})

            # Only process new comments
            if value.get("item") != "comment" or value.get("verb") != "add":
                continue

            # Extract comment data
            comment_id = value.get("comment_id", "")
            comment_text = value.get("message", "")
            post_id = value.get("post_id", "")
            commenter = value.get("from", {})
            commenter_id = commenter.get("id", "")
            commenter_name = commenter.get("name", "Unknown")

            logger.info(
                f"New comment from {commenter_name}: "
                f"{comment_text[:50]}..."
            )

            # Enqueue – worker sẽ xử lý tuần tự, không miss comment nào
            if comment_queue is not None:
                item = {
                    "page_id": page_id,
                    "post_id": post_id,
                    "comment_id": comment_id,
                    "comment_text": comment_text,
                    "commenter_id": commenter_id,
                    "commenter_name": commenter_name,
                }
                # Persist vào DB trước khi đưa vào in-memory queue.
                # INSERT OR IGNORE → tự bỏ qua nếu là duplicate webhook.
                saved = queue_db.save_pending(item)
                if not saved:
                    logger.info(
                        f"Duplicate webhook, bỏ qua comment {comment_id}"
                    )
                    continue
                await comment_queue.put(item)
                worker_state.on_enqueue(item)
                logger.info(
                    f"Queued comment {comment_id} "
                    f"(hàng đợi: {comment_queue.qsize()})"
                )

    return {"status": "ok"}


# ── Comment Queue Worker ────────────────────────────────────────────────
async def _comment_worker():
    """
    Worker duy nhất tiêu thụ comment_queue tuần tự.
    Đảm bảo FB chỉ nhận tối đa 1 action (react hoặc reply) mỗi
    REPLY_DELAY_SECONDS giây → tránh bị đánh dấu spam/bot.
    """
    worker_state.on_worker_start()
    while True:
        item = await comment_queue.get()
        comment_id = item.get("comment_id", "")
        # Đánh dấu đang xử lý trong DB
        queue_db.mark_processing(comment_id)
        worker_state.on_start_processing(item)
        try:
            await process_comment(**item)
        except Exception as e:
            logger.error(f"Worker error: {e}")
        finally:
            # Safety net – nếu process_comment crash mà chưa kịp on_complete
            if worker_state.current_item is not None:
                worker_state.on_complete("lỗi – worker exception")
            # Đánh dấu hoàn tất trong DB (dù lỗi hay thành công)
            queue_db.mark_done(comment_id)
            comment_queue.task_done()


async def _fb_delay():
    """Delay chung trước mỗi lần gọi Facebook API (react / reply)."""
    delay = settings.REPLY_DELAY_SECONDS
    if delay > 0:
        logger.info(f"Chờ {delay}s trước FB action...")
        await asyncio.sleep(delay)


# ── Comment Processing ──────────────────────────────────────────────────
async def process_comment(
    page_id: str,
    post_id: str,
    comment_id: str,
    comment_text: str,
    commenter_id: str,
    commenter_name: str,
):
    """
    Xử lý 1 comment tuần tự trong worker:
    delay → react → filter → AI → delay → reply → log.
    """
    like_status = ""
    try:
        # 1. Auto Like/React (chờ delay trước khi gọi FB)
        if settings.AUTO_LIKE_ENABLED:
            worker_state.set_stage("react_delay")
            await _fb_delay()
            worker_state.set_stage("reacting")
            reaction = settings.AUTO_LIKE_REACTION_TYPE
            liked = await like_comment(
                comment_id=comment_id,
                access_token=settings.FB_PAGE_ACCESS_TOKEN,
                reaction_type=reaction,
            )
            like_status = f"đã {reaction}" if liked else f"lỗi {reaction}"
            logger.info(f"Auto-react {comment_id}: {like_status}")

        # 2. Filter
        worker_state.set_stage("filtering")
        ok, reason = should_reply(
            comment_text=comment_text,
            commenter_id=commenter_id,
            page_id=page_id,
            comment_id=comment_id,
            sheets_logger=sheets,
        )

        if not ok:
            logger.info(f"Skipped comment {comment_id}: {reason}")
            worker_state.set_stage("logging")
            if sheets:
                sheets.log_comment(
                    post_id=post_id,
                    comment_id=comment_id,
                    commenter_name=commenter_name,
                    comment_text=comment_text,
                    reply_text="",
                    status=f"bỏ qua – {reason}",
                    like_status=like_status,
                )
            worker_state.on_complete(f"bỏ qua – {reason}")
            return

        # 3. Fetch post content for context
        worker_state.set_stage("fetching_post")
        post_content = await get_post_content(
            post_id, settings.FB_PAGE_ACCESS_TOKEN
        )

        # 4. Build prompt
        prompts = settings.prompts
        system_prompt = prompts.get("system_prompt", "")
        user_message = prompts.get("reply_instruction", "").format(
            post_content=post_content or "(không có nội dung)",
            comment_text=comment_text,
            commenter_name=commenter_name,
        )

        # 5. Generate AI reply (không phải FB API → không cần delay)
        worker_state.set_stage("generating_ai")
        reply_text = await generate_reply(
            api_key=settings.OPENROUTER_API_KEY,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=prompts.get("max_tokens", 150),
        )

        # 6. Reply on Facebook (chờ delay trước khi gọi FB)
        worker_state.set_stage("reply_delay")
        await _fb_delay()
        worker_state.set_stage("replying")
        success = await reply_comment(
            comment_id=comment_id,
            message=reply_text,
            access_token=settings.FB_PAGE_ACCESS_TOKEN,
        )

        # 7. Log to Google Sheets
        worker_state.set_stage("logging")
        status = "đã reply" if success else "lỗi"
        if sheets:
            sheets.log_comment(
                post_id=post_id,
                comment_id=comment_id,
                commenter_name=commenter_name,
                comment_text=comment_text,
                reply_text=reply_text,
                status=status,
                like_status=like_status,
            )

        # 8. Ghi vào recent comments (cho homepage)
        from model_stats import stats as model_stats_inst
        from openrouter_client import model_manager as mm
        model_stats_inst.record_comment(
            commenter_name=commenter_name,
            comment_text=comment_text,
            reply_text=reply_text,
            status=status,
            model_used=mm.current_model if mm else "",
            post_id=post_id,
            comment_id=comment_id,
            max_items=settings.RECENT_COMMENTS_LIMIT,
        )

        logger.info(f"Done processing comment {comment_id} → {status}")
        worker_state.on_complete(
            status,
            reply_text=reply_text,
            model_used=mm.current_model if mm else "",
        )

    except Exception as e:
        logger.error(f"Error processing comment {comment_id}: {e}")
        if sheets:
            sheets.log_comment(
                post_id=post_id,
                comment_id=comment_id,
                commenter_name=commenter_name,
                comment_text=comment_text,
                reply_text="",
                status=f"lỗi – {e}",
                like_status=like_status,
            )
        worker_state.on_complete(f"lỗi – {e}")

# ── Shared ───────────────────────────────────────────────────────────────
from fastapi.responses import HTMLResponse
import time as _time

_boot_time = _time.time()

SHARED_CSS = """
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: #0f0f23;
    color: #e0e0e0;
    min-height: 100vh;
  }
  .topnav {
    background: #1a1a2e;
    border-bottom: 1px solid #2a2a4a;
    padding: 12px 24px;
    display: flex;
    align-items: center;
    gap: 24px;
  }
  .topnav .brand {
    font-weight: 700;
    font-size: 1.1em;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .topnav a {
    color: #888;
    text-decoration: none;
    font-size: 0.9em;
    padding: 6px 14px;
    border-radius: 8px;
    transition: all 0.2s;
  }
  .topnav a:hover { background: #2a2a4a; color: #e0e0e0; }
  .topnav a.active { background: #667eea22; color: #667eea; }
  .container { padding: 30px; max-width: 1200px; margin: 0 auto; }
  .green { color: #4ade80; }
  .blue { color: #60a5fa; }
  .yellow { color: #fbbf24; }
  .red { color: #f87171; }
  .orange { color: #fb923c; }
  .cyan { color: #22d3ee; }
  .purple { color: #a78bfa; }
"""

NAV_HTML = """
<nav class="topnav">
  <span class="brand">🤖 FB Auto-Reply Bot</span>
  <a href="/" {home_active}>Trang chủ</a>
  <a href="/dashboard" {dash_active}>Dashboard</a>
  <a href="/worker" {worker_active}>Worker</a>
  <a href="/api/stats" {api_active}>API</a>
</nav>
"""


def _nav(active: str = "home") -> str:
    return NAV_HTML.format(
        home_active='class="active"' if active == "home" else "",
        dash_active='class="active"' if active == "dashboard" else "",
        worker_active='class="active"' if active == "worker" else "",
        api_active='class="active"' if active == "api" else "",
    )


def _uptime() -> str:
    secs = int(_time.time() - _boot_time)
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    mins, secs = divmod(secs, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m {secs}s")
    return " ".join(parts)


# ── Homepage ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def homepage():
    from openrouter_client import model_manager as mm
    from model_stats import stats
    from datetime import datetime

    model_info = mm.status() if mm else {}
    today = stats.get_today()

    status_class = "status-free" if model_info.get("using_free") else "status-paid"
    status_label = "🟢 FREE" if model_info.get("using_free") else "🟡 PAID"
    current_model = model_info.get("current_model", "N/A")
    uptime = _uptime()
    now = datetime.now().strftime("%H:%M:%S %d/%m/%Y")

    # Tính tỷ lệ free hôm nay
    total = today["total_replies"]
    free_pct = f'{today["free_success"]/total*100:.0f}' if total > 0 else "0"

    # Build recent comments rows
    recent_comments = stats.get_recent_comments()
    comment_rows = ""
    for c in recent_comments:
        status_cls = "tag-free" if c["status"] == "đã reply" else "tag-paid"
        model_short = c["model"].split("/")[-1] if c.get("model") else ""
        pid = c.get("post_id", "")
        cid = c.get("comment_id", "")
        post_link = f'<a href="https://www.facebook.com/{pid}" target="_blank" style="color:#60a5fa;text-decoration:none;" title="Xem bài viết">Xem bài ↗</a>' if pid else "—"
        comment_link = (
            f'<a href="https://www.facebook.com/{cid}" target="_blank" '
            f'style="color:#a78bfa;text-decoration:none;" title="Xem comment">Chi tiết ↗</a>'
            if cid else "—"
        )
        comment_rows += f"""
        <tr>
            <td>{c['time']}</td>
            <td><strong>{c['commenter']}</strong></td>
            <td class="comment-cell">{c['comment']}</td>
            <td class="reply-cell">{c['reply']}</td>
            <td><span class="tag {status_cls}">{c['status']}</span></td>
            <td style="font-size:0.75em;color:#888;">{model_short}</td>
            <td>{post_link}</td>
            <td>{comment_link}</td>
        </tr>"""

    refresh_s = settings.HOMEPAGE_REFRESH_SECONDS
    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{_refresh_meta(refresh_s)}
<title>FB Auto-Reply Bot</title>
<style>
  {SHARED_CSS}
  .hero {{
    text-align: center;
    padding: 50px 20px 30px;
  }}
  .hero h1 {{
    font-size: 2.4em;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 8px;
  }}
  .hero .tagline {{ color: #666; font-size: 0.95em; }}

  .status-pill {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 50px;
    padding: 10px 24px;
    margin: 20px 0;
    font-size: 0.95em;
  }}
  .status-pill.status-free {{ border-color: #4ade8044; }}
  .status-pill.status-paid {{ border-color: #fbbf2444; }}
  .status-pill .model {{ font-family: monospace; color: #a78bfa; font-size: 0.9em; }}

  .info-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 20px;
    margin: 30px 0;
  }}
  .info-card {{
    background: #1a1a2e;
    border-radius: 14px;
    padding: 24px;
    border: 1px solid #2a2a4a;
  }}
  .info-card h3 {{
    color: #a78bfa;
    font-size: 0.85em;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 16px;
  }}
  .info-row {{
    display: flex;
    justify-content: space-between;
    padding: 6px 0;
    font-size: 0.9em;
    border-bottom: 1px solid #2a2a4a22;
  }}
  .info-row .label {{ color: #888; }}
  .info-row .value {{ font-family: monospace; }}

  .quick-stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 12px;
    margin: 30px 0;
  }}
  .stat-card {{
    background: #1a1a2e;
    border-radius: 12px;
    padding: 16px;
    text-align: center;
    border: 1px solid #2a2a4a;
  }}
  .stat-card .num {{
    font-size: 1.8em;
    font-weight: 700;
    font-family: monospace;
  }}
  .stat-card .lbl {{ color: #888; font-size: 0.75em; text-transform: uppercase; margin-top: 4px; }}

  .nav-cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin: 30px 0;
  }}
  .nav-card {{
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 14px;
    padding: 24px;
    text-decoration: none;
    color: #e0e0e0;
    transition: all 0.25s;
  }}
  .nav-card:hover {{
    border-color: #667eea;
    transform: translateY(-2px);
    box-shadow: 0 8px 25px rgba(102, 126, 234, 0.15);
  }}
  .nav-card .icon {{ font-size: 1.8em; margin-bottom: 10px; }}
  .nav-card .title {{ font-weight: 600; margin-bottom: 6px; }}
  .nav-card .desc {{ color: #888; font-size: 0.8em; line-height: 1.4; }}

  .footer {{ text-align: center; color: #444; font-size: 0.75em; padding: 30px 0 10px; }}

  .section {{ margin: 30px 0; }}
  .section h2 {{
    font-size: 1.1em;
    color: #a78bfa;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #2a2a4a;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85em;
  }}
  th {{
    background: #16213e;
    padding: 10px 8px;
    text-align: left;
    color: #a78bfa;
    font-weight: 600;
  }}
  td {{
    padding: 8px;
    border-bottom: 1px solid #1a1a2e;
  }}
  tr:hover {{ background: #16213e44; }}
  .comment-cell {{ max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #ccc; }}
  .reply-cell {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #4ade80; }}
  .tag {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.8em;
    font-weight: 600;
  }}
  .tag-free {{ background: #4ade8022; color: #4ade80; border: 1px solid #4ade8044; }}
  .tag-paid {{ background: #f8717122; color: #f87171; border: 1px solid #f8717144; }}

  @media (max-width: 600px) {{
    .hero h1 {{ font-size: 1.6em; }}
    .info-grid {{ grid-template-columns: 1fr; }}
    .quick-stats {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>

{_nav("home")}

<div class="container">

<div class="hero">
  <h1>🤖 FB Auto-Reply Bot</h1>
  <div class="tagline">Tự động trả lời comment fanpage bằng AI</div>
  <div class="status-pill {status_class}">
    {status_label}
    <span class="model">{current_model}</span>
  </div>
</div>

<div class="quick-stats">
  <div class="stat-card">
    <div class="num green">{today['free_success']}</div>
    <div class="lbl">Free OK</div>
  </div>
  <div class="stat-card">
    <div class="num blue">{today['paid_success']}</div>
    <div class="lbl">Paid OK</div>
  </div>
  <div class="stat-card">
    <div class="num yellow">{today['fallback_used']}</div>
    <div class="lbl">Fallback</div>
  </div>
  <div class="stat-card">
    <div class="num" style="color:#e0e0e0;">{total}</div>
    <div class="lbl">Tổng hôm nay</div>
  </div>
  <div class="stat-card">
    <div class="num purple">{free_pct}%</div>
    <div class="lbl">Tỷ lệ Free</div>
  </div>
</div>

<div class="info-grid">
  <div class="info-card">
    <h3>⚙️ Cấu hình</h3>
    <div class="info-row"><span class="label">Model Free</span><span class="value purple">{settings.OPENROUTER_MODEL_FREE}</span></div>
    <div class="info-row"><span class="label">Model Paid</span><span class="value blue">{settings.OPENROUTER_MODEL_PAID}</span></div>
    <div class="info-row"><span class="label">Cooldown</span><span class="value">{settings.MODEL_FALLBACK_COOLDOWN} phút</span></div>
    <div class="info-row"><span class="label">Reply Delay</span><span class="value">{settings.REPLY_DELAY_SECONDS}s</span></div>
    <div class="info-row"><span class="label">Auto Like</span><span class="value green">{settings.AUTO_LIKE_REACTION_TYPE}</span></div>
  </div>
  <div class="info-card">
    <h3>📡 Trạng thái</h3>
    <div class="info-row"><span class="label">Status</span><span class="value green">Running</span></div>
    <div class="info-row"><span class="label">Uptime</span><span class="value">{uptime}</span></div>
    <div class="info-row"><span class="label">Port</span><span class="value">{settings.SERVER_PORT}</span></div>
    <div class="info-row"><span class="label">Google Sheet</span><span class="value">{settings.GOOGLE_SHEET_NAME}</span></div>
    <div class="info-row"><span class="label">Cập nhật</span><span class="value">{now}</span></div>
  </div>
</div>

<div class="nav-cards">
  <a href="/dashboard" class="nav-card">
    <div class="icon">📊</div>
    <div class="title">Dashboard</div>
    <div class="desc">Thống kê model usage theo ngày, lịch sử switch, bảng chi tiết 14 ngày</div>
  </a>
  <a href="/worker" class="nav-card">
    <div class="icon">🔧</div>
    <div class="title">Worker Monitor</div>
    <div class="desc">Theo dõi real-time: comment đang xử lý, stage, queue, lịch sử {settings.RECENT_COMMENTS_LIMIT} comment gần nhất</div>
  </a>
  <a href="/api/stats" class="nav-card">
    <div class="icon">🔌</div>
    <div class="title">Stats API</div>
    <div class="desc">JSON API trả về toàn bộ dữ liệu thống kê cho tích hợp bên ngoài</div>
  </a>
  <a href="/model-status" class="nav-card">
    <div class="icon">⚙️</div>
    <div class="title">Model Status</div>
    <div class="desc">JSON chi tiết trạng thái ModelManager: model đang dùng, cooldown, fail count</div>
  </a>
</div>

<div class="section">
  <h2>💬 {settings.RECENT_COMMENTS_LIMIT} Comment gần nhất (hôm nay)</h2>
  <div style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Giờ</th>
        <th>Người BL</th>
        <th>Comment</th>
        <th>Reply</th>
        <th>Trạng thái</th>
        <th>Model</th>
        <th>Bài viết</th>
        <th>Chi tiết</th>
      </tr>
    </thead>
    <tbody>
      {comment_rows if comment_rows else '<tr><td colspan="8" style="text-align:center;color:#666;">Chưa có comment nào hôm nay</td></tr>'}
    </tbody>
  </table>
  </div>
</div>

<div class="footer">{_refresh_label(refresh_s)} &nbsp;•&nbsp; FB Auto-Reply Bot v1.0</div>

</div>
</body>
</html>"""
    return html


@app.get("/model-status")
async def get_model_status():
    """Xem trạng thái chi tiết của ModelManager."""
    from openrouter_client import model_manager as mm
    if not mm:
        return {"error": "ModelManager chưa khởi tạo"}
    return mm.status()


# ── Stats API ────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def api_stats():
    """JSON API cho model stats."""
    from model_stats import stats
    from openrouter_client import model_manager as mm
    return {
        "model_status": mm.status() if mm else {},
        **stats.get_all_data(),
    }


# ── Worker Status API ────────────────────────────────────────────────────
@app.get("/api/worker")
async def api_worker():
    """JSON snapshot trạng thái worker real-time."""
    qsize = comment_queue.qsize() if comment_queue else 0
    return worker_state.snapshot(
        queue_size=qsize,
        history_limit=settings.RECENT_COMMENTS_LIMIT,
    )


def _refresh_meta(seconds: int) -> str:
    """Emit meta tag auto-refresh, trả rỗng nếu seconds <= 0 (tắt refresh)."""
    if seconds and seconds > 0:
        return f'<meta http-equiv="refresh" content="{seconds}">'
    return ""


def _refresh_label(seconds: int) -> str:
    """Label footer: 'Tự động refresh mỗi Ns' hoặc 'Auto-refresh: tắt'."""
    if seconds and seconds > 0:
        return f"Tự động refresh mỗi {seconds}s"
    return "Auto-refresh: tắt"


def _fmt_duration(secs: int) -> str:
    """Format giây → '1h 20m 5s' hoặc '5m 20s' hoặc '45s'."""
    secs = int(secs)
    if secs <= 0:
        return "0s"
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


@app.get("/worker", response_class=HTMLResponse)
async def worker_page():
    """Trang theo dõi worker real-time: item đang xử lý, queue, lịch sử."""
    qsize = comment_queue.qsize() if comment_queue else 0
    snap = worker_state.snapshot(
        queue_size=qsize,
        history_limit=settings.RECENT_COMMENTS_LIMIT,
    )

    running = snap["worker_running"]
    status_class = "status-free" if running else "status-paid"
    status_label = "🟢 RUNNING" if running else "⛔ STOPPED"
    uptime = snap["worker_uptime"] or "—"
    started_at = snap["worker_started_at"] or "—"

    current = snap["current"]
    counters = snap["counters"]
    throughput = snap["throughput"]
    delay_s = settings.REPLY_DELAY_SECONDS

    # Tính số liệu cho Progress section
    enq = counters["enqueued"]
    done = counters["total_done"]
    done_pct = (done / enq * 100) if enq > 0 else 0
    reply_pct = (
        counters["completed"] / done * 100 if done > 0 else 0
    )
    skip_pct = (
        counters["skipped"] / done * 100 if done > 0 else 0
    )
    fail_pct = (
        counters["failed"] / done * 100 if done > 0 else 0
    )
    avg_sec = throughput["avg_elapsed_sec"]
    eta_str = _fmt_duration(throughput["eta_seconds"]) if throughput["eta_seconds"] > 0 else "—"
    per_min = throughput["per_minute"]

    # Card hiển thị item đang xử lý (nếu có)
    if current:
        comment_preview = (current["comment_text"] or "")[:200]
        post_id = current.get("post_id", "")
        post_link = (
            f'<a href="https://www.facebook.com/{post_id}" target="_blank" '
            f'style="color:#60a5fa;">Xem bài ↗</a>'
            if post_id else "—"
        )
        total_el = current["elapsed_sec"]
        stage_el = current.get("stage_elapsed_sec", 0)
        stage = current["stage"]

        # Nếu stage là delay → hiển thị progress bar tới REPLY_DELAY_SECONDS
        stage_progress_html = ""
        if stage in ("react_delay", "reply_delay") and delay_s > 0:
            pct = min(100, (stage_el / delay_s) * 100)
            stage_progress_html = f"""
            <div class="stage-progress">
              <div class="stage-progress-bar" style="width:{pct:.0f}%"></div>
              <div class="stage-progress-label">{stage_el:.0f}s / {delay_s}s</div>
            </div>"""

        # Cảnh báo stuck nếu total > 3× avg (và avg > 0)
        stuck_badge = ""
        avg = throughput["avg_elapsed_sec"]
        if avg > 0 and total_el > avg * 3:
            stuck_badge = (
                f'<span class="stuck-badge" title="Chậm hơn 3× trung bình '
                f'({avg:.0f}s)">⚠ Chậm bất thường</span>'
            )

        current_html = f"""
        <div class="current-card">
          <div class="current-header">
            <span class="pulse"></span>
            <strong>Đang xử lý</strong>
            <span class="stage-badge">{current['stage_label']}</span>
            {stuck_badge}
            <span style="margin-left:auto;color:#888;font-size:0.85em;">
              bắt đầu {current['started_at']}
            </span>
          </div>
          <div class="current-times">
            <div class="time-box">
              <div class="time-label">Tổng</div>
              <div class="time-value">⏱ {_fmt_duration(total_el)}</div>
            </div>
            <div class="time-box">
              <div class="time-label">Stage này</div>
              <div class="time-value stage-time">🔸 {stage_el:.1f}s</div>
            </div>
          </div>
          {stage_progress_html}
          <div class="current-body">
            <div class="row"><span class="label">Người BL</span>
              <span class="value"><strong>{current['commenter_name']}</strong></span></div>
            <div class="row"><span class="label">Comment ID</span>
              <span class="value mono">{current['comment_id']}</span></div>
            <div class="row"><span class="label">Nội dung</span>
              <span class="value">{comment_preview}</span></div>
            <div class="row"><span class="label">Bài viết</span>
              <span class="value">{post_link}</span></div>
          </div>
        </div>"""
    else:
        current_html = """
        <div class="current-card idle">
          <div class="current-header">
            <span class="dot-idle"></span>
            <strong>Worker đang rảnh</strong>
            <span style="margin-left:auto;color:#666;font-size:0.85em;">
              chờ comment tiếp theo…
            </span>
          </div>
        </div>"""

    # Bảng lịch sử – cấu trúc giống trang chủ (thêm Reply, Model, Bài viết)
    history_rows = ""
    for h in snap["history"]:
        status = h["status"]
        if status == "đã reply":
            cls = "tag-free"
        elif status.startswith("bỏ qua"):
            cls = "tag-skip"
        else:
            cls = "tag-paid"
        model_short = h["model"].split("/")[-1] if h.get("model") else ""
        pid = h.get("post_id", "")
        post_link = (
            f'<a href="https://www.facebook.com/{pid}" target="_blank" '
            f'style="color:#60a5fa;text-decoration:none;" title="Xem bài viết">Xem bài ↗</a>'
            if pid else "—"
        )
        cid = h.get("comment_id", "")
        comment_link = (
            f'<a href="https://www.facebook.com/{cid}" target="_blank" '
            f'style="color:#a78bfa;text-decoration:none;" title="Xem comment">Chi tiết ↗</a>'
            if cid else "—"
        )
        history_rows += f"""
        <tr>
            <td>{h['time']}</td>
            <td><strong>{h['commenter']}</strong></td>
            <td class="comment-cell">{h['comment']}</td>
            <td class="reply-cell">{h.get('reply', '') or '—'}</td>
            <td><span class="tag {cls}">{status}</span></td>
            <td style="font-size:0.75em;color:#888;">{model_short}</td>
            <td class="mono">{h['elapsed_sec']}s</td>
            <td>{post_link}</td>
            <td>{comment_link}</td>
        </tr>"""

    refresh_s = settings.WORKER_REFRESH_SECONDS
    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{_refresh_meta(refresh_s)}
<title>Worker Monitor – FB Auto-Reply Bot</title>
<style>
  {SHARED_CSS}
  .header {{ text-align: center; margin-bottom: 25px; padding-top: 20px; }}
  .header h1 {{
    font-size: 1.8em;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 5px;
  }}
  .header .subtitle {{ color: #888; font-size: 0.85em; }}

  .status-banner {{
    background: #1a1a2e;
    border-radius: 12px;
    padding: 16px 24px;
    margin-bottom: 25px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    border: 1px solid #2a2a4a;
  }}
  .status-free {{ border-left: 4px solid #4ade80; }}
  .status-paid {{ border-left: 4px solid #f87171; }}

  .counters {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 25px;
  }}
  .counter-card {{
    background: #1a1a2e;
    border-radius: 12px;
    padding: 16px;
    text-align: center;
    border: 1px solid #2a2a4a;
  }}
  .counter-card .num {{
    font-size: 2em;
    font-weight: 700;
    font-family: monospace;
  }}
  .counter-card .lbl {{ color: #888; font-size: 0.75em; text-transform: uppercase; margin-top: 4px; }}

  .current-card {{
    background: linear-gradient(135deg, #1a1a2e 0%, #252544 100%);
    border-radius: 14px;
    padding: 20px;
    margin-bottom: 25px;
    border: 1px solid #667eea44;
    box-shadow: 0 4px 20px rgba(102, 126, 234, 0.15);
  }}
  .current-card.idle {{
    border-color: #2a2a4a;
    box-shadow: none;
    background: #1a1a2e;
  }}
  .current-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    padding-bottom: 12px;
    border-bottom: 1px solid #2a2a4a;
    margin-bottom: 12px;
  }}
  .current-body .row {{
    display: flex;
    gap: 10px;
    padding: 6px 0;
    font-size: 0.9em;
  }}
  .current-body .label {{
    color: #888;
    min-width: 100px;
    flex-shrink: 0;
  }}
  .current-body .value {{ color: #e0e0e0; word-break: break-word; }}
  .mono {{ font-family: monospace; color: #a78bfa; font-size: 0.85em; }}

  .pulse {{
    width: 10px;
    height: 10px;
    background: #4ade80;
    border-radius: 50%;
    animation: pulse 1.5s infinite;
  }}
  @keyframes pulse {{
    0% {{ box-shadow: 0 0 0 0 rgba(74, 222, 128, 0.7); }}
    70% {{ box-shadow: 0 0 0 10px rgba(74, 222, 128, 0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(74, 222, 128, 0); }}
  }}
  .dot-idle {{
    width: 10px;
    height: 10px;
    background: #555;
    border-radius: 50%;
  }}

  .stage-badge {{
    background: #667eea22;
    color: #a78bfa;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 0.8em;
    border: 1px solid #667eea44;
  }}
  .stuck-badge {{
    background: #f8717122;
    color: #f87171;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 0.8em;
    border: 1px solid #f8717166;
    animation: blink 1s infinite;
  }}
  @keyframes blink {{
    50% {{ opacity: 0.5; }}
  }}

  /* ── Tiến độ hôm nay ─────────────────────────────────── */
  .progress-section {{
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 12px;
    padding: 18px 22px;
    margin-bottom: 20px;
  }}
  .progress-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 10px;
  }}
  .progress-math {{
    margin-left: 12px;
    color: #888;
    font-size: 0.85em;
  }}
  .progress-pct-label {{
    font-size: 1.1em;
    font-weight: 700;
    color: #4ade80;
    font-family: monospace;
  }}
  .progress-bar-wrap {{
    display: flex;
    height: 18px;
    border-radius: 10px;
    overflow: hidden;
    background: #2a2a4a;
    margin: 10px 0 12px;
  }}
  .progress-seg {{ height: 100%; transition: width 0.4s; }}
  .progress-seg.green-bar {{ background: #4ade80; }}
  .progress-seg.yellow-bar {{ background: #fbbf24; }}
  .progress-seg.red-bar {{ background: #f87171; }}
  .progress-seg.inflight-bar {{
    background: repeating-linear-gradient(
      45deg, #a78bfa, #a78bfa 6px, #8f6fe0 6px, #8f6fe0 12px
    );
  }}
  .progress-legend {{
    display: flex;
    gap: 18px;
    flex-wrap: wrap;
    font-size: 0.82em;
    color: #aaa;
  }}
  .progress-legend .dot {{
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    margin-right: 5px;
    vertical-align: middle;
  }}
  .green-dot {{ background: #4ade80; }}
  .yellow-dot {{ background: #fbbf24; }}
  .red-dot {{ background: #f87171; }}
  .inflight-dot {{ background: #a78bfa; }}
  .queue-dot {{ background: #60a5fa; }}

  /* ── Throughput / ETA ────────────────────────────────── */
  .throughput-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }}
  .tp-card {{
    background: #1a1a2e;
    border: 1px solid #2a2a4a;
    border-radius: 12px;
    padding: 16px 20px;
  }}
  .tp-card.eta {{ border-left: 3px solid #60a5fa; }}
  .tp-label {{ color: #888; font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.5px; }}
  .tp-value {{
    font-size: 1.6em;
    font-family: monospace;
    color: #e0e0e0;
    margin: 4px 0 2px;
  }}
  .tp-unit {{ font-size: 0.55em; color: #888; margin-left: 3px; }}
  .tp-sub {{ font-size: 0.75em; color: #666; }}

  /* ── Current card: tách Total vs Stage ───────────────── */
  .current-times {{
    display: flex;
    gap: 12px;
    margin: 10px 0;
  }}
  .time-box {{
    flex: 1;
    background: #0f0f23;
    border-radius: 8px;
    padding: 10px 14px;
    border: 1px solid #2a2a4a;
  }}
  .time-label {{ color: #888; font-size: 0.72em; text-transform: uppercase; }}
  .time-value {{ font-family: monospace; color: #e0e0e0; font-size: 1.1em; margin-top: 3px; }}
  .stage-time {{ color: #fbbf24; }}

  .stage-progress {{
    position: relative;
    height: 14px;
    background: #2a2a4a;
    border-radius: 8px;
    overflow: hidden;
    margin: 8px 0 12px;
  }}
  .stage-progress-bar {{
    height: 100%;
    background: linear-gradient(90deg, #fbbf24, #f59e0b);
    transition: width 0.5s;
  }}
  .stage-progress-label {{
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75em;
    color: #0f0f23;
    font-weight: 700;
    font-family: monospace;
  }}

  .section {{ margin-bottom: 25px; }}
  .section h2 {{
    font-size: 1.05em;
    color: #a78bfa;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #2a2a4a;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
  th {{
    background: #16213e;
    padding: 10px 8px;
    text-align: left;
    color: #a78bfa;
    font-weight: 600;
  }}
  td {{ padding: 8px; border-bottom: 1px solid #1a1a2e; }}
  tr:hover {{ background: #16213e44; }}
  .comment-cell {{ max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #ccc; }}
  .reply-cell {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #4ade80; }}

  .tag {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.8em;
    font-weight: 600;
  }}
  .tag-free {{ background: #4ade8022; color: #4ade80; border: 1px solid #4ade8044; }}
  .tag-paid {{ background: #f8717122; color: #f87171; border: 1px solid #f8717144; }}
  .tag-skip {{ background: #fbbf2422; color: #fbbf24; border: 1px solid #fbbf2444; }}

  .auto-refresh {{ color: #555; font-size: 0.75em; text-align: center; margin-top: 20px; }}
</style>
</head>
<body>

{_nav("worker")}

<div class="container">

<div class="header">
  <h1>🔧 Worker Monitor</h1>
  <div class="subtitle">Real-time trạng thái comment queue worker – cập nhật {snap['generated_at']}</div>
</div>

<div class="status-banner {status_class}">
  <div>
    <strong>Trạng thái:</strong> {status_label}
    &nbsp;•&nbsp; Uptime: <span class="mono">{uptime}</span>
  </div>
  <div style="color:#888; font-size:0.85em;">
    Bắt đầu: {started_at}
  </div>
</div>

<div class="progress-section">
  <div class="progress-header">
    <div>
      <strong>Tiến độ hôm nay</strong>
      <span class="progress-math">
        Nhận <strong style="color:#e0e0e0;">{enq}</strong>
        = Xong <strong class="green">{done}</strong>
        + Đang xử lý <strong style="color:#a78bfa;">{counters['in_flight']}</strong>
        + Chờ <strong style="color:#60a5fa;">{counters['in_queue']}</strong>
      </span>
    </div>
    <div class="progress-pct-label">{done_pct:.0f}% &nbsp;<span style="color:#666;">({done}/{enq})</span></div>
  </div>
  <div class="progress-bar-wrap">
    <div class="progress-seg green-bar" style="width:{(counters['completed']/enq*100) if enq else 0:.2f}%" title="Đã reply: {counters['completed']}"></div>
    <div class="progress-seg yellow-bar" style="width:{(counters['skipped']/enq*100) if enq else 0:.2f}%" title="Bỏ qua: {counters['skipped']}"></div>
    <div class="progress-seg red-bar" style="width:{(counters['failed']/enq*100) if enq else 0:.2f}%" title="Lỗi: {counters['failed']}"></div>
    <div class="progress-seg inflight-bar" style="width:{(counters['in_flight']/enq*100) if enq else 0:.2f}%" title="Đang xử lý: {counters['in_flight']}"></div>
  </div>
  <div class="progress-legend">
    <span><span class="dot green-dot"></span>Reply {counters['completed']} ({reply_pct:.0f}%)</span>
    <span><span class="dot yellow-dot"></span>Skip {counters['skipped']} ({skip_pct:.0f}%)</span>
    <span><span class="dot red-dot"></span>Lỗi {counters['failed']} ({fail_pct:.0f}%)</span>
    <span><span class="dot inflight-dot"></span>In-flight {counters['in_flight']}</span>
    <span><span class="dot queue-dot"></span>Chờ {counters['in_queue']}</span>
  </div>
</div>

<div class="throughput-grid">
  <div class="tp-card">
    <div class="tp-label">⚡ Avg / comment</div>
    <div class="tp-value">{avg_sec:.1f}<span class="tp-unit">s</span></div>
    <div class="tp-sub">{per_min}/phút</div>
  </div>
  <div class="tp-card eta">
    <div class="tp-label">⏳ ETA hết queue</div>
    <div class="tp-value">{eta_str}</div>
    <div class="tp-sub">{counters['in_queue']} chờ + {counters['in_flight']} đang xử lý</div>
  </div>
  <div class="tp-card">
    <div class="tp-label">⚙️ Delay cấu hình</div>
    <div class="tp-value">{delay_s}<span class="tp-unit">s</span></div>
    <div class="tp-sub">áp dụng react + reply</div>
  </div>
</div>

<div class="counters">
  <div class="counter-card"><div class="num" style="color:#60a5fa;">{counters['in_queue']}</div><div class="lbl">Đang chờ</div></div>
  <div class="counter-card"><div class="num" style="color:#a78bfa;">{counters['in_flight']}</div><div class="lbl">Đang xử lý</div></div>
  <div class="counter-card"><div class="num" style="color:#e0e0e0;">{counters['enqueued']}</div><div class="lbl">Tổng nhận</div></div>
  <div class="counter-card"><div class="num green">{counters['completed']}</div><div class="lbl">Đã reply</div></div>
  <div class="counter-card"><div class="num yellow">{counters['skipped']}</div><div class="lbl">Bỏ qua</div></div>
  <div class="counter-card"><div class="num red">{counters['failed']}</div><div class="lbl">Lỗi</div></div>
</div>

{current_html}

<div class="section">
  <h2>📜 Lịch sử {settings.RECENT_COMMENTS_LIMIT} comment gần nhất (hôm nay)</h2>
  <div style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Giờ</th>
        <th>Người BL</th>
        <th>Comment</th>
        <th>Reply</th>
        <th>Kết quả</th>
        <th>Model</th>
        <th>Thời gian</th>
        <th>Bài viết</th>
        <th>Chi tiết</th>
      </tr>
    </thead>
    <tbody>
      {history_rows if history_rows else '<tr><td colspan="9" style="text-align:center;color:#666;">Chưa có lịch sử</td></tr>'}
    </tbody>
  </table>
  </div>
</div>

<div class="auto-refresh">
  {_refresh_label(refresh_s)}
  &nbsp;|&nbsp; <a href="/api/worker" style="color:#667eea;">JSON API</a>
  &nbsp;|&nbsp; <a href="/dashboard" style="color:#667eea;">Dashboard</a>
</div>

</div>
</body>
</html>"""
    return html


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Dashboard trực quan để theo dõi model usage."""
    from model_stats import stats
    from openrouter_client import model_manager as mm

    data = stats.get_all_data()
    today = data["today"]
    model_info = mm.status() if mm else {}

    # Build daily rows
    daily_rows = ""
    for day in data["daily_summary"]:
        total = day["total_replies"]
        free_pct = f'{day["free_success"]/total*100:.0f}%' if total > 0 else "0%"
        daily_rows += f"""
        <tr>
            <td>{day['date']}</td>
            <td class="num green">{day['free_success']}</td>
            <td class="num red">{day['free_fail']}</td>
            <td class="num blue">{day['paid_success']}</td>
            <td class="num red">{day['paid_fail']}</td>
            <td class="num yellow">{day['fallback_used']}</td>
            <td class="num orange">{day['switch_to_paid']}</td>
            <td class="num cyan">{day['switch_to_free']}</td>
            <td class="num">{total}</td>
            <td class="num">{free_pct}</td>
        </tr>"""

    # Build switch history rows
    switch_rows = ""
    for ev in data["recent_switches"][:30]:
        action_class = "tag-paid" if "PAID" in ev["action"].split("→")[-1].strip() else "tag-free"
        switch_rows += f"""
        <tr>
            <td>{ev['time']}</td>
            <td><span class="tag {action_class}">{ev['action']}</span></td>
            <td>{ev['reason']}</td>
        </tr>"""

    # Current status
    status_class = "status-free" if model_info.get("using_free") else "status-paid"
    status_label = "🟢 FREE" if model_info.get("using_free") else "🟡 PAID"
    current_model = model_info.get("current_model", "N/A")
    switched_at = model_info.get("switched_at") or "—"

    refresh_s = settings.DASHBOARD_REFRESH_SECONDS
    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{_refresh_meta(refresh_s)}
<title>Bot Dashboard – Model Stats</title>
<style>
  {SHARED_CSS}
  .container {{ padding: 30px; max-width: 1200px; margin: 0 auto; }}
  .header {{
    text-align: center;
    margin-bottom: 30px;
  }}
  .header h1 {{
    font-size: 1.8em;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 5px;
  }}
  .header .subtitle {{ color: #888; font-size: 0.85em; }}

  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 15px;
    margin-bottom: 30px;
  }}
  .card {{
    background: #1a1a2e;
    border-radius: 12px;
    padding: 18px;
    border: 1px solid #2a2a4a;
    text-align: center;
  }}
  .card .value {{
    font-size: 2em;
    font-weight: 700;
    margin: 8px 0 4px;
  }}
  .card .label {{ color: #888; font-size: 0.8em; text-transform: uppercase; }}

  .status-banner {{
    background: #1a1a2e;
    border-radius: 12px;
    padding: 16px 24px;
    margin-bottom: 25px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    border: 1px solid #2a2a4a;
  }}
  .status-banner .model {{ font-family: monospace; color: #a78bfa; }}
  .status-free {{ border-left: 4px solid #4ade80; }}
  .status-paid {{ border-left: 4px solid #fbbf24; }}

  .section {{ margin-bottom: 30px; }}
  .section h2 {{
    font-size: 1.1em;
    color: #a78bfa;
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid #2a2a4a;
  }}

  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85em;
  }}
  th {{
    background: #16213e;
    padding: 10px 8px;
    text-align: left;
    color: #a78bfa;
    font-weight: 600;
    position: sticky;
    top: 0;
  }}
  td {{
    padding: 8px;
    border-bottom: 1px solid #1a1a2e;
  }}
  tr:hover {{ background: #16213e44; }}
  .num {{ text-align: center; font-family: monospace; }}

  .tag {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.8em;
    font-weight: 600;
  }}
  .tag-paid {{ background: #fbbf2422; color: #fbbf24; border: 1px solid #fbbf2444; }}
  .tag-free {{ background: #4ade8022; color: #4ade80; border: 1px solid #4ade8044; }}

  .auto-refresh {{ color: #555; font-size: 0.75em; text-align: center; margin-top: 20px; }}

  @media (max-width: 600px) {{
    .cards {{ grid-template-columns: repeat(2, 1fr); }}
    .status-banner {{ flex-direction: column; text-align: center; }}
    table {{ font-size: 0.75em; }}
  }}
</style>
</head>
<body>

{_nav("dashboard")}

<div class="container">

<div class="header">
  <h1>📊 Bot Dashboard</h1>
  <div class="subtitle">Model Usage Statistics – Cập nhật lúc {data['generated_at']}</div>
</div>

<div class="status-banner {status_class}">
  <div>
    <strong>Trạng thái:</strong> {status_label}
    <span class="model">{current_model}</span>
  </div>
  <div style="color:#888; font-size:0.85em;">
    Switched at: {switched_at} &nbsp;|&nbsp; Fail count: {model_info.get('free_fail_count', 0)}
  </div>
</div>

<div class="cards">
  <div class="card">
    <div class="label">Free OK</div>
    <div class="value green">{today['free_success']}</div>
  </div>
  <div class="card">
    <div class="label">Free Fail</div>
    <div class="value red">{today['free_fail']}</div>
  </div>
  <div class="card">
    <div class="label">Paid OK</div>
    <div class="value blue">{today['paid_success']}</div>
  </div>
  <div class="card">
    <div class="label">Paid Fail</div>
    <div class="value red">{today['paid_fail']}</div>
  </div>
  <div class="card">
    <div class="label">Fallback</div>
    <div class="value yellow">{today['fallback_used']}</div>
  </div>
  <div class="card">
    <div class="label">Switch → Paid</div>
    <div class="value orange">{today['switch_to_paid']}</div>
  </div>
  <div class="card">
    <div class="label">Switch → Free</div>
    <div class="value cyan">{today['switch_to_free']}</div>
  </div>
  <div class="card">
    <div class="label">Tổng Reply</div>
    <div class="value" style="color:#e0e0e0;">{today['total_replies']}</div>
  </div>
</div>

<div class="section">
  <h2>📊 Thống kê theo ngày (14 ngày gần nhất)</h2>
  <div style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Ngày</th>
        <th>Free ✓</th>
        <th>Free ✗</th>
        <th>Paid ✓</th>
        <th>Paid ✗</th>
        <th>Fallback</th>
        <th>→Paid</th>
        <th>→Free</th>
        <th>Tổng</th>
        <th>Free%</th>
      </tr>
    </thead>
    <tbody>
      {daily_rows if daily_rows else '<tr><td colspan="10" style="text-align:center;color:#666;">Chưa có dữ liệu</td></tr>'}
    </tbody>
  </table>
  </div>
</div>

<div class="section">
  <h2>🔄 Lịch sử Switch gần đây (30 events)</h2>
  <div style="overflow-x:auto;">
  <table>
    <thead>
      <tr>
        <th>Thời gian</th>
        <th>Hành động</th>
        <th>Lý do</th>
      </tr>
    </thead>
    <tbody>
      {switch_rows if switch_rows else '<tr><td colspan="3" style="text-align:center;color:#666;">Chưa có switch nào</td></tr>'}
    </tbody>
  </table>
  </div>
</div>

<div class="auto-refresh">{_refresh_label(refresh_s)} &nbsp;|&nbsp; <a href="/api/stats" style="color:#667eea;">JSON API</a> &nbsp;|&nbsp; <a href="/" style="color:#667eea;">Trang chủ</a></div>

</div>
</body>
</html>"""
    return html

# ── Run directly ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.SERVER_PORT,
        reload=True,
    )
