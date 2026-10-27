# Ràng buộc nền tảng Messenger

Những quy định của Meta mà code chatbot phải tuân thủ. Rút gọn từ tài liệu chính sách của Meta ngày 18/9/2026.

Meta đổi chính sách thường xuyên. Trước khi làm tính năng chạm tới các điểm dưới đây, đối chiếu lại với [Messenger Platform policy](https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy) và [changelog](https://developers.facebook.com/documentation/business-messaging/messenger-platform/changelog/).

## 1. Quy tắc code không được sai

| Quy tắc | Cách tuân thủ trong code |
|---|---|
| Khách nhắn trước, bot chỉ trả lời | Mọi tin gửi đi dùng `messaging_type: RESPONSE`; bot không bao giờ chủ động nhắn |
| Chỉ gửi trong 24 giờ kể từ hành động gần nhất của khách | Không có luồng "nhắc lại", "chăm sóc sau" tự gửi. Kịch bản như `K013` (khách im lặng sau báo giá) **không được** làm bằng tin nhắn tự động ngoài 24 giờ |
| Không dùng message tag đã bị chặn | Cấm `CONFIRMED_EVENT_UPDATE`, `ACCOUNT_UPDATE`, `POST_PURCHASE_UPDATE` (Meta chặn từ 27/4/2026, trả lỗi code 100). Tag còn dùng được: `HUMAN_AGENT`, `CUSTOMER_FEEDBACK`; tag không bao giờ chứa nội dung khuyến mãi |
| Phản hồi mọi input trong 30 giây | Text, ảnh, sticker, voice, thứ không hiểu đều có phản hồi. Gọi AI có hạn giờ và câu trả lời dự phòng |
| Tự giới thiệu là trợ lý tự động | Tin đầu tiên với mỗi khách (`handler._intro`) |
| Nhường cho nhân viên | Khách gõ muốn gặp nhân viên thì bot dừng và báo khách. Thấy `message_echoes` từ nhân viên thì bot im lặng; bỏ qua echo do chính app gửi (`app_id == APP_ID`) |
| Xóa dữ liệu khi khách yêu cầu | Xóa theo PSID (kể cả log nếu có lưu nội dung) và xác nhận |
| Webhook | GET trả `hub.challenge` khi `hub.verify_token` khớp; POST trả `200` ngay, xử lý bất đồng bộ; xác thực `X-Hub-Signature-256` bằng App Secret; chống trùng theo `mid` |

## 2. Cửa sổ nhắn tin

| Cơ chế | Khi nào gửi được | Dùng cho |
|---|---|---|
| Standard messaging | Trong 24 giờ từ hành động gần nhất của khách | Toàn bộ luồng tư vấn và chốt đơn hiện tại |
| Human Agent tag | Trong 7 ngày, chỉ nhân viên thật trả lời, không tự động. Cần được duyệt riêng | Nhân viên xử lý đơn tồn |
| Utility messages | Ngoài 24 giờ, theo template, không khuyến mãi. Cần quyền `pages_utility_messaging` | Thông báo trạng thái đơn ngoài 24 giờ (thay cho tag đã bị chặn) |
| One-time notification | Một tin sau khi khách bấm đồng ý | Báo có hàng lại |
| Marketing Messages | Ngoài 24 giờ, khách opt-in, tính phí | Remarketing, giai đoạn sau |

Hành động mở lại cửa sổ 24 giờ: khách gửi tin, bấm Get Started hoặc nút CTA, bấm quảng cáo Click-to-Messenger rồi chat, bấm link `m.me` có `ref`, hoặc thả reaction.

**Hệ quả thiết kế:** mọi tính năng "báo đơn", "liên hệ lại", "nhắc khách" phải chạy trong cửa sổ 24 giờ, hoặc đi qua Utility messages / Human Agent. Không có đường tắt bằng tag.

## 3. Mức quyền truy cập

| Trường hợp | Quyền cần | Điều kiện |
|---|---|---|
| Page thuộc chính chủ app (đang dùng) | `pages_messaging` ở mức Standard Access | App ở chế độ **Live**; có Privacy Policy, Terms, Data Deletion URL hoạt động. Không cần Business Verification hay App Review |
| Nền tảng SaaS cho Page của người khác | Advanced Access cho `pages_messaging`, `pages_show_list`, `pages_manage_metadata` | Business Verification (cần pháp nhân), Facebook Login for Business, App Review có video |

Lỗi `(#10) Cannot message users who are not admins, developers or testers...` nghĩa là app còn ở chế độ Development mà dùng tài khoản ngoài để thử. Chuyển app sang Live thì hết.

Điểm đã kiểm chứng thực tế ngày 19/9/2026: với app Live và Standard Access, tài khoản không có vai trò trên app nhắn được và bot trả lời được.

## 4. Dữ liệu khách

- Chỉ dùng dữ liệu nhận qua Messenger cho mục đích đã nêu trong Privacy Policy. Không bán, không chuyển cho bên môi giới dữ liệu.
- Không yêu cầu khách gửi số thẻ, mật khẩu, mã OTP hay dữ liệu sức khỏe qua chat. Thanh toán qua COD hoặc link cổng thanh toán.
- PSID chỉ có nghĩa trong phạm vi một page; không ghép danh tính chéo giữa các page.
- Khi lưu tên, số điện thoại, địa chỉ khách, tuân thủ luật bảo vệ dữ liệu cá nhân của Việt Nam. Không ghi số điện thoại của khách vào log.

## 5. Duy trì để không mất quyền

| Việc | Tần suất | Hậu quả nếu bỏ qua |
|---|---|---|
| Giữ Privacy Policy, Terms, Data Deletion URL mở được công khai | Liên tục | App có thể bị gỡ khỏi chế độ Live |
| Theo dõi changelog Messenger Platform | Hằng tháng | Code gãy khi API đổi (như vụ message tag tháng 4/2026) |
| Nâng phiên bản Graph API (đang dùng `v26.0`) | Trước khi hết hạn | Request bị từ chối |
| Kiểm tra Page Support Inbox | Hằng tuần | Bỏ lỡ cảnh báo vi phạm; có 7 ngày để sửa trước khi bị giới hạn gửi tin |
| Data Use Checkup | Hằng năm (nếu có Advanced Access) | Mất Advanced Access |
| Dùng quyền đều đặn | Quyền không dùng 90 ngày phải cấp lại | Bot ngừng trên page đó |

Bot bán mặt hàng thuộc nhóm bị cấm theo Commerce Policies (vũ khí, thuốc kê đơn, thuốc lá, hàng giả...) là lý do bị khóa không phụ thuộc chất lượng code.

## 6. Điểm chưa xác minh

- Standard Access có đủ cho page được khách chia sẻ theo kiểu đối tác (partner access) hay không: chưa thấy Meta xác nhận.
- Phạm vi quốc gia và điều kiện của Utility messages và Marketing Messages có thể đã đổi: kiểm tra trước khi xây thông báo đơn ngoài 24 giờ.
- Phiên bản Graph API mới nhất: luôn lấy từ dashboard tại thời điểm code, không hardcode theo trí nhớ.
