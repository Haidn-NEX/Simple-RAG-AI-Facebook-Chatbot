"""
ai.py — Gọi Google Gemini API (google-genai SDK) để tạo câu trả lời.
Prompt = luật nền từ Sheet + dữ liệu truy xuất từ kb.py cho đúng lượt chat này; có timeout và fallback.
"""
import asyncio
import logging
import re
import time

from google import genai
from google.genai import errors, types

import config
import database as db
import kb

logger = logging.getLogger("bot")

_TIMEOUT = 25.0  # giây — phải trả lời trong 30 giây theo policy Meta
# Model chính trước, model dự phòng sau (tên lấy từ models.list() của tài khoản ngày 11/10/2026).
_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash")
_ATTEMPT_TIMEOUTS = (18.0, 15.0)  # giây cho từng model; chạy chồng nhau nên _TIMEOUT mới là hạn cuối
# SDK cũ không có ThinkingLevel / thinking_level: khi đó bỏ qua tham số, không để lỗi import làm sập bot.
_SDK_THINKING = hasattr(types, "ThinkingLevel") and "thinking_level" in types.ThinkingConfig.model_fields
_thinking_ok = _SDK_THINKING  # đổi thành False khi model từ chối thinking_level
# Hạn mức này tính cả token "suy nghĩ" của model; đặt thấp thì câu trả lời bị cắt cụt giữa chừng.
_MAX_OUTPUT_TOKENS = 4096
_RETRY_CODES = {404, 429, 500, 503, 504}

# Dùng khi không có prompt từ Sheet (chế độ products.json)
_DEFAULT_PROMPT = """Bạn là trợ lý bán hàng tự động của cửa hàng Your Shop.
Nhiệm vụ: tư vấn sản phẩm thú cưng (thức ăn, phụ kiện, đồ dùng) và nhận đơn hàng.

Quy tắc bắt buộc:
1. CHỈ trả lời dựa trên dữ liệu sản phẩm được cung cấp bên dưới. Không tự bịa giá hay sản phẩm không có trong danh mục.
2. Nếu khách hỏi sản phẩm không có trong danh mục, nói thật là hiện chưa có và đề xuất sản phẩm gần nhất.
3. Trả lời ngắn gọn, thân thiện, dùng tiếng Việt.
4. Khi khách muốn đặt hàng, thu thập: tên người nhận, số điện thoại, địa chỉ giao hàng, sản phẩm và số lượng, rồi tóm tắt để khách kiểm tra lại.
5. Không yêu cầu thông tin thẻ ngân hàng — chỉ hỗ trợ COD (thanh toán khi nhận hàng).
6. Không thảo luận chủ đề ngoài phạm vi cửa hàng.
7. Mỗi câu trả lời tối đa 300 từ."""

# Prompt trong Sheet viết cho n8n (gọi tool, chèn URL ảnh). Khối này nối nó với backend hiện tại.
_ADAPTER = """# GHI CHÚ HỆ THỐNG — ưu tiên hơn nếu mâu thuẫn với phần trên

- Ở hệ thống này bạn không gọi tool. Kết quả tra `data` và `knowledge` cho lượt này đã nằm sẵn trong các khối bên dưới; coi đó là kết quả tool.
- "DANH MỤC SẢN PHẨM" là toàn bộ sản phẩm shop đang bán. Sản phẩm không có trong đó nghĩa là shop chưa có.
- Thuộc tính nào của sản phẩm không được ghi ra nghĩa là chưa có dữ liệu: không đoán, nói cần kiểm tra lại.
- Gửi ảnh: KHÔNG viết URL. Mỗi ảnh viết một dòng riêng dạng [IMAGE: mã sản phẩm], ví dụ [IMAGE: P001]; hệ thống sẽ gửi ảnh thật. Chỉ dùng cho sản phẩm được ghi là có ảnh; mẫu chưa có ảnh thì nói chưa có ảnh.
- Ngoài cú pháp [IMAGE: ...], không nhắc mã sản phẩm hay mã kịch bản với khách.
- Bạn là trợ lý tự động của shop. Khách hỏi có phải người thật không thì nói thật. Khách muốn gặp người thật thì hướng dẫn nhắn "nhân viên".
- Hệ thống chưa tự tạo đơn hay báo cho ai: chỉ nói đã ghi nhận thông tin, không nói đã lên đơn.
- Messenger không hiển thị markdown: KHÔNG dùng dấu * hay ** để in đậm, không dùng # hay dấu `. Viết chữ thường, xuống dòng và dùng "•" cho danh sách."""

