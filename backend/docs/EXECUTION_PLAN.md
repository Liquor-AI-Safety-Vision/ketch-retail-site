# Kế hoạch triển khai backend local-first

Phiên bản: `v0.1`

Ngày cập nhật: `2026-08-23`

Tài liệu này bám theo [SOLUTION_ARCHITECTURE.md](/Users/ngoquangduc/Desktop/workspace/liquir_store/backend/docs/SOLUTION_ARCHITECTURE.md) và chuyển nó thành thứ tự triển khai thực tế trên máy local trước khi mang sang server GPU.

## 1. Mục tiêu phase hiện tại

Mục tiêu không phải deploy cloud ngay.

Mục tiêu là đưa repo về trạng thái:
- chạy được backend trên máy local như một server inference
- nhận `4` luồng video giả lập như streaming thật
- chạy đúng pipeline theo từng camera
- sinh alert, clip, metadata vào SQLite/local filesystem
- có benchmark entrypoint để sau này SSH vào máy GPU là test tải ngay

## 2. Nguyên tắc thực thi

- Code mới đặt trong `backend/`
- `scripts/` chỉ giữ vai trò nguồn POC, benchmark, và logic để port dần
- Mỗi lần hoàn thiện một phase phải giữ được khả năng chạy local
- Rule nghiệp vụ phải bám config, không hardcode trong runtime

## 3. Trạng thái đã xong

- `backend/` đã có cấu trúc thư mục và tài liệu kiến trúc
- Đã có manifest store/camera/pipeline mẫu cho local-dev
- Đã có virtual stream source phát file như streaming
- Đã có seed DB local cho store/camera/pipeline bindings
- Đã có gallery face local với `person_id`, `display_name`, `company_id`, `store_id`
- Đã có build flow để seed `staff_a`, `staff_b` vào gallery

## 4. Các phase code tiếp theo

### Phase A - Domain contracts

Mục tiêu:
- chuẩn hóa `PerceptionFrame`
- chuẩn hóa `DomainEvent`
- chuẩn hóa `AlertCandidate`
- chuẩn hóa `PersistedAlert`

Output cần có:
- dataclass/domain models
- kiểu event cho `storage_intrusion`, `cold_storage_intrusion`, `counter_cash_theft`

### Phase B - Runtime skeleton

Mục tiêu:
- `RuntimeSupervisor`
- `CameraRuntime`
- queue per-camera
- backpressure policy
- metrics cơ bản

Output cần có:
- 1 entrypoint start runtime từ manifest
- 4 camera runtime cùng chạy local-dev

### Phase C - Perception adapters

Mục tiêu:
- adapter hóa detector/tracker/reid hiện đang nằm ở `scripts/`
- adapter hóa till segmentation
- adapter hóa face identity

Output cần có:
- interface thống nhất cho perception modules
- runtime không gọi trực tiếp các demo runner

### Phase D - Storage pipelines

Mục tiêu:
- port `warehouse.py` vào pipeline runtime
- thêm `access_schedule`
- thêm `face_capture_window`
- match face theo `store_id`

Output cần có:
- `storage_intrusion_v1`
- `cold_storage_intrusion_v1`
- alert reason rõ: `unknown_entry`, `staff_outside_schedule`

### Phase E - Counter pipeline

Mục tiêu:
- port till detector
- port customer/staff gating
- port concealment FSM

Output cần có:
- `counter_cash_theft_v1`
- rule business chạy được trong backend runtime

### Phase F - Whole-store pipeline

Mục tiêu:
- tracking-only runtime trước
- giữ `target_fps = 5`
- chuẩn bị interface để sau này cắm `shoplifting_v1`

### Phase G - Alert persistence + evidence

Mục tiêu:
- persist alert vào SQLite
- snapshot tại trigger
- clip `pre/post` từ ring buffer

Output cần có:
- `alerts`
- `alert_media`
- thư mục artifact local-dev theo run/camera/alert

### Phase H - Benchmark + readiness

Mục tiêu:
- benchmark script local
- checklist mang sang server GPU
- script setup Linux/CUDA

Output cần có:
- đo throughput
- đo dropped frames
- đo alert latency
- kết luận pass/fail theo cấu hình camera hiện tại

## 5. Thứ tự ưu tiên hiện tại

Ưu tiên code trong các turn tiếp theo:

1. `RuntimeSupervisor` + `CameraRuntime`
2. port `storage/cold_storage`
3. alert persistence + clip ring buffer
4. port `whole_store`
5. port `counter`
6. benchmark entrypoint

## 6. Quy tắc gallery identity

Gallery phải mang scope thật:
- `person_id`
- `display_name`
- `is_staff`
- `company_id`
- `store_id`

Rule match:
- runtime camera của store nào thì chỉ match trong gallery của store đó
- không match cross-store mặc định

## 7. Kết luận

Phase hiện tại là phase “đóng hệ thống local chạy được”.

Chỉ khi phase này xong thì việc SSH vào server GPU mới có ý nghĩa:
- lúc đó GPU server dùng để benchmark và tuning
- không phải để vừa viết logic vừa sửa kiến trúc
