"""
database.py — SQLite: users, conversations, orders.
Dùng sqlite3 đồng bộ, thao tác nhanh gọn cho bot.
"""
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

logger = logging.getLogger("bot")

DB_PATH = Path("data/bot.db")


# ── Khởi tạo DB ──────────────────────────────────────────────────────────────
def _init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            psid       TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            psid       TEXT NOT NULL,
            role       TEXT NOT NULL,   -- 'user' | 'assistant'
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            psid        TEXT NOT NULL,
            order_code  TEXT NOT NULL,
            items       TEXT NOT NULL,  -- JSON
            name        TEXT,
            phone       TEXT,
            address     TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT NOT NULL
        );

        -- Ảnh sản phẩm đã tải lên Meta: gửi lại bằng attachment_id cho nhanh
        CREATE TABLE IF NOT EXISTS attachments (
            page_id       TEXT NOT NULL,
            url           TEXT NOT NULL,
            attachment_id TEXT NOT NULL,
            PRIMARY KEY (page_id, url)
        );
    """)
    conn.commit()
    conn.close()
    logger.info("DB khởi tạo tại %s", DB_PATH)


_init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(fn, *args):
    """Chạy hàm DB đồng bộ trong thread pool khi cần bất đồng bộ."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, partial(fn, *args))


# ── Users ─────────────────────────────────────────────────────────────────────
def is_new_user(psid: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute("SELECT 1 FROM users WHERE psid=?", (str(psid),)).fetchone()
    conn.close()
    return row is None


def register_user(psid: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO users (psid, created_at) VALUES (?,?)", (str(psid), _now()))
    conn.commit()
    conn.close()


def delete_user(psid: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM users WHERE psid=?", (str(psid),))
    conn.execute("DELETE FROM conversations WHERE psid=?", (str(psid),))
    conn.execute("DELETE FROM orders WHERE psid=?", (str(psid),))
    conn.commit()
    conn.close()
    logger.info("Đã xóa dữ liệu PSID=%s", psid)


# ── Conversations ─────────────────────────────────────────────────────────────
def save_message(psid: str, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO conversations (psid, role, content, created_at) VALUES (?,?,?,?)",
        (str(psid), role, content, _now()),
    )
    conn.commit()
    conn.close()


def get_history(psid: str, limit: int = 10) -> list[dict]:
    """Lấy lịch sử hội thoại gần nhất (theo thứ tự thời gian tăng dần)."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM conversations WHERE psid=? ORDER BY id DESC LIMIT ?",
        (str(psid), limit),
    ).fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


# ── Attachments ───────────────────────────────────────────────────────────────
def get_attachment(page_id: str, url: str) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row  = conn.execute(
        "SELECT attachment_id FROM attachments WHERE page_id=? AND url=?", (str(page_id), url)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def save_attachment(page_id: str, url: str, attachment_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO attachments (page_id, url, attachment_id) VALUES (?,?,?)",
        (str(page_id), url, str(attachment_id)),
    )
    conn.commit()
    conn.close()


def delete_attachment(page_id: str, url: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM attachments WHERE page_id=? AND url=?", (str(page_id), url))
    conn.commit()
    conn.close()


# ── Orders ────────────────────────────────────────────────────────────────────
def create_order(psid: str, items: list, name: str, phone: str, address: str) -> str:
    import random
    code = "FPA-" + str(random.randint(100000, 999999))
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO orders (psid, order_code, items, name, phone, address, created_at) VALUES (?,?,?,?,?,?,?)",
        (str(psid), code, json.dumps(items, ensure_ascii=False), name, phone, address, _now()),
    )
    conn.commit()
    conn.close()
    logger.info("Đơn mới: %s PSID=%s", code, psid)
    return code
