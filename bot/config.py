"""
config.py — Đọc biến môi trường từ .env
Hỗ trợ một hoặc nhiều Facebook Page cùng lúc.
"""
import os
from dotenv import load_dotenv

load_dotenv()

APP_ID             = os.getenv("APP_ID", "").strip()
APP_SECRET         = os.getenv("APP_SECRET", "").strip()
VERIFY_TOKEN       = os.getenv("VERIFY_TOKEN", "").strip()
PAGE_ACCESS_TOKEN  = os.getenv("PAGE_ACCESS_TOKEN", "").strip()
GRAPH_API_VERSION  = os.getenv("GRAPH_API_VERSION", "v21.0").strip()
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "").strip()
PORT               = int(os.getenv("PORT", "8000"))

# Kho tri thức (RAG): Google Sheet chứa tab prompt, data, knowledge
SHEET_ID           = os.getenv("SHEET_ID", "").strip()
KB_REFRESH_SECONDS = int(os.getenv("KB_REFRESH_SECONDS") or "300")
KB_FULL_CONTEXT_CHARS = int(os.getenv("KB_FULL_CONTEXT_CHARS") or "30000")  # dài hơn ngưỡng này → truy xuất
EMBED_MODEL        = os.getenv("EMBED_MODEL", "").strip()   # để trống = chỉ tìm theo từ khóa
ADMIN_TOKEN        = os.getenv("ADMIN_TOKEN", "").strip()   # bảo vệ POST /admin/reload
SHOP_NAME          = os.getenv("SHOP_NAME", "").strip()
# Mức "suy nghĩ" của Gemini: minimal | low | medium | high; "off" = không gửi tham số, để model tự quyết.
GEMINI_THINKING    = os.getenv("GEMINI_THINKING", "low").strip().lower()
# Chính chưa trả lời sau chừng này giây thì gọi model dự phòng song song (lấy kết quả về trước).
GEMINI_HEDGE_SECONDS = float(os.getenv("GEMINI_HEDGE_SECONDS") or "12")

GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Bảng tra token theo page_id: { "100000...": "EAAP...", ... }
PAGE_TOKENS: dict[str, str] = {}


def register_page_token(page_id: str, token: str):
    """Đăng ký cặp page_id và token vào bộ nhớ."""
    if page_id and token:
        PAGE_TOKENS[str(page_id).strip()] = token.strip()


# Tự động nạp mọi PAGE_ID / PAGE_ACCESS_TOKEN từ môi trường
# Hỗ trợ: PAGE_ID + PAGE_ACCESS_TOKEN, PAGE_ID2 + PAGE_ACCESS_TOKEN2, PAGE_ID_X + PAGE_ACCESS_TOKEN_X
for key, val in os.environ.items():
    if not val or not val.strip():
        continue
    if key == "PAGE_ACCESS_TOKEN":
        suffix = ""
    elif key.startswith("PAGE_ACCESS_TOKEN"):
        suffix = key[len("PAGE_ACCESS_TOKEN"):]
    else:
        continue

    matching_page_id = os.getenv(f"PAGE_ID{suffix}")
    if matching_page_id and matching_page_id.strip():
        register_page_token(matching_page_id, val)

# Luôn gắn fallback default vào PAGE_ACCESS_TOKEN chính nếu có
if PAGE_ACCESS_TOKEN:
    PAGE_TOKENS.setdefault("default", PAGE_ACCESS_TOKEN)

# Danh sách token chưa được ghép cặp với Page ID nào
# (để main.py tự động gọi Meta /me tra cứu khi server khởi động)
UNMAPPED_TOKENS: list[str] = []
for key, val in os.environ.items():
    if key.startswith("PAGE_ACCESS_TOKEN") and val.strip():
        token = val.strip()
        if token not in PAGE_TOKENS.values():
            UNMAPPED_TOKENS.append(token)
