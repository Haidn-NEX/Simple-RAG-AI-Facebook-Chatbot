# Các Lỗi Thường Gặp & Cách Khắc Phục (Troubleshooting)

Tổng hợp các sự cố kinh điển khi phát triển Chatbot Facebook Messenger tích hợp AI và phương án xử lý đã được kiểm chứng.

---

## 1. Lỗi bot tự im lặng vĩnh viễn sau câu trả lời đầu tiên (Lỗi `message_echoes`)

### Hiện tượng:
Khách nhắn tin, bot trả lời câu đầu tiên rất tốt. Nhưng từ câu thứ hai trở đi, khách nhắn gì bot cũng im lặng, không phản hồi.

### Nguyên nhân:
Khi bot gọi Send API gửi tin nhắn cho khách, Meta sẽ bắn ngược lại webhook sự kiện `message_echoes` để thông báo tin đã được gửi.
- Nếu code không kiểm tra `message.app_id`, bot sẽ tưởng nhầm chính tin nhắn của bot là **nhân viên tư vấn đang chat tay**.
- Hậu quả: Bot tự động kích hoạt `_human_mode` và tự mute chính mình!

### Cách khắc phục trong `handler.py`:
```python
if "message" in event and event.get("message", {}).get("is_echo"):
    msg = event["message"]
    app_id = msg.get("app_id")
    # Nếu tin nhắn do chính bot này gửi đi -> BỎ QUA NGAY
    if app_id and str(app_id) == str(config.APP_ID):
        return

    # Chỉ bật chế độ nhân viên khi tin do con người gửi từ Page Inbox
    customer_id = event.get("recipient", {}).get("id")
    if customer_id:
        _human_mode.add((page_id, str(customer_id)))
    return
```

---

## 2. Lỗi Google Gemini API 404 Not Found hoặc 429 Resource Exhausted

### Hiện tượng:
- Log báo lỗi `404 models/gemini-1.5-flash is not found`.
- Hoặc báo `429 RESOURCE_EXHAUSTED: Your prepayment credits are depleted`.

### Nguyên nhân:
- Thư viện cũ `google-generativeai` đã bị Google khai tử.
- Các tên model cũ (như 1.5-flash hoặc 2.0-flash) bị đổi chính sách hoặc hết hạn mức miễn phí.

### Cách khắc phục:
1. Nâng cấp lên thư viện chuẩn mới trong `requirements.txt`:
   ```text
   google-genai
   ```
2. Trong `ai.py`, sử dụng model mới nhất (danh sách model chính + dự phòng):
   ```python
   _MODELS = ("gemini-3.6-flash", "gemini-3.5-flash")
   ```
   Tên model đổi nhanh. Muốn biết tài khoản của bạn đang dùng được model nào, chạy `client.models.list()` (xem mục 6).
3. Tạo API Key mới tại **aistudio.google.com** để có hạn mức Free Tier đầy đủ.

---

## 5. Bot gửi câu trả lời dở dang và không gửi ảnh (Gemini cắt cụt đầu ra)

### Hiện tượng:
- Tin của bot kết thúc giữa chừng, ví dụ *"Em gửi trước 3 mẫu hot bên em để anh/"*.
- Khách hỏi xem ảnh, bot nói sẽ gửi nhưng không có ảnh nào, trong log sau tin chữ không có lệnh `Send API` gửi ảnh.
- Service vẫn `active`, không có `ERROR` trong log.

### Nguyên nhân:
`max_output_tokens` đặt quá thấp (800). Với model Gemini 3.x, hạn mức này có thể bị token "suy nghĩ" của model chiếm bớt, nên phần chữ bị cắt. Thẻ `[IMAGE: mã]` nằm cuối câu trả lời nên chưa kịp được viết ra, và bot lấy ảnh từ chính các thẻ này.

### Cách kiểm tra:
Đọc nguyên văn tin bot đã lưu (log chỉ in 150 ký tự đầu của mỗi tin):
```bash
python3 -c "import sqlite3; c=sqlite3.connect('file:/home/USER/chatbot/data/bot.db?mode=ro', uri=True); [print(r[0], r[1], sep='\n') for r in c.execute(\"select created_at, content from conversations where role='assistant' order by id desc limit 2\")]"
```
Sau khi sửa, log mỗi lượt có dòng `Gemini xong: model=..., finish=...`. `finish=FinishReason.MAX_TOKENS` nghĩa là vẫn bị cắt.

### Cách khắc phục:
Trong `ai.py` đặt `_MAX_OUTPUT_TOKENS = 4096` (độ dài câu trả lời vẫn do prompt giới hạn). Ghi nhận ngày 11/10/2026: sau khi sửa mọi lượt đều `finish=STOP`, 140 đến 210 token đầu ra.

---

## 6. Bot báo "sự cố kỹ thuật" vì Gemini quá tải hoặc quá chậm

### Hiện tượng:
Khách nhận *"Xin lỗi, tôi đang gặp sự cố kỹ thuật..."*. Log có một trong hai dạng:
- `ServerError: 503 UNAVAILABLE ... This model is currently experiencing high demand`
- `Gemini timeout sau 25.0 giây`

