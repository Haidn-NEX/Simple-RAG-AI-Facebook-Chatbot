# Vận hành VPS

Hướng dẫn truy cập, deploy, kiểm tra và quay lại bản cũ cho bot trên VPS. Dùng giá trị trung tính: thay `USER`, `IP_VPS`, `<SHEET_ID>` bằng giá trị thật. Không ghi secret vào tài liệu.

## 1. Thông tin chạy

| Mục | Giá trị |
|---|---|
| Thư mục bot | `~/chatbot` (có `venv/`, `.env`, `data/`, `logs/`) |
| Service | `chatbot.service` (cổng 8000) và `cloudflared.service` (Cloudflare Tunnel), cả hai chạy bằng systemd |
| Secret | Chỉ nằm trong `~/chatbot/.env` trên VPS và `bot/.env` trên máy; `.env` đã nằm trong `.gitignore` |
| Kiểm tra sống | `curl -s localhost:8000/health` |

## 2. Truy cập bằng key SSH riêng (làm một lần, trên Windows)

1. Tạo key, đặt passphrase khi được hỏi:

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\chatbot_vps" -C "chatbot-project"
```

2. Đưa **public key** (`.pub`) lên VPS. Lệnh chỉ thêm một dòng vào `authorized_keys`, không xóa key cũ:

```powershell
type "$env:USERPROFILE\.ssh\chatbot_vps.pub" | ssh USER@IP_VPS "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

3. Tạo file `C:\Users\<tên bạn>\.ssh\config` (tên không có đuôi):

```
Host chatbot-vps
    HostName IP_VPS
    User USER
    IdentityFile ~/.ssh/chatbot_vps
    IdentitiesOnly yes
```

4. Nạp key vào ssh-agent. Bật dịch vụ bằng PowerShell **quyền Administrator** (`Automatic` để tự chạy sau khi khởi động lại máy), rồi nạp key ở PowerShell thường:

```powershell
Set-Service ssh-agent -StartupType Automatic
Start-Service ssh-agent
ssh-add "$env:USERPROFILE\.ssh\chatbot_vps"
```

5. Thử: `ssh chatbot-vps`. Chạy `ssh`/`scp` trong **PowerShell**, không dùng Git Bash (không thấy ssh-agent của Windows).

Lỗi thường gặp xem [FAQ-TROUBLESHOOTING.md](FAQ-TROUBLESHOOTING.md), mục 7.

## 3. Deploy bản mới

Chạy từ máy Windows. Lệnh `scp` chỉ chép file nguồn, không đụng `.env`, `data/`, `logs/`.

Trước khi chép: so phiên bản thư viện trên VPS với máy phát triển (sự cố 12/10/2026: code dùng API của SDK mới, VPS có SDK 1.2.0 nên bot sập).

```powershell
ssh chatbot-vps "~/chatbot/venv/bin/pip show google-genai | grep -i ^version"
# Kiểm thử cục bộ bằng đúng phiên bản đó: pip install --target <thư mục tạm> google-genai==<phiên bản>

# 1. Sao lưu
ssh chatbot-vps "cp -r ~/chatbot ~/chatbot.bak-YYYYMMDD"

# 2. Chép code
cd D:\AI\chatbot\bot
scp ai.py config.py database.py handler.py kb.py main.py messenger.py requirements.txt chatbot-vps:~/chatbot/
```

Trong VPS (`ssh chatbot-vps`):

```bash
cd ~/chatbot
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m py_compile ai.py config.py database.py handler.py kb.py main.py messenger.py && echo COMPILE_OK
sudo systemctl restart chatbot
sleep 4
curl -s localhost:8000/health
```

**Kết quả đúng:** `"status":"ok"`, `kb.source` là `sheet`, số sản phẩm và kịch bản khớp Sheet.

### Biến môi trường mới

Thêm vào `.env` bằng `>>` (chỉ thêm cuối file) và sao lưu `.env` trước:

| Biến | Ý nghĩa |
|---|---|
| `SHEET_ID` | Chuỗi nằm giữa `/d/` và `/edit` trong link Google Sheet; để trống thì dùng `products.json` |
| `SHOP_NAME` | Tên shop dùng trong lời chào đầu; để trống thì bot gọi là "shop" |
| `ADMIN_TOKEN` | Chuỗi ngẫu nhiên, sinh bằng `openssl rand -hex 24`, không in ra màn hình; dùng cho `POST /admin/reload` |
| `KB_REFRESH_SECONDS`, `KB_FULL_CONTEXT_CHARS`, `EMBED_MODEL` | Xem `bot/.env.example` |
| `GEMINI_THINKING` | `minimal`/`low`/`medium`/`high`/`off`; mặc định `low`; chỉ có tác dụng khi SDK trên VPS hỗ trợ `thinking_level` |
| `GEMINI_HEDGE_SECONDS` | Giây chờ model chính trước khi gọi dự phòng song song; mặc định `12` (đo thực tế: 8 giây khiến gọi thừa) |

Google Sheet phải chia sẻ "Bất kỳ ai có đường liên kết: Người xem" và có đúng 3 tab `prompt`, `data`, `knowledge`. Chỉ dòng có `status = APPROVED` được dùng.

## 4. Kiểm tra log

```bash
# Các lượt gọi Gemini: model nào, mất bao lâu, kết thúc vì sao
journalctl -u chatbot --since "-1h" --no-pager | grep "Gemini xong" | cut -c40-230

# Lỗi và cảnh báo
journalctl -u chatbot --since "-1h" --no-pager | grep -E "ERROR|WARNING" | cut -c40-230
```

`finish=FinishReason.STOP` là bình thường; `MAX_TOKENS` nghĩa là câu trả lời bị cắt (xem FAQ mục 5).

## 5. Quay lại bản cũ

```bash
cp ~/chatbot.bak-YYYYMMDD/*.py ~/chatbot/
sudo systemctl restart chatbot
```

Quay lại `.env` cũ: `cp ~/chatbot/.env.bak-YYYYMMDD ~/chatbot/.env` rồi restart.

## 6. Việc cần người chạy, trợ lý không tự làm được

- `sudo systemctl restart chatbot` (cần quyền sudo; công cụ tự động bị chặn ghi từ xa).
- Ghi vào `.env` trên VPS.
- Nhập passphrase cho `ssh-add` sau khi khởi động lại máy nếu agent chưa tự chạy.

Trợ lý có thể chép file bằng `scp` và chạy lệnh chỉ đọc (`/health`, `journalctl`, `md5sum`) để kiểm tra.
