"""
OpenRouter API client – generate AI replies using free models.
"""

import httpx
import logging
import re
import random

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

FALLBACK_MESSAGE = [
    "Cảm ơn bạn đã quan tâm, follow page để theo dõi các bài viết hấp dẫn tiếp theo nhé.",
    "Cảm ơn bạn đã theo dõi. Tiếp tục đồng hành cùng fanpage bạn nhé!",
    "Chào bạn, cảm ơn bạn đã tương tác. Nhấn theo dõi để xem thêm nhiều nội dung thú vị.",
    "Rất vui vì nhận được bình luận từ bạn. Follow page để không bỏ lỡ tin mới nhất.",
    "Cảm ơn sự ủng hộ của bạn! Chúc bạn một ngày tốt lành.",
    "Cảm ơn bạn đã ghé thăm. Đừng quên bấm theo dõi để cập nhật tin tức mỗi ngày.",
    "Fanpage rất trân trọng sự quan tâm của bạn. Hẹn gặp bạn ở các bài viết tới!",
    "Cảm ơn bạn nhiều nha! Hãy theo dõi page để nhận thêm nhiều thông tin hữu ích.",
    "Rất cảm ơn bạn đã tương tác. Follow page ngay để nhận thông báo bài viết mới.",
    "Chào bạn, cảm ơn bạn đã quan tâm đến fanpage. Chúc bạn luôn vui vẻ!",
    "Sự tương tác của bạn là niềm vui cho page. Nhớ thường xuyên ghé thăm nhé!",
    "Cảm ơn bạn đã để lại bình luận. Follow page để ủng hộ tụi mình nhé.",
    "Chúc bạn ngày mới năng động. Cảm ơn bạn đã theo dõi fanpage!",
    "Rất vui vì bạn đã quan tâm. Hãy theo dõi page để cùng thảo luận thêm nhé.",
    "Cảm ơn bạn đã đồng hành. Đừng quên bật thông báo để xem bài mới sớm nhất.",
    "Cảm ơn bạn đã tương tác nhiệt tình. Follow page để nhận thêm nhiều điều thú vị.",
    "Chào bạn, cảm ơn bạn đã ghé qua. Hẹn gặp lại bạn trong các bài viết tiếp theo.",
    "Sự quan tâm của bạn là động lực rất lớn cho page. Cảm ơn bạn nhiều!",
    "Cảm ơn bạn đã tin tưởng và theo dõi. Chúc bạn mọi điều tốt đẹp.",
    "Follow page ngay để cập nhật những xu hướng mới nhất nhé. Cảm ơn bạn!",
    "Cảm ơn bạn đã dành thời gian tương tác. Page sẽ cố gắng mang lại nội dung hay hơn.",
    "Nhấn theo dõi để khám phá thêm nhiều điều bất ngờ từ fanpage nha. Cảm ơn bạn!",
    "Cảm ơn bạn đã là một phần của cộng đồng chúng mình. Chúc bạn ngày vui!",
    "Rất trân trọng ý kiến của bạn. Cảm ơn bạn đã theo dõi fanpage.",
    "Chào bạn, đừng quên follow page để tham gia các hoạt động hấp dẫn nhé!",
    "Cảm ơn bạn đã ủng hộ. Page luôn sẵn sàng lắng nghe bạn!",
    "Chúc bạn có những phút giây thư giãn cùng fanpage. Cảm ơn bạn đã quan tâm.",
    "Cảm ơn bạn đã tương tác. Nhớ bấm theo dõi để đón chờ những điều sắp tới!",
    "Thật tuyệt vời khi thấy bình luận của bạn. Cảm ơn bạn rất nhiều!",
    "Cảm ơn sự nhiệt tình của bạn. Tiếp tục theo dõi page để nhận tin hay nhé.",
    "Chào bạn, cảm ơn bạn đã luôn ủng hộ chúng mình. Mãi yêu!",
    "Page xin gửi lời cảm ơn chân thành đến sự quan tâm của bạn.",
    "Cảm ơn bạn đã theo dõi. Nhấn follow để không bỏ lỡ bất kỳ tin tức nào.",
    "Rất vui được gặp bạn ở đây. Cảm ơn bạn đã đồng hành cùng page.",
    "Chúc bạn thật nhiều niềm vui. Đừng quên follow page để cập nhật thông tin nhé.",
    "Cảm ơn bạn đã quan tâm. Page sẽ sớm có thêm nhiều bài viết hay cho bạn.",
    "Bình luận của bạn thật ý nghĩa. Cảm ơn bạn đã tương tác!",
    "Follow page để chúng mình có cơ hội phục vụ bạn tốt hơn nhé. Cảm ơn bạn!",
    "Cảm ơn bạn đã ghé thăm fanpage của tụi mình. Chúc bạn ngày ấm áp.",
    "Sự hiện diện của bạn làm page thêm sôi động. Trân trọng cảm ơn!",
    "Cảm ơn bạn đã tương tác. Hãy cùng chia sẻ page đến bạn bè nhé!",
    "Rất cảm ơn bạn đã quan tâm. Đừng rời đi mà chưa nhấn nút theo dõi nha.",
    "Cảm ơn bạn vì đã quan tâm. Page luôn trân quý từng lượt tương tác của bạn.",
    "Chúc bạn gặt hái nhiều thành công. Cảm ơn bạn đã follow fanpage!",
    "Cảm ơn bạn đã để lại dấu ấn tại đây. Hẹn sớm gặp lại bạn!",
    "Rất vui vì nhận được sự quan tâm từ bạn. Chúc bạn vạn sự như ý.",
    "Cảm ơn bạn đã theo dõi page thường xuyên. Yêu thương thật nhiều!",
    "Đừng quên bấm theo dõi để không bỏ lỡ tin hay mỗi ngày nhé. Cảm ơn bạn!",
    "Page luôn nỗ lực vì sự hài lòng của bạn. Cảm ơn bạn đã quan tâm.",
    "Cảm ơn bạn đã đồng hành cùng sự phát triển của fanpage!",
    "Cảm ơn bạn đã theo dõi...Đó là nguồn động viên tinh thần rất lớn với đội ngũ Cánh Sóng Âm Vang",
    "Cảm ơn bạn đã theo dõi...Đó là nguồn động viên tinh thần rất lớn với đội ngũ Cánh Sóng Âm Vang",
    "Cảm ơn bạn đã theo dõi...Đó là nguồn động viên tinh thần rất lớn với đội ngũ Cánh Sóng Âm Vang",
]


