# Dự án: Chatbot bán hàng Messenger, thay thế giải pháp n8n

File này là chỉ dẫn cho trợ lý AI lập trình làm việc trong thư mục này (Claude Code, Codex, Cursor, Gemini CLI hoặc công cụ tương tự). Đọc hết trước khi làm bất cứ việc gì. Trả lời người dùng bằng tiếng Việt.

Đây là bản chỉ dẫn gốc duy nhất. `CLAUDE.md` chỉ trỏ về file này; khi cần sửa luật, sửa ở đây.

## Mục tiêu

Xây backend chatbot Messenger tự động tư vấn và chốt đơn cho shop, chạy trên VPS riêng, **thay thế quy trình đang chạy bằng n8n**. Toàn bộ nội dung bán hàng (prompt, sản phẩm, kịch bản) do chủ shop quản lý trên Google Sheet; backend đọc Sheet, gọi Gemini để trả lời, và tự làm các hành động mà trước đây n8n làm.

Dự án đã qua giai đoạn xin quyền Meta: app ở chế độ Live, quyền `pages_messaging` mức Standard đủ cho Page của chính chủ.

## Hiện trạng

- Bot chạy trên VPS, 2 Page, đồng bộ Google Sheet mỗi 5 phút, gửi ảnh sản phẩm, có model Gemini dự phòng.
- Chưa làm: các hành động ngoài trả lời chữ. Sheet đánh dấu 5 kịch bản cần backend tự làm (cột `requires_n8n_logic = YES`): `K004` khách chê đắt, `K006` chốt đơn, `K008` xác nhận đơn, `K012` đơn số lượng lớn / đại lý, `K013` khách im lặng sau báo giá. Hiện bot **chưa tạo đơn, chưa báo chủ shop**, và model có thể hứa những việc đó.

## Tài liệu trong repo

| File | Nội dung |
|---|---|
| `docs/ARCHITECTURE.md` | Kiến trúc và luồng dữ liệu |
| `docs/RANG-BUOC-NEN-TANG.md` | Ràng buộc Messenger Platform mà code phải tuân thủ |
| `docs/VAN-HANH-VPS.md` | Truy cập SSH, deploy, kiểm tra log, quay lại bản cũ |
| `docs/FAQ-TROUBLESHOOTING.md` | Lỗi đã gặp và cách xử lý |
| `docs/MULTI-PAGE-SETUP.md` | Thêm Page thứ hai trở đi |
| `bot/` | Mã nguồn (Python 3.12, FastAPI, SQLite, SDK `google-genai`) |
| `site/` | Trang Privacy Policy, Terms, Data Deletion (bắt buộc để app ở chế độ Live) |

## Quy tắc code backend

Chi tiết nền tảng ở `docs/RANG-BUOC-NEN-TANG.md`. Những điểm không được sai:

- Webhook GET trả lại `hub.challenge` khi `hub.verify_token` khớp. Webhook POST trả `200` ngay, xử lý bất đồng bộ phía sau, xác thực `X-Hub-Signature-256` bằng App Secret, chống xử lý trùng theo `mid`.
- Mọi tin gửi đi dùng `messaging_type: RESPONSE`. Bot không bao giờ nhắn trước, chỉ trả lời trong cửa sổ 24 giờ.
- Không dùng `CONFIRMED_EVENT_UPDATE`, `ACCOUNT_UPDATE`, `POST_PURCHASE_UPDATE`: Meta đã chặn từ 27/4/2026. Kiến thức cũ của bạn có thể vẫn gợi ý các tag này; đừng dùng.
- Mọi input đều có phản hồi trong dưới 30 giây (text, ảnh, sticker, voice, thứ không hiểu). Gọi AI phải có hạn giờ và câu trả lời dự phòng.
- Tin đầu tiên với mỗi khách tự giới thiệu là trợ lý tự động.
- Khách muốn gặp nhân viên: bot dừng trả lời hội thoại đó và báo khách. Thấy `message_echoes` từ nhân viên: bot im lặng. Bỏ qua echo do chính app gửi.
- Khách yêu cầu xóa dữ liệu: xóa bản ghi theo PSID và xác nhận.
- AI chỉ trả lời từ dữ liệu trong Sheet, không tự bịa giá hay sản phẩm. Mô hình **không được hứa** hành động mà backend chưa làm thật.
- Tin gửi cho khách là văn bản thuần: Messenger không hiển thị markdown (`plain_text()` trong `ai.py` làm sạch).
- Phiên bản Graph API lấy từ dashboard của người dùng, không hardcode theo trí nhớ.
- Log mọi request và response với Graph API, che token. Không ghi số điện thoại hay nội dung nhạy cảm của khách vào log.
- Giữ code nhỏ và dễ đọc: một dịch vụ, SQLite. Không thêm framework, hàng đợi, Docker hay tính năng ngoài phạm vi trừ khi người dùng yêu cầu.