_FIRST_TURN = "Hệ thống vừa tự gửi lời chào và giới thiệu trợ lý tự động cho khách. Không chào lại, trả lời thẳng vào tin nhắn."

_FALLBACK = (
    "Xin lỗi, tôi đang gặp sự cố kỹ thuật. "
    "Bạn có thể nhắn lại sau vài giây, hoặc nhắn \"nhân viên\" để được hỗ trợ trực tiếp. 🙏"
)

_BUSY = (
    "Dạ hiện bên em đang có nhiều khách nhắn cùng lúc nên chưa trả lời kịp ạ. "
    "Anh/chị nhắn lại sau ít phút giúp em, hoặc nhắn \"nhân viên\" để được hỗ trợ trực tiếp ạ. 🙏"
)


class QuotaExceeded(RuntimeError):
    """Gemini trả 429: hết hạn mức (số request hoặc token) của project."""


_client: genai.Client | None = None

_BOLD   = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_BULLET = re.compile(r"^[ \t]*[*-][ \t]+", re.MULTILINE)
_HEADER = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)


def plain_text(text: str) -> str:
    """Bỏ định dạng markdown: Messenger hiển thị nguyên dấu *, #, ` cho khách."""
    text = _BULLET.sub("• ", text)
    text = _HEADER.sub("", text)
    text = _BOLD.sub(r"\2", text)
    text = _ITALIC.sub(r"\1", text)
    return text.replace("`", "")


def _build_system(products: list[dict], rules: list[dict]) -> str:
    """Phần ít đổi đặt trước, phần đổi theo từng lượt đặt sau — để Gemini cache được phần đầu."""
    parts = [kb.prompt() or _DEFAULT_PROMPT, _ADAPTER, kb.static_text()]
    if products:
        parts.append("=== CHI TIẾT SẢN PHẨM LIÊN QUAN LƯỢT NÀY ===\n" + kb.format_products(products))
    if rules:
        parts.append("=== KỊCH BẢN LIÊN QUAN LƯỢT NÀY ===\n" + kb.format_rules(rules))
    return "\n\n".join(parts)


async def get_reply(psid: str, user_text: str) -> str:
    """Gọi Gemini, trả về chuỗi câu trả lời. Fallback nếu lỗi hoặc timeout."""
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY chưa đặt trong .env")
        return _FALLBACK
    if kb.is_empty():
        logger.error("Kho tri thức rỗng — chưa đồng bộ được dữ liệu sản phẩm")
        return _FALLBACK

    t0 = time.monotonic()
    # Lưu tin nhắn của khách vào DB
    db.save_message(psid, "user", user_text)

    # Lấy lịch sử hội thoại gần nhất
    history = db.get_history(psid, limit=12)
    logger.info("DB đọc/ghi lịch sử: %.2fs", time.monotonic() - t0)

    try:
        reply = await asyncio.wait_for(_answer(history, user_text), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error("Gemini timeout sau %.1f giây cho PSID=%s", _TIMEOUT, psid)
        return _FALLBACK
    except QuotaExceeded:
        logger.error("Gemini hết hạn mức (429) ở mọi model — trả câu báo bận cho PSID=%s", psid)
        return _BUSY
    except Exception as exc:
        logger.exception("Gemini lỗi: %s", exc)
        return _FALLBACK
    if not reply:
        logger.error("Gemini trả về rỗng cho PSID=%s", psid)
        return _FALLBACK

    # Làm sạch trước khi lưu: lịch sử sạch thì model cũng bớt dùng markdown ở các lượt sau
    reply = plain_text(reply)
    db.save_message(psid, "assistant", reply)
    return reply


async def _answer(history: list[dict], user_text: str) -> str:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)

    # Hai tin ngay trước làm ngữ cảnh truy xuất, để "mẫu đó giá sao?" vẫn tìm ra đúng mẫu
    context = " ".join((m.get("content") or "") for m in history[-3:-1])
    t0 = time.monotonic()
    products, rules = await kb.search(user_text, context)
    logger.info("KB truy xuất: sản phẩm=%s kịch bản=%s (%.2fs)",
                [p["id"] for p in products], [r["id"] for r in rules], time.monotonic() - t0)

    # Xây dựng nội dung hội thoại theo định dạng SDK google-genai
    contents = []
    for msg in history[:-1]:  # bỏ tin cuối (chính là user_text vừa lưu)
        text_part = (msg.get("content") or "").strip()
        if not text_part:
            continue
        role = "user" if msg.get("role") == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=text_part)]))

    # Thêm tin nhắn hiện tại của khách
    # Ghi chú lượt đầu nằm ở tin khách, không ở system prompt: system giống hệt giữa các lượt thì cache ngầm mới trúng.
    note = f"[Hệ thống: {_FIRST_TURN}]\n\n" if len(history) <= 1 else ""
    contents.append(types.Content(role="user", parts=[types.Part(text=note + user_text.strip())]))

    system = _build_system(products, rules)
    return await _race(contents, system)


