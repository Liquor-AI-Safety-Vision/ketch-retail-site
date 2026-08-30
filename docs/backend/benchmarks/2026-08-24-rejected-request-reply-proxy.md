# 2026-08-24 — Hai hướng đã bị bác bỏ: inference proxy và spike tách tiến trình

Ghi lại từ `diag_transport.py` và `spike_split_arch.py` trước khi chúng rời khỏi
backend. Cả hai đều tự khai đã chết trong docstring, và `diag_transport` thì
**không import nổi** từ lâu — bốn class nó dùng (`ClientChannel`,
`InferenceClient`, `ServerConfig`, `serve`) đã bị xoá cùng kiến trúc mà nó đo.

Số đo của chúng là lý do kiến trúc hiện tại có hình dạng như bây giờ, nên nó
thuộc về docs. Một file `.py` không chạy được nằm trong `tools/` thì không phải
bản ghi — nó là cái bẫy cho người sau.

## 1. Proxy request/reply cho inference — bị bác bỏ

**Câu hỏi**: một request vượt qua ranh giới process có đắt hơn đáng kể so với
cùng lời gọi đó làm in-process không? Nếu có, đưa model ra process riêng là trả
tiền thuê trên **mỗi frame**.

**Kết quả**: có, dứt khoát.

| | độ trễ |
|---|---|
| ping không có model phía sau | 2.30 ms |
| ping tới process **chỉ nạp model và ngồi im** | 9.35 ms |

Ba cách giải thích đã được thử và loại: số thread của onnxruntime, chế độ
spin-wait của nó, và CUDA context của chính phía gọi. Còn lại là bản thân hình
dạng: **request/reply blocking hỏi đúng process ít có khả năng trả lời nhanh
nhất, hai đến ba lần mỗi frame**, trên một frame mà chi phí thật chỉ khoảng
25 ms.

**Hệ quả**: proxy bị thay bằng pipeline một chiều trong
`backend/src/runtime/inference_server.py`.

Nơi các phép đo của nó sống bây giờ:

| nội dung | chỗ |
|---|---|
| chi phí chuyển frame giữa process | `runtime/frame_ring.py`, docstring module |
| vì sao một chiều, không phải request/reply | `runtime/inference_server.py`, docstring module |
| độ trễ per-model và batching | `backend/tools/diag_infer_modules.py` |
| thông lượng per-tier | `backend/tools/diag_split_tiers.py` |

Hai công cụ cuối vẫn sống. `diag_infer_modules` bao phủ mọi thứ `_bench_local`
từng làm, và làm tốt hơn: nó quét theo batch size và số caller thay vì đo một
đường tuần tự duy nhất.

## 2. Spike tách decode khỏi inference — đã hoàn thành nhiệm vụ

Spike dùng một lần, hỏi: tách decode khỏi inference có nâng được trần không?

Bối cảnh: mọi phép đo lúc đó đều nói hình dạng hiện tại bị **GIL** chặn, không
phải GPU. Đẩy pose preprocessing ra khỏi thread batch-runner cắt `queue_wait`
60% và `detect_pose` 22% mà **thông lượng không đổi** — đó chính là hình dạng
của trần GIL.

Chi phí CUDA context đo được, là lý do không dựng nhiều stack riêng:

| số context | chi phí |
|---|---|
| 2 | 19 ms |
| 4 | 45 ms |
| 8 | 153 ms |

**Một sai lầm phương pháp đáng giữ lại**: bản spike đầu tiên chỉ chạy pose và
cho kết quả vô kết luận. Nó xứng đáng như vậy — lược pipeline xuống còn mỗi pose
đã xoá mất tracker và ReID, tức là phần tạo ra hầu hết áp lực GIL, rồi nạp liệu
cho một process inference duy nhất mà 5.70 ms/frame tuần tự đã tự chặn nó ở
175 FPS bất kể bao nhiêu decoder đẩy vào. **Nó đo phép tách so với trần của
chính nó, chứ không phải so với GIL.**

Bản sau sửa cả hai: workload là pose + ReID + tracking (~80% thời gian frame
thật), giống hệt nhau ở cả hai chế độ, và phép tách chạy hai process inference —
đúng bằng hai CUDA context mà hình dạng hiện tại vốn đã trả.

Kiến trúc tách đã được chốt và triển khai thật trong `backend/src/runtime/`, nên
spike không còn việc. Nó nằm ở `dev/tools/spike_split_arch.py` nếu cần đọc lại
phương pháp.
