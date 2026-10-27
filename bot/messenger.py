"""
messenger.py — Gọi Facebook Send API.
Hỗ trợ nhiều page: dùng đúng token theo page_id.
Mọi request log đầy đủ, token được che trong log.
"""
import logging
import time

import httpx

import config
import database as db

logger = logging.getLogger("bot")

_TIMEOUT = 10.0  # giây
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    """Dùng chung một client để giữ kết nối tới Graph API, khỏi bắt tay TLS lại ở mỗi tin."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


def _masked_token(token: str) -> str:
    """Che phần giữa token trong log để bảo mật."""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "***"
    return token[:4] + "***" + token[-4:]


def _get_token(page_id: str) -> str:
    """Lấy access token phù hợp với page_id."""
    token = config.PAGE_TOKENS.get(str(page_id)) or config.PAGE_TOKENS.get("default", "")
    if not token:
        logger.warning("Không tìm thấy access token cho page_id=%s", page_id)
    return token


async def _post(page_id: str, payload: dict) -> dict | None:
    """Gọi Send API, log request/response, che token."""
    token = _get_token(page_id)
    if not token:
        logger.error("Không có token cho page_id=%s, bỏ qua gửi request", page_id)
        return None

    send_url = f"{config.GRAPH_API_BASE}/me/messages"
    headers = {"Content-Type": "application/json"}
    params  = {"access_token": token}

    log_params = {"access_token": _masked_token(token)}
    logger.info("Send API (page=%s) → %s params=%s body=%s", page_id, send_url, log_params, payload)

    try:
        started = time.monotonic()
        resp = await _http().post(send_url, json=payload, params=params, headers=headers)
        logger.info("Send API mất %.2fs", time.monotonic() - started)
        data = resp.json()
        if resp.status_code != 200:
            logger.error("Send API lỗi status=%d body=%s", resp.status_code, data)
        else:
            logger.info("Send API ← status=%d body=%s", resp.status_code, data)
        return data
    except httpx.TimeoutException:
        logger.error("Send API timeout sau %.1f giây cho page_id=%s", _TIMEOUT, page_id)
    except Exception as exc:
        logger.exception("Send API lỗi cho page_id=%s: %s", page_id, exc)
    return None


async def send_text(page_id: str, recipient_id: str, text: str) -> dict | None:
    """Gửi tin nhắn văn bản."""
    if len(text) > 1999:
        text = text[:1996] + "..."
    payload = {
        "messaging_type": "RESPONSE",
        "recipient": {"id": str(recipient_id)},
        "message": {"text": text},
    }
    return await _post(page_id, payload)


async def send_typing(page_id: str, recipient_id: str) -> None:
    """Bật typing indicator."""
    payload = {
        "recipient": {"id": str(recipient_id)},
        "sender_action": "typing_on",
    }
    await _post(page_id, payload)


async def send_image(page_id: str, recipient_id: str, image_url: str) -> dict | None:
    """Gửi ảnh. Lần đầu tải từ URL công khai HTTPS, các lần sau dùng lại attachment_id."""
    def payload(source: dict) -> dict:
        return {
            "messaging_type": "RESPONSE",
            "recipient": {"id": str(recipient_id)},
            "message": {"attachment": {"type": "image", "payload": source}},
        }

    attachment_id = db.get_attachment(page_id, image_url)
    if attachment_id:
        data = await _post(page_id, payload({"attachment_id": attachment_id}))
        if data and "error" not in data:
            return data
        db.delete_attachment(page_id, image_url)  # attachment hết dùng được → tải lại từ URL

    data = await _post(page_id, payload({"url": image_url, "is_reusable": True}))
    if data and data.get("attachment_id"):
        db.save_attachment(page_id, image_url, data["attachment_id"])
    return data
