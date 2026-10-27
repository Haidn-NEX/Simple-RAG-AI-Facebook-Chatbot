"""
kb.py — Kho tri thức của bot (RAG).
Nguồn: Google Sheet (tab prompt, data, knowledge), đồng bộ định kỳ vào bộ nhớ.
Dữ liệu còn nhỏ (dưới KB_FULL_CONTEXT_CHARS) thì đưa trọn vào prompt: không sót, phần đầu prompt cố định nên Gemini cache được.
Dữ liệu lớn hơn ngưỡng thì mỗi lượt chat chỉ truy xuất vài sản phẩm / kịch bản liên quan.
Không có SHEET_ID thì dùng products.json như cũ.
"""
import asyncio
import csv
import hashlib
import io
import json
import logging
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

import config

logger = logging.getLogger("bot")

SNAPSHOT_PATH = Path("data/kb_snapshot.json")
PRODUCTS_PATH = Path("products.json")

_SHEET_URL     = "https://docs.google.com/spreadsheets/d/{id}"
_TABS          = ("prompt", "data", "knowledge")
_TOP_PRODUCTS  = 6     # số sản phẩm đưa chi tiết vào prompt mỗi lượt
_TOP_RULES     = 3     # số kịch bản truy xuất thêm (ngoài nhóm luôn áp dụng)
_CONTEXT_W     = 0.4   # trọng số của ngữ cảnh hội thoại so với tin hiện tại
_EMBED_DIM     = 768
_EMBED_TIMEOUT = 2.0   # giây — quá thời gian thì chỉ dùng tìm theo từ khóa
_PRIORITY      = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}

# Từ đệm trong câu chat (đã bỏ dấu) — bỏ khỏi câu truy vấn để không kéo về kết quả nhiễu
_STOPWORDS = set(
    "a ah oi da vang em anh chi ban minh shop ben cho co khong ko k la thi ma nhe nha nhi "
    "the nao gi sao bao nhieu may cai loai mau xem them gui lay mua can muon duoc roi va voi".split()
)

_PRODUCT_FIELDS = [
    ("category", "Nhóm"), ("material", "Chất liệu"), ("size", "Kích thước"),
    ("length", "Dài"), ("width", "Rộng"), ("color", "Màu"), ("packaging", "Đóng gói"),
    ("retail_price", "Giá lẻ"), ("wholesale_price", "Giá sỉ"),
    ("min_order_qty", "Mua tối thiểu"), ("use_case", "Công dụng"),
    ("water_resistant", "Chống nước"), ("notes", "Ghi chú bán"),
]


# ── Chuẩn hóa tiếng Việt ─────────────────────────────────────────────────────
def _norm(text: str) -> str:
    """Chữ thường, bỏ dấu — để khách gõ không dấu vẫn tìm ra."""
    text = unicodedata.normalize("NFD", (text or "").lower()).replace("đ", "d")
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _tokens(text: str, query: bool = False) -> list[str]:
    """Từ đơn + cặp từ liền nhau (tiếng Việt hay là từ ghép: "ruy băng", "giấy gói")."""
    words = re.findall(r"[a-z0-9]+", _norm(text))
    pairs = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    if query:
        words = [w for w in words if w not in _STOPWORDS]
    return words + pairs


