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
- `camera.perception`: threshold/device/model path cho detector, tracker, face identity
- `camera.pipelines[]`: binding pipeline và business params riêng từng luồng

**Không có file config riêng cho từng camera.** Một store là một file JSON; sửa
camera nào thì mở đúng file store đó và sửa trong `cameras[]` của nó. Một hay hai
mươi camera cũng vậy.

## Ngưỡng là của từng camera, không phải của hệ thống

`match_threshold`, `match_margin`, `conf_thres`, các ngưỡng `person_tracker.*`,
khối `fsm` của camera quầy, và các ngưỡng vùng của camera kho — **tất cả đều độc
lập theo từng camera**, không có giá trị "chuẩn toàn hệ thống" nào để đối chiếu.
Hai camera cùng loại đặt khác nhau là **bình thường**, không phải lệch cấu hình:
góc đặt, độ cao, ánh sáng, khoảng cách khác nhau thì ngưỡng hợp lý cũng khác.

Hiện `store_1..4` dùng `match_threshold: 0.25`, còn `local_dev_store*` dùng `0.30`.
Cả hai đều đúng chủ trương — **đừng "chuẩn hoá" chúng về một số.**

Code có mặc định `0.30 / 0.10` (`storage_intrusion.py`), chỉ dùng khi manifest
không khai. Ngưỡng thấp hơn = dễ gán tên cho một khuôn mặt lạ hơn, nên chỉnh nó là
quyết định nghiệp vụ của từng camera, không phải tinh chỉnh kỹ thuật.

### Ngưỡng nào đã nối tới đâu

### `runtime.target_fps` điều khiển tất cả

Một số duy nhất, khai ở `cameras[].runtime.target_fps` (không khai thì lấy
`runtime.default_target_fps` cấp store). Nó đi vào `CameraStreamSpec.target_fps`
rồi chi phối **cả nguồn lẫn nghiệp vụ**:

| nơi dùng | quy đổi |
| --- | --- |
| nguồn (video **và** rtsp) | `source_step = native_fps / target_fps` — quyết định lấy frame nào |
| tracker | `max(1, round(giây × fps))` cho `MAX_AGE_S`, `N_FEAT_S`, `PERSON_GRACE_S`, `HAND_TRAJ_S`… |
| FSM quầy | 8 hằng thời gian, `frames_10fps × fps / 10` |
| zone kho | `warehouse.Config(fps=)` → `entry_frames`, `exit_frames` |
| till | `compute_till_schedule(fps, till_confirm_frames, till_fps)` |
| clip cảnh báo | `AlertClipBuffer(fps, pre_s, post_s)` |
| `customer_recent_window_s` | `round(giây × fps)` |
| đồng hồ giả lập | `elapsed_s = frame_idx / fps` |

Không chỗ nào hardcode fps. Đổi `target_fps` là mọi cửa sổ tự đổi theo.

**`target_fps` không được vượt tốc độ nguồn.** Nếu vượt, `source_step` tụt xuống
dưới 1, mọi frame nguồn đều lọt qua, luồng phát ra `native_fps` trong khi mọi ngưỡng
đã tính theo `target_fps` — tức mỗi cửa sổ xác nhận phủ nhiều thời gian thực hơn ý
định, và **không có chỉ số nào bất thường**. Nay:

- **video file** — báo lỗi ngay lúc mở. `native_fps` đọc từ file là số đo chắc chắn.
- **rtsp, camera có báo fps** — báo lỗi ngay.
- **rtsp, camera không báo fps** — **chỉ cảnh báo**, vì lúc đó `native_fps` là số
  đoán (`stream.rtsp.native_fps` nếu có, không thì hằng 15.0). Từ chối dựa trên một
  con số đoán sẽ hạ một camera đang chạy tốt chỉ vì OpenCV không đọc được header.

Camera RTSP thật rất hay không báo fps. Khi đó khai tay:

```json
"stream": { "input_mode": "rtsp", "uri": "rtsp://...", "rtsp": { "native_fps": 25 } }
```

### `camera.perception` — nhận thức

| khoá | tới |
| --- | --- |
| `person_detector.conf_thres` | ngưỡng detect người |
| `person_detector.model_path` | file pose model của riêng camera này |
| `person_tracker.<tên_config>` | bất kỳ trường nào của `uae_tracker.Config`, viết thường — ví dụ `step_a_max_cost` |
| `person_tracker.reid_model_path` | file ReID model |
| `face_identity.face_conf` | ngưỡng **detect** khuôn mặt |
| `face_identity.match_threshold` / `match_margin` | ngưỡng **match** khuôn mặt |
| `face_identity.crop_expand` / `iou_thres` | cắt và NMS của face detector |
| `face_identity.model_path` / `arcface_model_path` | file model detect mặt và ArcFace |

Đường dẫn model chấp nhận `${BACKEND_ROOT}` / `${DATA_ROOT}`.

### `pipelines[].params` — nghiệp vụ

Camera **quầy** (`counter_cash_theft_v1`), khối `fsm`:

| nhóm | khoá | mặc định |
| --- | --- | --- |
| hình học (đơn vị: độ dài thân người) | `drawer_in` / `drawer_out` | 0.80 / 0.90 |
| | `deep_in` / `withdraw_delta` | 0.35 / 0.25 |
| | `pocket_radius` / `hand_follow_gate` | 0.30 / 0.60 |
| | `drawer_roi_expand` / `wrist_conf_th` | 0.25 / 0.30 |
| thời gian (**giây**) | `hand_inside_confirm_s` / `hand_exit_confirm_s` | 0.3 / 0.3 |
| | `pose_grace_s` / `dwell_confirm_s` | 0.5 / 0.5 |
| | `state_timeout_s` / `post_event_s` | 5.0 / 3.0 |
| | `traj_window_s` / `traj_lookback_s` | 2.5 / 0.5 |
| cờ | `any_hand` | false |

