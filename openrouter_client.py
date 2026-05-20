"""
OpenRouter API client – generate AI replies using free models.
Auto-switch giữa model free và paid khi bị rate limit.
"""

import httpx
import logging
import re
import random
from datetime import datetime, timedelta
from model_stats import stats as model_stats

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
    "Cảm ơn bạn đã theo dõi...Đó là nguồn động viên tinh thần rất lớn với đội ngũ Lật Mở Hồ Sơ Mật",
    "Cảm ơn bạn đã theo dõi...Đó là nguồn động viên tinh thần rất lớn với đội ngũ Lật Mở Hồ Sơ Mật",
    "Cảm ơn bạn đã theo dõi...Đó là nguồn động viên tinh thần rất lớn với đội ngũ Lật Mở Hồ Sơ Mật",
    "Cảm ơn bạn đã theo dõi, nhớ follow page để đón xem kỳ tiếp nhé!",
    "Cảm ơn bạn đã quan tâm, mời bạn follow page để đón đọc tập tiếp theo nhé!",
    "Cảm ơn bạn đã ủng hộ, mời bạn follow page để đón đọc tập tiếp theo nhé!",
    "Cảm ơn bạn đã quan tâm, nhớ theo dõi page để đón đọc nhé!",
    "Cảm ơn bạn đã theo dõi, hãy tiếp tục đồng hành cùng Lật Mở Hồ Sơ Mật nhé",
    "Cảm ơn bạn đã tương tác, hãy tiếp tục ủng hộ page nhé!",
    "Cảm ơn bạn đã tương tác, nếu thấy hay hãy like và chia sẻ bài viết nhé!",
    "Cảm ơn bạn đã quan tâm, nếu thấy hay hãy like và chia sẻ bài viết nhé!",
    "Cảm ơn bạn, mời bạn tiếp tục đón đọc các tập tiếp theo trên fanpage nhé!",
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


# ── Model Manager ────────────────────────────────────────────────────────

class ModelManager:
    """
    Quản lý auto-switch giữa model free và paid.

    - Ưu tiên model free
    - Nếu free bị rate limit (429/402/503) → chuyển sang paid
    - Sau mỗi cooldown_minutes → tự thử lại free
    """

    def __init__(
        self,
        free_model: str,
        paid_model: str,
        cooldown_minutes: int = 120,
    ):
        self.free_model = free_model
        self.paid_model = paid_model
        self.cooldown = timedelta(minutes=cooldown_minutes)

        self._using_free = True
        self._switched_at: datetime | None = None
        self._free_fail_count = 0

    @property
    def current_model(self) -> str:
        """Model đang được sử dụng."""
        # Nếu đang dùng paid, kiểm tra xem đã hết cooldown chưa
        if not self._using_free and self._switched_at:
            elapsed = datetime.now() - self._switched_at
            if elapsed >= self.cooldown:
                logger.info(
                    f"⏰ Cooldown hết ({self.cooldown.total_seconds()/60:.0f} phút). "
                    f"Thử lại model free: {self.free_model}"
                )
                self._using_free = True
                self._switched_at = None
                self._free_fail_count = 0
                model_stats.record_switch_to_free()

        return self.free_model if self._using_free else self.paid_model

    @property
    def is_using_free(self) -> bool:
        return self._using_free

    def switch_to_paid(self, reason: str = ""):
        """Chuyển sang model paid."""
        if self._using_free:
            self._using_free = False
            self._switched_at = datetime.now()
            self._free_fail_count += 1
            model_stats.record_switch_to_paid(reason)
            logger.warning(
                f"🔄 Chuyển sang model paid: {self.paid_model} "
                f"(lý do: {reason}). "
                f"Sẽ thử lại free sau {self.cooldown.total_seconds()/60:.0f} phút."
            )

    def report_success(self):
        """Báo cáo model hiện tại hoạt động OK."""
        if self._using_free and self._free_fail_count > 0:
            logger.info(f"✅ Model free hoạt động lại! ({self.free_model})")
            self._free_fail_count = 0

    def status(self) -> dict:
        """Trạng thái hiện tại."""
        return {
            "current_model": self.current_model,
            "using_free": self._using_free,
            "free_model": self.free_model,
            "paid_model": self.paid_model,
            "switched_at": str(self._switched_at) if self._switched_at else None,
            "free_fail_count": self._free_fail_count,
        }