def _thinking() -> types.ThinkingConfig | None:
    level = config.GEMINI_THINKING.upper()
    if not _thinking_ok or level not in ("MINIMAL", "LOW", "MEDIUM", "HIGH"):
        return None
    return types.ThinkingConfig(thinking_level=getattr(types.ThinkingLevel, level))


async def _call(model: str, limit: float, contents: list, system: str) -> str:
    """Một lần gọi một model, có hạn giờ riêng. Lỗi hoặc quá hạn thì ném ngoại lệ."""
    global _thinking_ok
    for attempt in (0, 1):
        thinking = _thinking()
        gen_config = types.GenerateContentConfig(
            system_instruction=system, max_output_tokens=_MAX_OUTPUT_TOKENS, thinking_config=thinking)
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                _client.aio.models.generate_content(model=model, contents=contents, config=gen_config),
                timeout=limit)
        except errors.APIError as exc:
            if exc.code == 400 and thinking and attempt == 0:
                # Model không nhận tham số thinking: bỏ nó đi và thử lại ngay, không tính là lỗi model.
                _thinking_ok = False
                logger.warning("Gemini %s từ chối thinking_level (%s) — gọi lại không kèm tham số", model, exc.message)
                continue
            raise
        cand = response.candidates[0] if response.candidates else None
        usage = response.usage_metadata
        logger.info("Gemini xong: model=%s, %.1fs, finish=%s, token vào=%s (đã cache=%s), token đầu ra=%s, token suy nghĩ=%s",
                    model, time.monotonic() - started,
                    getattr(cand, "finish_reason", None),
                    getattr(usage, "prompt_token_count", None),
                    getattr(usage, "cached_content_token_count", None),
                    getattr(usage, "candidates_token_count", None),
                    getattr(usage, "thoughts_token_count", None))
        return (response.text or "").strip()
    raise RuntimeError("không gọi được Gemini")


async def _race(contents: list, system: str) -> str:
    """Gọi model chính; nếu lỗi/quá tải thì gọi ngay model dự phòng, nếu chỉ chậm hơn
    GEMINI_HEDGE_SECONDS thì gọi dự phòng song song. Kết quả nào về trước (không rỗng) thắng."""
    tasks: dict[asyncio.Task, str] = {}

    def start(i: int) -> asyncio.Task:
        task = asyncio.create_task(_call(_MODELS[i], _ATTEMPT_TIMEOUTS[i], contents, system))
        tasks[task] = _MODELS[i]
        return task

    next_i, pending, quota = 1, {start(0)}, False
    try:
        while pending:
            hedge = config.GEMINI_HEDGE_SECONDS if next_i < len(_MODELS) else None
            done, pending = await asyncio.wait(pending, timeout=hedge, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc is None and task.result():
                    return task.result()
                reason = "trả về rỗng" if exc is None else (
                    "quá hạn" if isinstance(exc, asyncio.TimeoutError) else f"lỗi: {exc}")
                quota = quota or (isinstance(exc, errors.APIError) and exc.code == 429)
                logger.warning("Gemini %s %s", tasks[task], reason)
            if next_i < len(_MODELS):
                logger.info("Gemini: gọi thêm %s song song (%s)", _MODELS[next_i],
                            "chính chậm" if not done else "chính lỗi")
                pending.add(start(next_i))
                next_i += 1
        if quota:
            raise QuotaExceeded("hết hạn mức Gemini")
        raise RuntimeError("tất cả model Gemini đều chậm hoặc quá tải")
    finally:
        for task in tasks:
            task.cancel()