Nhóm hình học **không sống sót qua thay đổi góc camera**. Chúng chuẩn hoá theo độ
dài thân người nên chịu được khoảng cách, nhưng một quầy quay từ 3 m chéo xuống
không đặt cổ tay ở cùng khoảng cách chuẩn hoá tới tâm ngăn kéo như quầy quay ngang
từ 2 m. Số hiện tại đo trên **một clip**. `drawer_in` phải **nhỏ hơn** `drawer_out`
— đó là cặp trễ (hysteresis), khai ngược sẽ báo lỗi ngay.

Thời gian khai bằng **giây**, không phải frame. Trong code chúng là "frame ở 10fps"
vì đó là chỗ đo ra, không phải tốc độ camera nào chạy thật.

Camera **kho** (`storage_intrusion_v1` / `cold_storage_intrusion_v1`):

| khoá | mặc định | tác dụng |
| --- | --- | --- |
| `entry_confirm_seconds` | 1.0 | giây trong vùng mới tính là vào |
| `exit_confirm_seconds` | 4.0 | giây ngoài vùng mới tính là ra |
| `presence_confirm_frames` | 5 | frame liên tiếp để xác nhận có mặt |
| `exit_on_loss_min_frames` | 3 | mất track ở ngoài vùng bao lâu thì tính là đã ra |
| `anchor` | `pelvis` | điểm đại diện người: `pelvis` hoặc `bottom_center` |

`exit_confirm_seconds` là vế quyết định **người rời đi có được ghi nhận là rời đi
hay không**. Giá trị 4.0 đo trên một clip kho; cửa ở mép khung hình, lối đi đông
hơn, hay camera lùi xa đều làm nó lệch.

Tracker nhận một lớp `Config` **con** riêng cho từng camera, không sửa `Config`
gốc — trong split tier nhiều camera là thread trong cùng một process, ghi đè lên
`Config` sẽ retune lẫn nhau, đúng lỗi mà FSM globals từng mắc. Tên không có
trong `Config` thì **báo lỗi**, vì một ngưỡng tracker không ai đọc là cách tốn
một ngày chỉnh một con số vô nghĩa.

### `match_thres` đã bị gỡ

Nó có trong mọi manifest từ commit đầu, **không code nào đọc**, và không có
khoá tương ứng nào trong `uae-main` (`defaults.py` chỉ có `STEP_A/B/C/D.MAX_COST`).
Nên giá trị 0.65 không truy được ra nghĩa gì — trong khi 0.65 nằm trên mốc 0.5
mà chính `uae_tracker.py` chú thích là "hai người khác nhau".

Tracker vẫn chạy `STEP_A_MAX_COST = 0.25`, đúng mặc định của uae-main và đúng
con số mọi vòng benchmark đã dùng. Muốn chỉnh theo camera thì khai bằng tên thật:

```json
"person_tracker": { "step_a_max_cost": 0.3 }
```

Tên đó không mơ hồ, và sai tên thì báo lỗi ngay.

### Một khoá chưa tới đâu, và vì sao

`device` — **giữ lại có chủ đích**, đón trước multi-GPU/multi-node (`cuda:0`,
`cuda:1`). Hiện chưa đọc vì kiến trúc split nạp model **một lần cho cả tier** và
dùng chung 16 camera; đặt provider khác nhau theo camera cần nhiều bộ session,
tức thêm CUDA context — đã đo là 19ms ở 2 context, 45ms ở 4. Việc này thuộc bước
scale ra nhiều node, không phải sửa cấu hình. Đây là khoá **duy nhất** validator
còn cảnh báo "configures nothing".

### Bảy khoá đã bị xoá hẳn (viết lại sẽ báo lỗi)

Khác với `device`, mỗi khoá dưới đây mô tả một hành vi hệ thống **không có** và
sẽ bị tin là có. Nên chúng bị gỡ khỏi schema chứ không hạ xuống mức cảnh báo —
cảnh báo chỉ mời người sau đi nối chúng.

| khoá | vì sao không nối |
| --- | --- |
| `runtime.drop_if_lagging` | không có nhánh drop nào để bật. Ring **chặn** (đó là cơ chế điều tiết của nó), còn nguồn RTSP **luôn** decimate vì camera không chờ được |
| `policies.identity_gallery_scope` | mọi call site đều truyền cả `store_id` lẫn `company_id`, nên scope luôn hẹp nhất. Đặt `"company"` sẽ trông như đã cấu hình mà không đổi gì — và bài test gallery 4 store dựa vào việc lọc theo store |
| `policies.default_alert_clip` | mặc định thật là số `15.0` trong pipeline. `alert_clip` theo **từng binding** mới là chỗ đặt, và đó mới là mức đúng: alert quầy và alert kho trên cùng camera muốn độ dài khác nhau |
| `mode` | nhãn không nhánh nào rẽ theo |
| `perception.person_detector.nms_thres` | pose head là **NMS-free** (`stream_track.py`: `(300, 57) NMS-free`). Không phải "chưa nối" mà là **không tồn tại chỗ để nối** |
| `params.emit_customer_tracks` | mô tả hành vi không có |
| `params.face_capture_window.start_status` | không code nào đọc |

`display_name` cấp store thì **ngược lại** — nó đi vào cột `stores.display_name`
(`NOT NULL`, mọi dashboard đọc). Trước đây bị bỏ qua và cột ghi đại `store_id`.

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