# ── Singleton ModelManager (được khởi tạo từ main.py) ───────────────────
model_manager: ModelManager | None = None


def init_model_manager(
    free_model: str,
    paid_model: str,
    cooldown_minutes: int = 120,
) -> ModelManager:
    """Khởi tạo ModelManager singleton."""
    global model_manager
    model_manager = ModelManager(free_model, paid_model, cooldown_minutes)
    logger.info(
        f"ModelManager initialized: free={free_model}, "
        f"paid={paid_model}, cooldown={cooldown_minutes}m"
    )
    return model_manager


# ── Rate limit error codes ───────────────────────────────────────────────
RATE_LIMIT_CODES = {429, 402, 503}


async def generate_reply(
    api_key: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int = 150,
) -> str:
    """
    Gọi OpenRouter API để tạo reply.
    Tự động switch model nếu bị rate limit.

    Args:
        api_key: OpenRouter API key
        system_prompt: System prompt for the AI
        user_message: The user message (formatted prompt with comment info)
        max_tokens: Maximum tokens in response

    Returns:
        Generated reply text, or a random fallback message on error.
    """
    if not api_key:
        return get_random_fallback()

    if not model_manager:
        logger.error("ModelManager chưa được khởi tạo!")
        return get_random_fallback()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://fb-auto-reply-bot.local",
    }

    # Lấy model hiện tại (tự kiểm tra cooldown)
    current = model_manager.current_model
    is_free = model_manager.is_using_free

    # Thử gọi API
    result = await _call_openrouter(
        headers, current, system_prompt, user_message, max_tokens
    )

    if result is not None:
        model_manager.report_success()
        if is_free:
            model_stats.record_free_success()
        else:
            model_stats.record_paid_success()
        return result

    # Ghi nhận fail
    if is_free:
        model_stats.record_free_fail()
    else:
        model_stats.record_paid_fail()

    # Nếu đang dùng free và thất bại → switch sang paid và thử lại
    if model_manager.is_using_free:
        model_manager.switch_to_paid(reason="rate limit hoặc lỗi API")
        paid = model_manager.current_model
        logger.info(f"🔁 Thử lại với model paid: {paid}")

        result = await _call_openrouter(
            headers, paid, system_prompt, user_message, max_tokens
        )
        if result is not None:
            model_stats.record_paid_success()
            return result
        else:
            model_stats.record_paid_fail()

    # Cả 2 model đều fail → dùng fallback
    logger.error("Cả 2 model đều thất bại, dùng fallback message")
    model_stats.record_fallback()
    return get_random_fallback()


async def _call_openrouter(
    headers: dict,
    model: str,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
) -> str | None:
    """
    Gọi OpenRouter API với 1 model cụ thể.
    Return reply text nếu thành công, None nếu thất bại.
    """
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
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                OPENROUTER_API_URL, json=payload, headers=headers
            )
            response.raise_for_status()
            data = response.json()

            raw_reply = data["choices"][0]["message"]["content"].strip()
            reply = _clean_ai_reply(raw_reply)

            # Nếu sau khi clean mà rỗng → dùng fallback
            if not reply:
                logger.warning(f"[{model}] AI reply empty after cleaning. Raw: {raw_reply[:100]}")
                return get_random_fallback()

            logger.info(f"[{model}] AI reply: {reply[:80]}...")
            return reply

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error(f"[{model}] API error {status_code}: {e.response.text[:200]}")

        # Rate limit → return None để trigger switch
        if status_code in RATE_LIMIT_CODES:
            logger.warning(f"[{model}] Rate limited ({status_code})!")
            return None

        # Lỗi khác → cũng return None
        return None

    except httpx.TimeoutException:
        logger.error(f"[{model}] API timeout")
        return None

    except Exception as e:
        logger.error(f"[{model}] Unexpected error: {e}")
        return None

