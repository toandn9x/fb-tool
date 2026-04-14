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

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot")

# ── Globals ──────────────────────────────────────────────────────────────
sheets: SheetsLogger | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    global sheets
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
    logger.info(f"  Delay   : {settings.REPLY_DELAY_SECONDS}s")
    logger.info(f"  AutoLike: {settings.AUTO_LIKE_ENABLED} ({settings.AUTO_LIKE_REACTION_TYPE})")
    logger.info(f"  Sheet   : {settings.GOOGLE_SHEET_NAME}")
    logger.info("-" * 50)
    port = settings.SERVER_PORT
    logger.info(f"  http://localhost:{port}/           → Health Check")
    logger.info(f"  http://localhost:{port}/dashboard   → Dashboard")
    logger.info(f"  http://localhost:{port}/model-status → Model Status")
    logger.info(f"  http://localhost:{port}/api/stats   → Stats API")
    logger.info("=" * 50)
    yield


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

            # Process in background to respond quickly to Facebook
            asyncio.create_task(
                process_comment(
                    page_id=page_id,
                    post_id=post_id,
                    comment_id=comment_id,
                    comment_text=comment_text,
                    commenter_id=commenter_id,
                    commenter_name=commenter_name,
                )
            )

    return {"status": "ok"}


# ── Comment Processing ──────────────────────────────────────────────────
async def process_comment(
    page_id: str,
    post_id: str,
    comment_id: str,
    comment_text: str,
    commenter_id: str,
    commenter_name: str,
):
    """Process a single comment: filter → delay → AI → reply → log."""
    like_status = ""
    try:
        # 1. Auto Like/React (thực hiện ngay, không cần chờ delay)
        if settings.AUTO_LIKE_ENABLED:
            reaction = settings.AUTO_LIKE_REACTION_TYPE
            liked = await like_comment(
                comment_id=comment_id,
                access_token=settings.FB_PAGE_ACCESS_TOKEN,
                reaction_type=reaction,
            )
            like_status = f"đã {reaction}" if liked else f"lỗi {reaction}"
            logger.info(
                f"Auto-react {comment_id}: {like_status}"
            )

        # 2. Filter
        ok, reason = should_reply(
            comment_text=comment_text,
            commenter_id=commenter_id,
            page_id=page_id,
            comment_id=comment_id,
            sheets_logger=sheets,
        )

        if not ok:
            logger.info(f"Skipped comment {comment_id}: {reason}")
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
            return

        # 3. Delay
        delay = settings.REPLY_DELAY_SECONDS
        if delay > 0:
            logger.info(f"Waiting {delay}s before replying...")
            await asyncio.sleep(delay)

        # 4. Fetch post content for context
        post_content = await get_post_content(
            post_id, settings.FB_PAGE_ACCESS_TOKEN
        )

        # 5. Build prompt
        prompts = settings.prompts
        system_prompt = prompts.get("system_prompt", "")
        user_message = prompts.get("reply_instruction", "").format(
            post_content=post_content or "(không có nội dung)",
            comment_text=comment_text,
            commenter_name=commenter_name,
        )

        # 6. Generate AI reply
        reply_text = await generate_reply(
            api_key=settings.OPENROUTER_API_KEY,
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=prompts.get("max_tokens", 150),
        )

        # 7. Reply on Facebook
        success = await reply_comment(
            comment_id=comment_id,
            message=reply_text,
            access_token=settings.FB_PAGE_ACCESS_TOKEN,
        )

        # 8. Log to Google Sheets
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

        logger.info(f"Done processing comment {comment_id} → {status}")

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


# ── Health Check ─────────────────────────────────────────────────────────
@app.get("/")
async def health():
    from openrouter_client import model_manager as mm
    return {
        "status": "running",
        "current_model": mm.current_model if mm else "N/A",
        "using_free": mm.is_using_free if mm else None,
        "delay": settings.REPLY_DELAY_SECONDS,
    }


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


# ── Dashboard ────────────────────────────────────────────────────────────
from fastapi.responses import HTMLResponse

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

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>Bot Dashboard – Model Stats</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: #0f0f23;
    color: #e0e0e0;
    padding: 20px;
    min-height: 100vh;
  }}
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
  .green {{ color: #4ade80; }}
  .blue {{ color: #60a5fa; }}
  .yellow {{ color: #fbbf24; }}
  .red {{ color: #f87171; }}
  .orange {{ color: #fb923c; }}
  .cyan {{ color: #22d3ee; }}

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

<div class="header">
  <h1>🤖 Bot Dashboard</h1>
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

<div class="auto-refresh">Tự động refresh sau mỗi 30 giây &nbsp;|&nbsp; <a href="/api/stats" style="color:#667eea;">JSON API</a></div>

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