### Nguyên nhân:
Lỗi phía Google, tạm thời. Ngày 11/10/2026, `gemini-3.6-flash` mất 7 đến 10 giây cho câu trả lời chỉ 70 đến 210 token, có lượt 22 giây và có lượt vượt 25 giây (`token suy nghĩ=None`, tức độ trễ nằm ở dịch vụ, không phải model "nghĩ" lâu). Bản cũ không thử lại nên gặp lỗi là báo khách ngay.

### Cách khắc phục (đã làm trong `ai.py`):
- Mỗi model có hạn giờ riêng (`_ATTEMPT_TIMEOUTS = (13.0, 10.0)` giây). Quá hạn hoặc gặp lỗi 404/429/500/503/504 thì chuyển sang model kế tiếp trong `_MODELS`. Tổng 23 giây, dưới `_TIMEOUT = 25` và dưới luật 30 giây của Meta.
- Lỗi khác (sai key, nội dung bị chặn...) vẫn báo ngay, không thử lại.
- Kiểm tra độ trễ từng model:
  ```bash
  journalctl -u chatbot --since "-1h" --no-pager | grep "Gemini xong" | cut -c40-230
  ```
  Nếu model chính thường xuyên trên 8 giây còn model dự phòng nhanh rõ rệt, đảo thứ tự trong `_MODELS`.

### Liệt kê model khả dụng của tài khoản (không in API key):
Chạy trên VPS bằng Python của `venv`, đọc key từ `.env` trong script rồi gọi `client.models.list()` và lọc tên có `flash`.

---

## 7. SSH vào VPS bằng key riêng trên Windows

| Thông báo | Nguyên nhân | Cách sửa |
|---|---|---|
| `Bad permissions. Try removing permissions for user: ... on file .../.ssh/config` | File `config` còn quyền của tài khoản Windows khác (kế thừa từ thư mục `.ssh`) | `icacls "$env:USERPROFILE\.ssh\config" /inheritance:r`, rồi `/grant "${env:USERNAME}:F"`. Lỗi tương tự với file key thì làm đúng như vậy cho file key |
| `Permission denied (publickey,password)` | Public key chưa có trong `~/.ssh/authorized_keys` của VPS | Thêm lại public key (xem [VAN-HANH-VPS.md](VAN-HANH-VPS.md), mục 2, bước 2) |
| `Permission denied` nhưng `ssh -v` có `Server accepts key` | Private key có passphrase và chưa nạp vào ssh-agent (chế độ không tương tác không hỏi được passphrase) | Bật dịch vụ `ssh-agent` bằng PowerShell quyền Administrator, rồi `ssh-add` file key |
| `scp`/`ssh` chạy trong Git Bash báo `Connection closed` | Git Bash dùng bản ssh khác, không thấy ssh-agent và file `config` của Windows | Chạy `ssh`/`scp` trong PowerShell |
| Sau khi khởi động lại máy lại bị hỏi passphrase | ssh-agent không giữ key sau khi tắt máy | Chạy lại `ssh-add` |

---

## 8. Chạy lệnh có `sudo` trên VPS từ công cụ tự động

`sudo systemctl restart chatbot` cần mật khẩu sudo hoặc quyền được cấp, nên trợ lý AI không tự restart được qua SSH không tương tác. Quy trình hiện tại: trợ lý chép file và kiểm tra bằng lệnh chỉ đọc; **người dùng tự chạy `sudo systemctl restart chatbot`** rồi gửi lại kết quả `/health`.

---

## 3. Lỗi Webhook Verify Token không khớp (HTTP 403)

### Hiện tượng:
Khi bấm "Xác minh và lưu" trên Meta Developer Dashboard, Meta báo lỗi đỏ: *Không thể xác thực URL gọi lại*.

### Cách kiểm tra:
1. Mở file `.env` trên VPS kiểm tra chuỗi `VERIFY_TOKEN`:
   ```bash
   cat ~/chatbot/.env | grep VERIFY_TOKEN
   ```
2. Đảm bảo ô "Xác minh mã" trên Meta Dashboard điền **chính xác 100% từng ký tự** (chú ý không để thừa khoảng trắng ở đầu hoặc cuối).
3. Đảm bảo dịch vụ chatbot đang chạy trên VPS:
   ```bash
   sudo systemctl status chatbot
   ```

---

## 4. Lỗi Send API trả về mã HTTP 400 (Bad Request)

### Các nguyên nhân thường gặp:
- **Token không hợp lệ hoặc đã hết hạn:** Dùng Graph API Explorer kiểm tra lại token.
- **Dùng sai Token giữa các Page:** Khách nhắn vào Page B nhưng bot lại dùng Token của Page A để gọi Send API. Luôn đảm bảo `_get_token(page_id)` lấy đúng token.
- **Nội dung tin nhắn quá dài:** Meta giới hạn tối đa 2000 ký tự cho một tin nhắn text. Trong `messenger.py` đã có hàm tự động cắt ngắn nếu vượt quá 1999 ký tự:
  ```python
  if len(text) > 1999:
      text = text[:1996] + "..."
  ```
