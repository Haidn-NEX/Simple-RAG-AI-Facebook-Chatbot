"""
handler.py — Xử lý từng loại sự kiện Messenger.
Hỗ trợ nhiều page: nhận page_id để dùng đúng token.
"""
import asyncio
import logging
import re
import time

import config
import ai
import database as db
import kb
import messenger

logger = logging.getLogger("bot")

# Tập (page_id, psid) đang chờ nhân viên (bot tạm dừng trả lời trong các hội thoại này)
_human_mode: set[tuple[str, str]] = set()

# Tập mid đã xử lý (chống xử lý trùng theo spec Meta)
_seen_mids: set[str] = set()
_MAX_SEEN = 10_000

_MAX_IMAGES = 10  # số ảnh tối đa gửi trong một lượt trả lời


def _intro() -> str:
    """Lời tự giới thiệu là trợ lý tự động, gửi ở tin đầu tiên với mỗi khách."""
    if not config.SHEET_ID:
        return (
            "Xin chào! 🐾 Tôi là trợ lý tự động của Your Shop.\n"
            "Tôi có thể giúp bạn tìm sản phẩm, xem giá, đặt hàng thức ăn và phụ kiện cho thú cưng.\n"
            "Hỏi tôi bất cứ điều gì nhé!"
        )
    return (
        f"Dạ em chào anh/chị ạ 😊 Em là trợ lý tự động của {config.SHOP_NAME or 'shop'}, "
        "hỗ trợ tư vấn sản phẩm, báo giá và ghi nhận đơn hàng ạ."
    )


async def handle_event(event: dict, page_id: str = "default"):
    """Điểm vào cho mỗi messaging event từ webhook."""
    # ── message_echoes: nhân viên trả lời qua Meta Business Suite / Page Inbox ──
    if "message" in event and event.get("message", {}).get("is_echo"):
        msg = event["message"]
        app_id = msg.get("app_id")

        # Nếu echo là do chính bot này gửi (app_id trùng APP_ID) → bỏ qua
        if app_id and str(app_id) == str(config.APP_ID):
            return

        # Echo do nhân viên gửi từ Page Inbox hoặc ứng dụng khác
        # Trong echo event: sender là Page ID, recipient là khách hàng (PSID)!
        customer_id = event.get("recipient", {}).get("id")
        if customer_id:
            user_key = (page_id, str(customer_id))
            _human_mode.add(user_key)
            logger.info("Nhân viên trả lời trên page=%s — bot tạm dừng với khách PSID=%s", page_id, customer_id)
        return

    sender_id = event.get("sender", {}).get("id")
    if not sender_id:
        return
    sender_id = str(sender_id)

    # ── Tin nhắn thường từ khách ──────────────────────────────────────────────
    if "message" in event:
        await _handle_message(page_id, sender_id, event["message"])
        return

    # ── Postback (nút bấm) ────────────────────────────────────────────────────
    if "postback" in event:
        await _handle_message(page_id, sender_id, {"text": event["postback"].get("payload", "")})
        return


