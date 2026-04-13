"""
Facebook Graph API client – reply to comments, like/react, fetch post content.
"""

import hmac
import hashlib
import httpx
import logging

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


async def reply_comment(
    comment_id: str, message: str, access_token: str
) -> bool:
    """
    Reply to a Facebook comment.

    Args:
        comment_id: The comment ID to reply to
        message: Reply message text
        access_token: Page access token

    Returns:
        True if reply was successful, False otherwise.
    """
    url = f"{GRAPH_API_BASE}/{comment_id}/comments"
    params = {
        "message": message,
        "access_token": access_token,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, data=params)
            response.raise_for_status()
            logger.info(f"Replied to comment {comment_id}")
            return True

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Failed to reply to {comment_id}: "
            f"{e.response.status_code} – {e.response.text}"
        )
        return False

    except Exception as e:
        logger.error(f"Reply error for {comment_id}: {e}")
        return False


# Các reaction hợp lệ trên Facebook
VALID_REACTIONS = {"LIKE", "LOVE", "HAHA", "WOW", "SAD", "ANGRY"}


async def like_comment(
    comment_id: str,
    access_token: str,
    reaction_type: str = "LIKE",
) -> bool:
    """
    Like or React to a Facebook comment.

    Args:
        comment_id: The comment ID to react to
        access_token: Page access token
        reaction_type: Reaction type – LIKE, LOVE, HAHA, WOW, SAD, ANGRY

    Returns:
        True if reaction was successful, False otherwise.
    """
    reaction_type = reaction_type.upper()
    if reaction_type not in VALID_REACTIONS:
        logger.warning(
            f"Invalid reaction '{reaction_type}', falling back to LIKE"
        )
        reaction_type = "LIKE"

    # Endpoint: dùng /likes cho LIKE, /reactions cho các loại khác
    if reaction_type == "LIKE":
        url = f"{GRAPH_API_BASE}/{comment_id}/likes"
        params = {"access_token": access_token}
    else:
        url = f"{GRAPH_API_BASE}/{comment_id}/reactions"
        params = {
            "type": reaction_type,
            "access_token": access_token,
        }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, data=params)
            response.raise_for_status()
            logger.info(
                f"Reacted {reaction_type} to comment {comment_id}"
            )
            return True

    except httpx.HTTPStatusError as e:
        logger.error(
            f"Failed to react to {comment_id}: "
            f"{e.response.status_code} – {e.response.text}"
        )
        return False

    except Exception as e:
        logger.error(f"React error for {comment_id}: {e}")
        return False


async def get_post_content(post_id: str, access_token: str) -> str:
    """
    Fetch the content/message of a Facebook post.

    Returns:
        Post message text or empty string on error.
    """
    url = f"{GRAPH_API_BASE}/{post_id}"
    params = {
        "fields": "message",
        "access_token": access_token,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("message", "")

    except Exception as e:
        logger.warning(f"Could not fetch post {post_id}: {e}")
        return ""


def verify_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    """
    Verify that the webhook payload is genuinely from Facebook.

    Args:
        payload: Raw request body bytes
        signature: X-Hub-Signature-256 header value
        app_secret: Facebook App Secret

    Returns:
        True if signature is valid.
    """
    if not signature or not app_secret:
        return True  # Skip verification if not configured

    expected = "sha256=" + hmac.new(
        app_secret.encode(), payload, hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
