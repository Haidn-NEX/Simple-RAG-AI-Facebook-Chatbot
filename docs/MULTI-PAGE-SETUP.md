# Hướng dẫn Mở rộng và Cấu hình Đa Fanpage (Multi-Page)

Hệ thống cho phép bạn kết nối 1, 2, 3... hoặc không giới hạn số lượng Fanpage Facebook vào cùng một bot chạy trên một máy chủ VPS duy nhất. Mỗi Fanpage sẽ được bot trả lời độc lập bằng đúng Token của nó.

---

## 1. Nguyên lý hoạt động

1. Khi khách hàng nhắn tin vào bất kỳ Page nào, Meta gửi webhook đến bot kèm trường:
   ```json
   {
     "entry": [
       {
         "id": "PAGE_ID_CỦA_TRANG_NHẬN_TIN",
         "messaging": [...]
       }
     ]
   }
   ```
2. Bot đọc `entry.id` và tra cứu trong bảng `config.PAGE_TOKENS`.
3. Khi gọi Send API trả lời khách, bot sử dụng đúng `access_token` thuộc về Page đó.
4. Trạng thái tạm dừng cho nhân viên tư vấn (`_human_mode`) được lưu theo cặp `(page_id, customer_psid)` nên không ảnh hưởng chéo giữa các Page.

---

## 2. Các bước kết nối thêm Fanpage mới

### Bước 1: Thêm Page trong Meta Developer Dashboard
1. Truy cập **developers.facebook.com** → Chọn App của bạn.
2. Vào menu trái **Messenger** → **Cài đặt API Messenger**.
3. Cuộn xuống mục **"2. Tạo mã truy cập"**:
   - Bấm nút **"Thêm trang"** (Add Page).
   - Chọn Fanpage thứ hai cần kết nối và cấp quyền.
   - Bấm nút **"Tạo"** (Generate) để sinh Page Access Token.
   - Sao chép mã token này (bắt đầu bằng `EAAP...`).
4. Tại cột **"Gói đăng ký Webhook"** của Page vừa thêm:
   - Bấm **"Chỉnh sửa"** (Edit).
   - Tích chọn 2 trường: `messages` và `message_echoes`.
   - Bấm **Lưu**.

---

### Bước 2: Cấu hình biến môi trường trên VPS
SSH vào VPS và mở file `.env`:
```bash
nano ~/chatbot/.env
```

Thêm thông tin cho Page mới. Bạn có thể đặt tên biến theo mẫu sau:

```env
# Page 1 (Đã có sẵn)
PAGE_ID=100000000000001
PAGE_ACCESS_TOKEN=EAA...token_page_1...

# Page 2 (Mới thêm)
PAGE_ID2=100000000000002
PAGE_ACCESS_TOKEN2=EAA...token_page_2...

# Page 3 (Nếu có tiếp)
PAGE_ID3=100000000000003
PAGE_ACCESS_TOKEN3=EAA...token_page_3...
```

> 💡 **Tính năng Tự động Nhận diện (Auto-Discovery):**
> Nếu bạn chỉ điền `PAGE_ACCESS_TOKEN2` mà quên điền `PAGE_ID2`, đừng lo! Khi bot khởi động, hệ thống sẽ tự động gọi API `GET /me` của Meta bằng token đó để tra cứu ra ID và tên thật của Page rồi nạp vào bộ nhớ.

---

### Bước 3: Khởi động lại Bot
Chạy lệnh khởi động lại service:
```bash
sudo systemctl restart chatbot
```

Xem log kiểm tra:
```bash
tail -n 25 ~/chatbot/logs/bot.log
```
Bạn sẽ thấy dòng log xác nhận các Page đã sẵn sàng:
```text
[INFO] Danh sách Page ID đã sẵn sàng: ['100000000000001', '100000000000002', 'default']
```

---

## 3. Kiểm tra hoạt động
1. Mở trình duyệt truy cập: `https://chatbot.[yourdomain.com]/health`
2. Kết quả trả về sẽ có đủ danh sách các Page ID:
   ```json
   {
     "status": "ok",
     "version": "v26.0",
     "active_pages": [
       "100000000000001",
       "100000000000002"
     ]
   }
   ```
3. Lấy 2 tài khoản Facebook nhắn tin đồng thời vào cả 2 Fanpage để tận hưởng thành quả!
