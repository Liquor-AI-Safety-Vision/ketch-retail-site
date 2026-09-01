# Benchmark notes

**Cập nhật 2026-08-30: file này từng mô tả hai chế độ `process` và `shared batching`.
Cả hai đã bị thay bằng split architecture (ba tier tách process, nối bằng
shared-memory ring). Bản ghi cũ giữ nguyên trong `2026-08-24-process-l40s.md` vì nó
là mốc so sánh, không phải kiến trúc hiện tại.**

Mục tiêu của thư mục này: **không con số nào sống bằng trí nhớ.** Mỗi bản ghi phải
gắn với ngày chạy, lệnh chạy, commit hash, cấu hình phần cứng và điều kiện đo.

## Đọc theo thứ tự nào

| file | nội dung |
|---|---|
| `2026-08-27-16cam-4070-multistore.md` | **số chính thức hiện tại** — 4070, 16 camera, n=3, 99.1 FPS, biên 1.55× |
| `2026-08-27-edge-gpu-4060ti-vs-4070.md` | chọn GPU edge |
| `2026-08-25-split-16cam-l40s.md` | split architecture, nơi topology 8/2×8/4 được chốt |
| `2026-08-26-till-thread-pool-oversizing.md` | cú tối ưu lớn nhất (5×), và cái thước đo hỏng làm lạc hướng 4 vòng |
| `2026-08-24-rejected-request-reply-proxy.md` | hai hướng đã thử và bị bác bỏ bằng số đo |
| `2026-08-27-till-nano-rejected.md` | model till nhẹ hơn, bị bác bỏ vì bỏ sót 55% |
| `2026-08-24-hybrid-16cam-l40s.md` | **nguồn của 3 quyết định split đang dùng**: CUDA EP trả giá khi batch shape đổi → `pose_pad_batch`; số CUDA context quyết định `infer` → 2 inferer; batch thật không giúp khi GPU bão hoà. Kèm danh sách hướng đã bác bỏ |
| `2026-08-24-process-l40s.md` | baseline cũ, giữ làm mốc |

Hai bản ghi `2026-08-24-*-l40s.md` đo trên **kiến trúc monolithic đã gỡ 01/09/2026**.
Lệnh trong đó không chạy lại được và FPS không so chéo được với split; phần nguyên
nhân gốc thì vẫn đang chi phối split, nên chúng ở lại.

## Ba cái bẫy đã trả giá, đừng lặp lại

**Đừng suy ra nút thắt từ việc một tầng kết thúc cuối cùng.** Ring buffer hữu hạn:
tầng dưới chậm thì decode bị chặn và giãn ra bằng tầng chậm nhất. "Decode == wall"
xuất hiện ở 12/12 lượt và kết luận rút ra từ đó là **sai**. Chỉ ladder mới kết luận
được, vì nó bỏ hẳn tầng dưới ra khỏi phép đo.

**Đừng tin `nproc` hay `os.cpu_count()` trong container.** Đọc cgroup `cpu.max`.
Box 4070 báo 16 nhưng quota 15.36; một box vast.ai báo 192 trong khi quota 23 — và
chính chỗ đó ăn mất 5× runtime.

**Đừng tin `get_available_providers()`.** Nó liệt kê thứ wheel được build kèm,
không phải thứ session sẽ nhận. Xác minh bằng `InferenceSession` thật.
