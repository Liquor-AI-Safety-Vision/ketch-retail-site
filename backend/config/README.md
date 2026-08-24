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

Các ý quan trọng:
- local-dev vẫn dùng đúng schema production-shape, chỉ khác `stream.input_mode=video_file`
- muốn giả lập giờ nghiệp vụ thì dùng `runtime.simulation_clock.started_at_local`
- pipeline kho/lạnh phải đọc `access_schedule.allowed_windows` từ config, không hardcode trong code

Manifest mẫu hiện tại:
- [stores/local_dev_store.json](/Users/ngoquangduc/Desktop/workspace/liquir_store/backend/config/stores/local_dev_store.json)
