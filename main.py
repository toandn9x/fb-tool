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
from openrouter_client import generate_reply
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
    logger.info(f"  Model   : {settings.OPENROUTER_MODEL}")
    logger.info(f"  Delay   : {settings.REPLY_DELAY_SECONDS}s")
    logger.info(f"  AutoLike: {settings.AUTO_LIKE_ENABLED} ({settings.AUTO_LIKE_REACTION_TYPE})")
    logger.info(f"  Sheet   : {settings.GOOGLE_SHEET_NAME}")
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
            model=settings.OPENROUTER_MODEL,
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
    return {
        "status": "running",
        "model": settings.OPENROUTER_MODEL,
        "delay": settings.REPLY_DELAY_SECONDS,
    }


# ── Run directly ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.SERVER_PORT,
        reload=True,
    )
