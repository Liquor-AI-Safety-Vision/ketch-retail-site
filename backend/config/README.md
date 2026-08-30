# Config

Nơi đặt:
- manifest theo `store_id`
- camera bindings
- pipeline parameter overrides
- zone asset manifests

Không đặt:
- artifact đầu ra
- debug JSONL

Mục tiêu:
- hệ thống config-driven
- không hardcode camera/pipeline trong code

Thiết kế config hiện tại được chia theo 4 tầng:
- `runtime` cấp store: tham số chạy chung, `default_target_fps`, `simulation_clock`
- `camera.stream`: nguồn vào của từng camera, hỗ trợ cả `video_file` và `rtsp`
- `camera.perception`: threshold/device/module config cho detector, tracker, face identity
- `camera.pipelines[]`: binding pipeline và business params riêng từng luồng

## Ngưỡng là của từng camera, không phải của hệ thống

`match_threshold`, `match_margin`, `conf_thres`, `nms_thres`, `match_thres` — mọi
ngưỡng khai trong `camera.perception` đều **độc lập theo từng camera**, không có
giá trị "chuẩn toàn hệ thống" nào để đối chiếu. Hai camera cùng loại đặt khác nhau
là **bình thường**, không phải lệch cấu hình: góc đặt, ánh sáng, khoảng cách tới
mặt người khác nhau thì ngưỡng hợp lý cũng khác.

Hiện `store_1..4` dùng `match_threshold: 0.25`, còn `local_dev_store*` dùng `0.30`.
Cả hai đều đúng chủ trương — **đừng "chuẩn hoá" chúng về một số.**

Code có mặc định `0.30 / 0.10` (`storage_intrusion.py`), chỉ dùng khi manifest
không khai. Ngưỡng thấp hơn = dễ gán tên cho một khuôn mặt lạ hơn, nên chỉnh nó là
quyết định nghiệp vụ của từng camera, không phải tinh chỉnh kỹ thuật.

## `pipeline_id` phải nằm trong registry

`backend/src/pipelines/registry.py` giữ danh sách id hợp lệ. Một id lạ **báo lỗi
lúc bootstrap**. Trước đây nó rơi vào nhánh mặc định thành tracking pipeline —
camera vẫn chạy, vẫn ra frame, và **không bao giờ cảnh báo**.

Các ý quan trọng:
- local-dev vẫn dùng đúng schema production-shape, chỉ khác `stream.input_mode=video_file`
- muốn giả lập giờ nghiệp vụ thì dùng `runtime.simulation_clock.started_at_local`
- pipeline kho/lạnh phải đọc `access_schedule.allowed_windows` từ config, không hardcode trong code

Manifest mẫu hiện tại:
- [stores/local_dev_store.json](/Users/ngoquangduc/Desktop/workspace/liquir_store/backend/config/stores/local_dev_store.json)
