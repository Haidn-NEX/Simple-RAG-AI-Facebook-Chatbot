"""
main.py — FastAPI app: /health, GET /webhook, POST /webhook
Hỗ trợ nhiều page: nhận diện page_id theo entry[id] và phân phối đúng token.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

import config
import kb
from handler import handle_event

# ── Logging ──────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("bot")

if not logger.handlers:
    logger.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    _fh  = logging.handlers.RotatingFileHandler(
        "logs/bot.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)
    # stdout cho systemd journal
    _ch = logging.StreamHandler()
    _ch.setFormatter(_fmt)
    logger.addHandler(_ch)


# ── App Lifespan ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Bot khởi động — Graph API %s", config.GRAPH_API_VERSION)

    # Tự động truy vấn Page ID từ Meta cho các token chưa rõ page_id trong .env
    if config.UNMAPPED_TOKENS:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for token in config.UNMAPPED_TOKENS:
                try:
                    res = await client.get(
                        f"{config.GRAPH_API_BASE}/me",
                        params={"access_token": token, "fields": "id,name"},
                    )
                    if res.status_code == 200:
                        data = res.json()
                        pid = str(data.get("id"))
                        pname = data.get("name", "Unknown Page")
                        config.register_page_token(pid, token)
                        logger.info("Đã tự động nhận diện Page từ token: %r (Page ID: %s)", pname, pid)
                    else:
                        logger.warning("Không thể tra cứu Page từ token: status=%d body=%s", res.status_code, res.text)
                except Exception as exc:
                    logger.warning("Lỗi khi kết nối Meta tra cứu token: %s", exc)

    logger.info("Danh sách Page ID đã sẵn sàng: %s", list(config.PAGE_TOKENS.keys()))

    # Kho tri thức: nạp bản lưu trên đĩa trước (có ngay), rồi kéo bản mới từ Google Sheet
    kb.load_snapshot()
    await kb.refresh()
    refresher = asyncio.create_task(_kb_refresh_loop()) if config.SHEET_ID else None
    yield
    if refresher:
        refresher.cancel()
    logger.info("Bot dừng")


async def _kb_refresh_loop():
    """Định kỳ kéo dữ liệu mới từ Google Sheet: sửa Sheet xong bot tự cập nhật."""
    while True:
        await asyncio.sleep(config.KB_REFRESH_SECONDS)
        await kb.refresh()


app = FastAPI(title="Your Shop Bot", lifespan=lifespan)


# ── /health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": config.GRAPH_API_VERSION,
        "active_pages": [k for k in config.PAGE_TOKENS.keys() if k != "default"],
        "kb": kb.status(),
    }


# ── POST /admin/reload — cập nhật dữ liệu từ Sheet ngay, không chờ chu kỳ ────
@app.post("/admin/reload")
async def reload_kb(request: Request):
    token = request.headers.get("X-Admin-Token", "")
    if not config.ADMIN_TOKEN or not hmac.compare_digest(token.encode(), config.ADMIN_TOKEN.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")
    return await kb.refresh()


# ── GET /webhook — xác minh webhook (bước 7.2 & 8.1) ─────────────────────────
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None            = Query(None, alias="hub.mode"),
    hub_verify_token: str | None    = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None       = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == config.VERIFY_TOKEN:
        logger.info("Webhook xác minh thành công")
        return PlainTextResponse(hub_challenge or "")
    logger.warning("Xác minh webhook thất bại: mode=%s token=%s", hub_mode, hub_verify_token)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── POST /webhook — nhận sự kiện (bước 7.3) ──────────────────────────────────
def _verify_signature(body: bytes, sig_header: str | None) -> bool:
    """Xác thực chữ ký X-Hub-Signature-256."""
    if not sig_header or not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(
        config.APP_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    received = sig_header[len("sha256="):]
    return hmac.compare_digest(expected, received)


@app.post("/webhook")
async def receive_webhook(request: Request):
    body = await request.body()
    sig  = request.headers.get("X-Hub-Signature-256")

    # Meta yêu cầu trả HTTP 200 trong vòng 20 giây
    if not _verify_signature(body, sig):
        logger.warning("Chữ ký X-Hub-Signature-256 không khớp: %s", sig)
        # Vẫn trả 200 để Meta không retry liên tục; ghi log cảnh báo
        return Response(status_code=200)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Body webhook không phải JSON hợp lệ")
        return Response(status_code=200)

    logger.info("Nhận webhook payload: object=%s", payload.get("object"))

    # Xử lý bất đồng bộ trong background task để trả 200 ngay tức thì
    asyncio.create_task(_process_payload(payload))
    return Response(status_code=200)


async def _process_payload(payload: dict):
    """Duyệt qua từng entry và messaging event, gọi handle_event."""
    for entry in payload.get("entry", []):
        page_id = str(entry.get("id", "default"))  # ID của page nhận tin
        for event in entry.get("messaging", []):
            try:
                await handle_event(event, page_id)
            except Exception as exc:
                logger.exception("Lỗi xử lý event trên page %s: %s", page_id, exc)


# ── Chạy trực tiếp ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