class _Bm25:
    """Chỉ mục BM25 trong bộ nhớ, đủ nhanh cho vài nghìn dòng."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf   = [Counter(d) for d in docs]
        self.size = [len(d) for d in docs]
        self.avg  = (sum(self.size) / len(docs)) if docs else 1.0
        df = Counter(t for tf in self.tf for t in tf)
        n  = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = [0.0] * len(self.tf)
        for term in set(query):
            idf = self.idf.get(term)
            if not idf:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(term)
                if f:
                    norm = self.k1 * (1 - self.b + self.b * self.size[i] / self.avg)
                    out[i] += idf * f * (self.k1 + 1) / (f + norm)
        return out


# ── Đọc dòng từ Sheet ────────────────────────────────────────────────────────
def _direct_image_url(url: str) -> str:
    """Link chia sẻ Google Drive → link ảnh trực tiếp mà Messenger tải được."""
    url = (url or "").strip()
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:[^#]*&)?id=)([\w-]+)", url)
    if m:
        return f"https://lh3.googleusercontent.com/d/{m.group(1)}=w1600"
    return url if url.startswith("https://") else ""


def _rows(csv_text: str) -> list[dict]:
    rows = csv.DictReader(io.StringIO(csv_text))
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


def _parse_products(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        pid, name = r.get("product_id", ""), r.get("product_name", "")
        if not pid or not name or pid in seen or r.get("status", "").upper() != "APPROVED":
            continue
        seen.add(pid)
        out.append({
            "id": pid, "name": name,
            "category": r.get("category", ""), "keywords": r.get("search_keywords", ""),
            "material": r.get("material", ""), "size": r.get("size", ""),
            "length": r.get("length", ""), "width": r.get("width", ""),
            "color": r.get("color", ""), "packaging": r.get("packaging", ""),
            "retail_price": r.get("retail_price", ""), "wholesale_price": r.get("wholesale_price", ""),
            "min_order_qty": r.get("min_order_qty", ""), "use_case": r.get("use_case", ""),
            "water_resistant": r.get("water_resistant", ""), "notes": r.get("selling_notes", ""),
            "availability": r.get("availability_status", "").upper(),
            "image_url": _direct_image_url(r.get("image_url", "")),
        })
    return out


def _parse_rules(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not r.get("knowledge_id") or r.get("status", "").upper() != "APPROVED":
            continue
        out.append({
            "id": r["knowledge_id"], "type": r.get("type", "").lower(),
            "scenario": r.get("scenario", ""), "keywords": r.get("keywords", ""),
            "priority": r.get("priority", "NORMAL").upper() or "NORMAL",
            "instruction": r.get("runtime_instruction", ""),
            "sample": r.get("runtime_sample_response", ""),
            "hard": r.get("hard_rule", "").upper() == "YES",
            "needs_backend": r.get("requires_n8n_logic", "").upper() == "YES",
        })
    return out


def _local_products() -> list[dict]:
    """products.json (chế độ không dùng Sheet) → cùng cấu trúc với dòng trong Sheet."""
    with open(PRODUCTS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return [{
        "id": p["id"], "name": p["name"], "category": p.get("category", ""),
        "retail_price": f"{p['price']:,}đ/{p['unit']}", "use_case": p.get("description", ""),
        "availability": "", "image_url": _direct_image_url(p.get("image_url", "")),
    } for p in raw]


# ── Kho tri thức đang dùng ───────────────────────────────────────────────────
def _product_tokens(p: dict) -> list[str]:
    rest = " ".join(p.get(k, "") for k in ("keywords", "material", "size", "color", "packaging", "use_case", "notes"))
    return _tokens(p["name"]) * 3 + _tokens(p.get("category", "")) * 2 + _tokens(rest)


def _rule_tokens(r: dict) -> list[str]:
    return _tokens(f"{r['scenario']} {r['keywords']}") * 2 + _tokens(f"{r['instruction']} {r['sample']}")


def _product_embed_text(p: dict) -> str:
    return " | ".join(p.get(k, "") for k in ("name", "category", "keywords", "material", "color", "use_case") if p.get(k))


def _rule_embed_text(r: dict) -> str:
    return " | ".join(x for x in (r["scenario"], r["keywords"], r["instruction"]) if x)


def _vkey(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class _KB:
    def __init__(self, prompt: str, products: list[dict], rules: list[dict],
                 vectors: dict | None = None, fingerprint: str = "", synced_at: str = ""):
        self.prompt, self.products, self.rules = prompt, products, rules
        self.vectors     = vectors or {}      # sha1(nội dung) → vector embedding
        self.fingerprint = fingerprint
        self.synced_at   = synced_at
        self.by_id       = {_norm(p["id"]): p for p in products}

        # Luật nền, cấu hình shop và luật CRITICAL áp dụng cho mọi hội thoại → luôn có mặt.
        rules = sorted(rules, key=lambda r: _PRIORITY.get(r["priority"], 2))
        self.always_rules = [r for r in rules if r["type"] in ("rule", "config") or r["priority"] == "CRITICAL"]
        self.search_rules = [r for r in rules if r not in self.always_rules]

        self._p_index = _Bm25([_product_tokens(p) for p in products])
        self._r_index = _Bm25([_rule_tokens(r) for r in self.search_rules])
        self._p_names = [_norm(p["name"]).strip() for p in products]

        # Dữ liệu nhỏ → đưa trọn vào prompt; lớn hơn ngưỡng → truy xuất theo từng lượt.
        all_products, all_rules = format_products(products), format_rules(rules)
        self.full_products = len(all_products) <= config.KB_FULL_CONTEXT_CHARS
        self.full_rules    = len(all_rules) <= config.KB_FULL_CONTEXT_CHARS
        if self.full_products:
            product_block = "=== DANH MỤC SẢN PHẨM (đầy đủ chi tiết) ===\n" + all_products
        else:
            product_block = "=== DANH MỤC SẢN PHẨM (mã, tên, giá lẻ) ===\n" + self._build_catalog()
        if self.full_rules:
            rule_block = "=== LUẬT & KỊCH BẢN BÁN HÀNG ===\n" + all_rules
        else:
            rule_block = "=== LUẬT LUÔN ÁP DỤNG ===\n" + format_rules(self.always_rules)
        self.static_text = product_block + ("\n\n" + rule_block if rules else "")

    def _build_catalog(self) -> str:
        groups: dict[str, list[str]] = {}
        titles: dict[str, str] = {}
        for p in self.products:
            key = _norm(p.get("category", "")) or "khac"
            titles.setdefault(key, p.get("category") or "Khác")
            price = p.get("retail_price") or "chưa có giá"
            photo = " — có ảnh" if p.get("image_url") else ""
            groups.setdefault(key, []).append(f"{p['id']} {p['name'].strip()} — {price}{photo}")
        return "\n".join(f"[{titles[k]}]\n" + "\n".join(v) for k, v in groups.items())

    def to_snapshot(self) -> dict:
        return {
            "fingerprint": self.fingerprint, "synced_at": self.synced_at, "prompt": self.prompt,
            "products": self.products, "rules": self.rules, "vectors": self.vectors,
        }


# ── Định dạng đưa vào prompt ─────────────────────────────────────────────────
def format_products(products: list[dict]) -> str:
    blocks = []
    for p in products:
        lines = [f"[{p['id']}] {p['name'].strip()}"]
        lines += [f"  {label}: {p[key]}" for key, label in _PRODUCT_FIELDS if p.get(key)]
        stock = "đã xác nhận còn hàng" if p.get("availability") == "CONFIRMED" else "chưa xác nhận (không khẳng định còn hàng)"
        lines.append(f"  Tồn kho: {stock}")
        lines.append(f"  Ảnh: {'có' if p.get('image_url') else 'chưa có'}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def format_rules(rules: list[dict]) -> str:
    blocks = []
    for r in rules:
        tags = [r["id"], r["priority"]] + (["luật cứng"] if r["hard"] else [])
        lines = [f"[{' | '.join(tags)}] {r['scenario']}"]
        if r["instruction"]:
            lines.append(f"  Hướng dẫn: {r['instruction']}")
        if r["sample"]:
            lines.append(f"  Câu mẫu: {r['sample']}")
        if r["needs_backend"]:
            lines.append("  Lưu ý: hệ thống chưa tự làm hành động kèm theo (hẹn giờ, gắn tag, báo chủ shop) — không nói là đã làm.")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


_kb = _KB("", [], [])


# ── Truy cập từ các module khác ──────────────────────────────────────────────
def prompt() -> str:
    return _kb.prompt


def static_text() -> str:
    """Phần dữ liệu có mặt trong mọi lượt chat (không đổi cho tới lần đồng bộ sau)."""
    return _kb.static_text


def is_empty() -> bool:
    return not _kb.products


def get_product(product_id: str) -> dict | None:
    return _kb.by_id.get(_norm(product_id))


def status() -> dict:
    return {
        "source": "sheet" if config.SHEET_ID else "products.json",
        "products": len(_kb.products), "rules": len(_kb.rules),
        "products_mode": "full" if _kb.full_products else "retrieval",
        "rules_mode": "full" if _kb.full_rules else "retrieval",
        "embeddings": bool(_kb.vectors and config.EMBED_MODEL), "synced_at": _kb.synced_at,
    }


# ── Embedding (tùy chọn, bật bằng EMBED_MODEL) ───────────────────────────────
_client = None


async def _embed(texts: list[str], task_type: str) -> list[list[float]]:
    global _client
    from google import genai
    from google.genai import types

    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    out: list[list[float]] = []
    for i in range(0, len(texts), 50):
        res = await _client.aio.models.embed_content(
            model=config.EMBED_MODEL,
            contents=texts[i:i + 50],
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=_EMBED_DIM),
        )
        out += [[round(v, 5) for v in e.values] for e in res.embeddings]
    return out


async def _embed_docs(products: list[dict], rules: list[dict], cached: dict) -> dict:
    """Chỉ embed những dòng mới hoặc vừa sửa; dòng cũ lấy lại từ cache."""
    if not (config.EMBED_MODEL and config.GEMINI_API_KEY):
        return {}
    texts   = [_product_embed_text(p) for p in products] + [_rule_embed_text(r) for r in rules]
    vectors = {_vkey(t): cached[_vkey(t)] for t in texts if _vkey(t) in cached}
    missing = [t for t in dict.fromkeys(texts) if _vkey(t) not in vectors]
    if missing:
        try:
            for text, vec in zip(missing, await _embed(missing, "RETRIEVAL_DOCUMENT")):
                vectors[_vkey(text)] = vec
            logger.info("KB: đã embed %d dòng mới/sửa", len(missing))
        except Exception as exc:
            logger.warning("KB: embed thất bại (%s) — các dòng đó chỉ tìm theo từ khóa", exc)
    return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ── Truy xuất ────────────────────────────────────────────────────────────────
def _rank(index: _Bm25, docs: list[dict], embed_text, query: str, context: str,
          qvec: list[float] | None, vectors: dict, top_k: int, names: list[str] | None = None) -> list[dict]:
    lex = index.scores(_tokens(query, query=True))
    if context:
        lex = [a + _CONTEXT_W * b for a, b in zip(lex, index.scores(_tokens(context, query=True)))]
    if names:
        # Khách gõ đúng tên sản phẩm → đẩy sản phẩm đó lên đầu.
        nq, top = _norm(query), max(lex, default=0.0) or 1.0
        lex = [s + top if n and n in nq else s for s, n in zip(lex, names)]

    top = max(lex, default=0.0)
    if qvec:
        lex   = [s / top for s in lex] if top else lex
        final = [0.5 * s + 0.5 * max(_cosine(qvec, vectors.get(_vkey(embed_text(d)), [])), 0.0)
                 for s, d in zip(lex, docs)]
        floor = 0.0
    else:
        final, floor = lex, top * 0.25   # cắt đuôi các kết quả khớp quá yếu

    order = sorted(range(len(docs)), key=lambda i: final[i], reverse=True)
    return [docs[i] for i in order[:top_k] if final[i] > floor]


async def search(query: str, context: str = "") -> tuple[list[dict], list[dict]]:
    """Trả về (sản phẩm liên quan, kịch bản liên quan) cho tin nhắn `query`.
    Phần nào đã nằm trọn trong static_text thì trả về danh sách rỗng."""
    kb = _kb
    if kb.full_products and kb.full_rules:
        return [], []
    qvec = None
    if kb.vectors and config.EMBED_MODEL:
        try:
            qvec = (await asyncio.wait_for(_embed([query], "RETRIEVAL_QUERY"), _EMBED_TIMEOUT))[0]
        except Exception as exc:
            logger.warning("KB: embed câu hỏi thất bại (%s) — dùng tìm theo từ khóa", exc)

    products = [] if kb.full_products else _rank(
        kb._p_index, kb.products, _product_embed_text, query, context,
        qvec, kb.vectors, _TOP_PRODUCTS, kb._p_names)
    rules = [] if kb.full_rules else _rank(
        kb._r_index, kb.search_rules, _rule_embed_text, query, context,
        qvec, kb.vectors, _TOP_RULES)
    return products, rules


# ── Đồng bộ ──────────────────────────────────────────────────────────────────
def load_snapshot() -> bool:
    """Nạp bản lưu gần nhất từ đĩa để bot trả lời được ngay cả khi Google Sheet không truy cập được."""
    global _kb
    if not config.SHEET_ID:
        _kb = _KB("", _local_products(), [])
        return True
    try:
        snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        _kb = _KB(snap["prompt"], snap["products"], snap["rules"],
                  snap.get("vectors"), snap.get("fingerprint", ""), snap.get("synced_at", ""))
        logger.info("KB: nạp bản lưu %s — %d sản phẩm, %d kịch bản", _kb.synced_at, len(_kb.products), len(_kb.rules))
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.warning("KB: không đọc được bản lưu (%s)", exc)
        return False


async def _fetch_tabs() -> dict[str, str]:
    base = _SHEET_URL.format(id=config.SHEET_ID)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        # Tra gid theo tên tab: xuất CSV theo gid giữ nguyên mọi ô
        # (kiểu gviz theo tên tab làm rỗng ô chữ nằm trong cột số).
        html = (await client.get(f"{base}/htmlview")).text
        gids = dict(re.findall(r'items\.push\(\{name: "([^"]+)".*?gid: "(\d+)"', html))
        missing = [t for t in _TABS if t not in gids]
        if missing:
            raise RuntimeError(f"không thấy tab {missing} — kiểm tra tên tab và quyền 'Bất kỳ ai có đường liên kết'")

        async def tab(name: str) -> str:
            resp = await client.get(f"{base}/export", params={"format": "csv", "gid": gids[name]})
            if resp.status_code != 200 or "text/csv" not in resp.headers.get("content-type", ""):
                raise RuntimeError(f"tab {name}: status={resp.status_code}")
            return resp.content.decode("utf-8-sig")

        texts = await asyncio.gather(*(tab(t) for t in _TABS))
    return dict(zip(_TABS, texts))


async def refresh() -> dict:
    """Kéo dữ liệu mới từ Sheet. Lỗi hoặc dữ liệu hỏng thì giữ nguyên kho đang dùng."""
    global _kb
    if not config.SHEET_ID:
        load_snapshot()
        return {"ok": True, "changed": False, **status()}
    try:
        tabs = await _fetch_tabs()
        fingerprint = _vkey("\x00".join(tabs[t] for t in _TABS) + config.EMBED_MODEL + str(config.KB_FULL_CONTEXT_CHARS))
        if fingerprint == _kb.fingerprint:
            return {"ok": True, "changed": False, **status()}

        prompt_rows = [r for r in _rows(tabs["prompt"]) if r.get("prompt")]
        products    = _parse_products(_rows(tabs["data"]))
        rules       = _parse_rules(_rows(tabs["knowledge"]))
        if not prompt_rows or not products:
            raise RuntimeError(f"dữ liệu thiếu: prompt={len(prompt_rows)} dòng, sản phẩm APPROVED={len(products)}")

        vectors = await _embed_docs(products, rules, _kb.vectors)
        _kb = _KB(prompt_rows[0]["prompt"], products, rules, vectors,
                  fingerprint, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(_kb.to_snapshot(), ensure_ascii=False), encoding="utf-8")
        logger.info("KB: cập nhật từ Sheet — %d sản phẩm, %d kịch bản, embedding=%s",
                    len(products), len(rules), bool(vectors))
        return {"ok": True, "changed": True, **status()}
    except Exception as exc:
        logger.error("KB: đồng bộ Sheet thất bại (%s) — giữ dữ liệu cũ", exc)
        return {"ok": False, "error": str(exc), **status()}


# ── Chạy tay để kiểm tra: python kb.py "câu hỏi thử" ─────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    async def _demo():
        load_snapshot()
        print(await refresh())
        for q in sys.argv[1:]:
            products, rules = await search(q)
            print(f"\n» {q}\n  sản phẩm: {[p['id'] + ' ' + p['name'].strip() for p in products]}"
                  f"\n  kịch bản: {[r['id'] + ' ' + r['scenario'] for r in rules]}")

    asyncio.run(_demo())