async def _handle_message(page_id: str, psid: str, message: dict):
    """Xử lý một tin nhắn từ khách hàng."""
    mid = message.get("mid", "")

    # Chống xử lý trùng
    if mid and mid in _seen_mids:
        logger.debug("mid đã xử lý, bỏ qua: %s", mid)
        return
    if mid:
        _seen_mids.add(mid)
        if len(_seen_mids) > _MAX_SEEN:
            _seen_mids.pop()

    text = (message.get("text") or "").strip()
    attachments = message.get("attachments", [])

    logger.info("Tin từ %s (page=%s): text=%r attachments=%d", psid, page_id, text[:60], len(attachments))

    user_key = (page_id, psid)

    # ── Bot đang ở chế độ nhân viên → im lặng ────────────────────────────────
    if user_key in _human_mode:
        if text.lower() in ("bot", "chatbot", "tự động", "auto"):
            _human_mode.discard(user_key)
            await messenger.send_text(page_id, psid, "Bot đã quay lại hỗ trợ bạn. Bạn cần giúp gì? 😊")
        return

    # ── Xóa dữ liệu (chính sách Meta & Privacy Policy) ───────────────────────
    if text.lower() in ("xóa dữ liệu", "xoa du lieu", "delete my data", "delete data"):
        db.delete_user(psid)
        await messenger.send_text(
            page_id, psid,
            "✅ Toàn bộ dữ liệu của bạn đã được xóa khỏi hệ thống. "
            "Chúng tôi không còn lưu bất kỳ thông tin nào liên quan đến tài khoản này."
        )
        logger.info("Đã xóa dữ liệu theo yêu cầu: %s (page=%s)", psid, page_id)
        return

    # ── Yêu cầu gặp nhân viên ────────────────────────────────────────────────
    if any(kw in text.lower() for kw in ("nhân viên", "nhan vien", "người thật", "staff", "human")):
        _human_mode.add(user_key)
        await messenger.send_text(
            page_id, psid,
            "Đã ghi nhận! Nhân viên sẽ tiếp tục hỗ trợ bạn trong thời gian sớm nhất. 🙏\n"
            "Trong giờ hành chính (8–17h), nhân viên thường phản hồi trong vòng 15 phút.\n"
            "Để quay lại bot tự động, nhắn \"bot\"."
        )
        return

    # ── Lần đầu gặp khách → tự giới thiệu ───────────────────────────────────
    # Lời chào chạy nền để khỏi chặn lượt gọi AI; luôn phải gửi xong trước tin trả lời.
    intro_task = None
    if db.is_new_user(psid):
        db.register_user(psid)
        intro_task = asyncio.create_task(messenger.send_text(page_id, psid, _intro()))

    # ── Tin nhắn có attachment (ảnh, voice, sticker...) ──────────────────────
    if attachments and not text:
        if intro_task:
            await intro_task
        await messenger.send_text(
            page_id, psid,
            "Cảm ơn bạn đã gửi file! 😊 Tôi chỉ xử lý được tin nhắn văn bản. "
            "Bạn muốn hỏi về sản phẩm hay đặt hàng gì không?"
        )
        return

    # ── Không có text ─────────────────────────────────────────────────────────
    if not text:
        if intro_task:
            await intro_task
        await messenger.send_text(page_id, psid, "Bạn muốn hỏi gì? Tôi sẵn sàng hỗ trợ! 😊")
        return

    # ── Gọi AI trả lời ────────────────────────────────────────────────────────
    # Typing và lời chào chạy song song với AI; độ trễ cộng thêm chỉ còn phần AI.
    started = time.monotonic()
    typing_task = asyncio.create_task(messenger.send_typing(page_id, psid))
    reply = await ai.get_reply(psid, text)
    ai_secs = time.monotonic() - started
    await asyncio.gather(typing_task, *([intro_task] if intro_task else []))

    # Trích mã sản phẩm từ cú pháp [IMAGE: mã]; URL ảnh lấy từ kho dữ liệu, không lấy từ AI
    image_refs = re.findall(r"\[IMAGE:\s*([^\]\s]+)\s*\]", reply)
    clean_reply = re.sub(r"\n{3,}", "\n\n", re.sub(r"\[IMAGE:[^\]]*\]", "", reply)).strip()

    image_urls: list[str] = []
    for ref in image_refs:
        product = kb.get_product(ref)
        url = product.get("image_url") if product else ""
        if url and url not in image_urls:
            image_urls.append(url)

    if not clean_reply and not image_urls:
        clean_reply = "Dạ mẫu này hiện em chưa có ảnh để gửi trực tiếp ạ."
    if clean_reply:
        await messenger.send_text(page_id, psid, clean_reply)

    text_secs = time.monotonic() - started
    # Gửi ảnh sản phẩm kèm theo (nếu khách hỏi xem ảnh)
    for img_url in image_urls[:_MAX_IMAGES]:
        await messenger.send_image(page_id, psid, img_url)
    logger.info("Thời gian lượt: AI=%.1fs, đến khi gửi xong chữ=%.1fs, tổng kể cả %d ảnh=%.1fs",
                ai_secs, text_secs, len(image_urls[:_MAX_IMAGES]), time.monotonic() - started)
