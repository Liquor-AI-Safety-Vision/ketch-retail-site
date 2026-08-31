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

### Ngưỡng nào đã nối tới đâu

| khoá | tới |
| --- | --- |
| `person_detector.conf_thres` | ngưỡng detect người |
| `person_tracker.match_thres` | `Config.STEP_A_MAX_COST` — cổng association chính |
| `person_tracker.<tên_config>` | bất kỳ trường nào của `uae_tracker.Config`, viết thường |
| `face_identity.face_conf` | ngưỡng **detect** khuôn mặt |
| `face_identity.match_threshold` / `match_margin` | ngưỡng **match** khuôn mặt |
| `face_identity.crop_expand` / `iou_thres` | cắt và NMS của face detector |

Tracker nhận một lớp `Config` **con** riêng cho từng camera, không sửa `Config`
gốc — trong split tier nhiều camera là thread trong cùng một process, ghi đè lên
`Config` sẽ retune lẫn nhau, đúng lỗi mà FSM globals từng mắc. Tên không có
trong `Config` thì **báo lỗi**, vì một ngưỡng tracker không ai đọc là cách tốn
một ngày chỉnh một con số vô nghĩa.

### Hai khoá chưa tới đâu, và vì sao

`nms_thres` — pose head là **NMS-free** (`stream_track.py`: `(300, 57) NMS-free`),
không có NMS để đặt ngưỡng. Không phải "chưa nối" mà là không có chỗ nối.

`device` — giữ lại có chủ đích, đón trước multi-GPU/multi-node (`cuda:0`,
`cuda:1`). Hiện chưa đọc vì kiến trúc split nạp model **một lần cho cả tier** và
dùng chung 16 camera; đặt provider khác nhau theo camera cần nhiều bộ session,
tức thêm CUDA context — đã đo là 19ms ở 2 context, 45ms ở 4. Việc này thuộc bước
scale ra nhiều node, không phải sửa cấu hình.

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