def get_random_fallback() -> str:
    """Return a random message from the FALLBACK_MESSAGE list."""
    return random.choice(FALLBACK_MESSAGE)


def _clean_ai_reply(raw: str) -> str:
    """
    Lọc bỏ phần suy luận/reasoning của AI, chỉ giữ lại câu trả lời cuối.

    Một số model (đặc biệt DeepSeek) hay output quá trình suy nghĩ
    trước khi đưa ra câu trả lời thực sự.
    """
    text = raw.strip()

    # 1. Loại bỏ <think>...</think> hoặc <reasoning>...</reasoning>
    text = re.sub(
        r"<(?:think|thinking|reasoning)>.*?</(?:think|thinking|reasoning)>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    ).strip()

    # 2. Nếu có pattern "So answer:" hoặc "So the answer is:" → lấy phần sau
    for marker in [
        "So the answer is:", "So answer:", "Final answer:",
        "My reply:", "Reply:", "Response:", "Output:",
    ]:
        if marker.lower() in text.lower():
            idx = text.lower().index(marker.lower()) + len(marker)
            candidate = text[idx:].strip().strip('"').strip("'").strip()
            if candidate:
                text = candidate
                break

    # 3. Nếu text bắt đầu bằng tiếng Anh reasoning, lấy dòng cuối (thường là câu trả lời VN)
    if text and re.match(r"^(We need|According to|The user|I should|Let me|Based on)", text, re.IGNORECASE):
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        # Tìm dòng cuối có tiếng Việt
        for line in reversed(lines):
            if re.search(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", line, re.IGNORECASE):
                text = line.strip('"').strip("'").strip()
                break

    # 4. Loại bỏ dấu ngoặc kép bao quanh nếu có
    if len(text) > 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()

    # 5. Loại bỏ markdown còn sót
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"^[-•]\s*", "", text)

    return text.strip()


async def generate_reply(
    api_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 150,
) -> str:
    """
    Call OpenRouter API to generate a reply.

    Args:
        api_key: OpenRouter API key
        model: Model ID (e.g. 'meta-llama/llama-3.3-70b-instruct:free')
        system_prompt: System prompt for the AI
        user_message: The user message (formatted prompt with comment info)
        max_tokens: Maximum tokens in response

    Returns:
        Generated reply text, or a random fallback message on error.
    """
    if not api_key:
        return get_random_fallback()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://fb-auto-reply-bot.local",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                OPENROUTER_API_URL, json=payload, headers=headers
            )
            response.raise_for_status()
            data = response.json()

            raw_reply = data["choices"][0]["message"]["content"].strip()
            reply = _clean_ai_reply(raw_reply)

            # Nếu sau khi clean mà rỗng → dùng fallback
            if not reply:
                logger.warning(f"AI reply empty after cleaning. Raw: {raw_reply[:100]}")
                return get_random_fallback()

            logger.info(f"AI reply generated: {reply[:80]}...")
            return reply

    except httpx.TimeoutException:
        logger.error("OpenRouter API timeout")
        return get_random_fallback()

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter API error {e.response.status_code}: {e.response.text}")
        return get_random_fallback()

    except Exception as e:
        logger.error(f"OpenRouter unexpected error: {e}")
        return get_random_fallback()
