# 2026-08-25 — Mốc chuẩn đo lường, và vì sao lọc chuyển động bị dừng

Máy: NVIDIA L40S 46 GB (vast.ai). 16 camera, hybrid, 2 worker, native_threads 1.
Lưu ý: 16 camera này phát lại **4 video phân biệt**, mỗi video 4 lần.

## Mốc chuẩn — 3 lượt, cùng một build

| | Lượt 1 | Lượt 2 | Lượt 3 |
|---|---|---|---|
| FPS (chỉ tính lúc phục vụ khung) | 123.12 | 113.42 | 115.87 |
| Khung | 11520 | 11520 | 11520 |
| Alert | 24 | 24 | 24 |
| Track được tạo | 396 | 396 | 396 |
| Tuổi thọ track TB (khung) | 38.85 | 38.85 | 38.85 |

Hai nửa hành xử khác hẳn nhau, và điều đó quyết định cách nghiệm thu:

- **Alert và nhận dạng: giống hệt từng bit qua cả 3 lượt.** Bất kỳ thay đổi nào ở
  đây đều là thật, không cần dung sai.
- **Thông lượng thì không: chênh 8.6% trên mã không đổi.**

Luật ±5% ban đầu **sẽ chấp nhận chính 8.6% đó là "cải tiến"**, tức ghi công cho
thứ phần cứng phát không. Đã đổi sang luật phi tham số hợp với n=3: *lượt tệ nhất
của bản mới phải thắng lượt tốt nhất của mốc chuẩn.* So sánh trung bình với 3 mẫu
không tách nổi cú nhảy 8.6% khỏi kết quả thật.

## Lọc chuyển động — đã dựng, đã chạy, đã dừng

Cơ chế hoạt động đúng như thiết kế. Trên `storage_1`, gate bỏ qua **50/60 khung**;
trên `counter_1` bỏ qua **0** vì luôn có track sống. Điều kiện kép và keyframe đều
làm đúng việc.

**Nhưng nó bị dừng, và không phải vì hiệu năng.**

### Lý do dừng: ngưỡng chỉ khớp với đúng 4 video đã đo

Gate dùng một hằng số tuyệt đối — bỏ qua khung khi dưới 0.2% pixel thumbnail thay
đổi. Con số đó được hiệu chỉnh trên đúng 4 video của bộ thử. Đổi độ phân giải,
codec, ánh sáng hay mức nhiễu cảm biến là nó trượt, **và trượt theo hướng bỏ sót
người** chứ không phải hướng an toàn. Bốn cảnh không đủ để chốt một hằng số mà cả
hệ thống nhận thức phụ thuộc vào.

Muốn làm lại cho đúng thì cần ngưỡng **tự hiệu chỉnh theo từng camera** (ví dụ suy
ra sàn nhiễu của chính camera đó từ một cửa sổ trượt, rồi lấy bội số của sàn đó),
và phải kiểm trên nhiều cảnh hơn hẳn 4. Đó là việc khác, không phải tinh chỉnh
việc này.

### Số liệu vẫn giữ được, phòng khi quay lại

Khảo sát `diag_motion.py` ở ngưỡng 0.002, dùng `INTER_AREA`:

| Loại camera | % khung tĩnh | Chuỗi tĩnh dài nhất | Median dịch chuyển |
|---|---|---|---|
| storage | 66.4% | 61.3s | 0.0001 |
| cold_storage | 53.8% | 55.7s | 0.0013 |
| counter | 28.7% | 6.8s | 0.0037 |
| whole_store | 23.1% | 3.0s | 0.0060 |

Chặn trên toàn hệ: 38.7% số khung. Thực tế sẽ thấp hơn nhiều vì luật thật còn đòi
"không có track nào đang sống".

### Hai bài học kỹ thuật đáng giữ

**Thu nhỏ ảnh không hề rẻ.** `cv2.resize` 1920×1080 xuống 160×90 bằng `INTER_AREA`
tốn **2.38 ms**, đo tại chỗ trong pipeline là **6–7 ms** khi có tranh chấp — so với
26 ms của toàn bộ nhận thức mà nó định tiết kiệm. Giảm mẫu 4× trên một kênh trước
rồi mới lấy trung bình còn **0.19 ms**.

**Nhưng không được bỏ phép lấy trung bình.** Chỉ lấy mẫu thưa (`[::12,::12]`) rẻ
nhất, 0.009 ms, và **dùng không được**: nhiễu cảm biến từng pixel không bị san
phẳng, median dịch chuyển trên cùng đoạn phim tĩnh vọt lên 0.00236 — gấp 17 lần —
và tỷ lệ khung được gọi là "tĩnh" sụp từ 100% xuống 28.8%. Nó đo nhiễu rồi gọi đó
là chuyển động.

| Cách thu nhỏ | Chi phí | Median dịch chuyển | % tĩnh @0.002 |
|---|---|---|---|
| `INTER_AREA` trực tiếp | 2.383 ms | 0.00014 | 100.0% |
| giảm mẫu 4× + `INTER_AREA` | 0.187 ms | 0.00042 | 94.9% |
| chỉ lấy mẫu thưa 12× | 0.009 ms | 0.00236 | 28.8% |