Trước khi viết code, nói ngắn gọn bạn sắp tạo hoặc sửa những file nào. Sau khi viết, tự chạy kiểm tra được gì thì chạy (biên dịch, kiểm thử hàm thuần) rồi mới bảo người dùng deploy.

## Làm việc với VPS

- Trợ lý được chép file bằng `scp` và chạy lệnh **chỉ đọc** (`/health`, `journalctl`, `md5sum`). Hệ thống chặn ghi từ xa không tương tác nên các việc sau do **người dùng tự chạy**: `sudo systemctl restart chatbot`, ghi vào `.env` trên VPS, mọi lệnh `sudo`.
- Nói rõ lệnh sẽ làm gì trước khi đưa cho người dùng; luôn sao lưu `~/chatbot` và `.env` trước khi đổi.
- Sau mỗi deploy: so `md5sum` file trên VPS với bản trên máy, kiểm tra `/health` và dòng log `Gemini xong`.
- Quy trình đầy đủ: `docs/VAN-HANH-VPS.md`.
- Dùng PowerShell cho `ssh`/`scp` (Git Bash không thấy ssh-agent của Windows). Truyền script nhiều dòng qua file rồi `scp`, đừng lồng dấu nháy trong chuỗi lệnh.

## Bảo mật

- App Secret, Page access token, Verify token, API key Gemini, `ADMIN_TOKEN`, mật khẩu VPS chỉ nằm trong `.env` trên VPS và trên máy người dùng. `.env` đã nằm trong `.gitignore`.
- Không ghi secret vào tài liệu, mô tả commit hay log. Khi cần dùng key trên VPS (ví dụ liệt kê model), đọc key bên trong script chạy trên VPS và không in ra.
- Dữ liệu khách (`bot/data/`) không đưa vào git.
- Trước khi commit, quét token (`EAA...`, `AIza...`) và `SHEET_ID` trong các file sẽ commit.

## Điểm dừng bắt buộc

Dừng lại, giải thích các lựa chọn và chờ người dùng quyết định ở các điểm sau:

- Mọi việc tốn tiền: dịch vụ AI trả phí, VPS, domain.
- Kênh báo cho chủ shop khi có đơn hoặc yêu cầu đặc biệt, và phạm vi hành động bot được tự làm.
- Đổi model Gemini chính, hoặc đổi thứ tự model dự phòng (ảnh hưởng chất lượng và chi phí).
- Chuyển Sheet sang page shop khác, hoặc đổi nội dung app, trang pháp lý đang khai với Meta.
- Gửi hoặc commit lên remote: chỉ khi người dùng yêu cầu.
- Bất kỳ lệnh nào xóa dữ liệu hoặc thay đổi cấu hình hệ thống trên VPS.

## Những việc không làm

- Không thao tác thay người dùng trên tài khoản Meta, Cloudflare hay nhà cung cấp VPS.
- Không khuyên cách lách chính sách Meta: nhắn ngoài cửa sổ 24 giờ, dùng message tag sai mục đích, khai sai thông tin, dùng tài khoản giả.
- Không trình bày phỏng đoán như sự thật. Nói "tôi không chắc" khi không chắc, và đề xuất cách kiểm chứng (log, truy vấn đọc, ảnh chụp).
- Không đưa ảnh chụp màn hình dashboard (có thể lộ ID, domain, token) vào repo.
