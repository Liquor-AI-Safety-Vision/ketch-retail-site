# Solution Architecture

Phiên bản: `v0.2`

Ngày cập nhật: `2026-08-23`

## 1. Mục tiêu tài liệu

Tài liệu này là blueprint kỹ thuật cho hệ thống backend surveillance retail multi-store.

Tài liệu phục vụ 3 mục đích:
- Chốt hướng kiến trúc để phát triển local trước, production sau.
- Xác định boundary giữa runtime, pipeline nghiệp vụ, storage và frontend delivery.
- Tạo reference implementation plan để code trong `backend/`.

Tài liệu này không thay thế business docs đã có trong `docs/01_quay_thu_ngan.md` và `docs/02_kho_ngoai_gio.md`.
Nó đóng vai trò cầu nối giữa:
- business/problem definition
- module POC hiện có trong `scripts/`
- backend system cần xây dựng để chạy end-to-end

## 2. Phạm vi hệ thống

Hệ thống mục tiêu:
- Nhận nhiều luồng camera theo thời gian thực.
- Mỗi camera đi qua một pipeline riêng.
- Backend xử lý frame, phát hiện event, tạo alert, cắt clip bằng chứng và lưu kết quả.
- Frontend hiển thị alert, clip, identity, trạng thái run.
- Hỗ trợ scale lên nhiều store, mỗi store có bộ camera và bộ pipeline khác nhau.

Trạng thái implementation giai đoạn đầu:
- Chạy local, coi máy local như backend server.
- Nguồn video là `virtual stream` phát từ file video thay cho RTSP/camera thật.
- Metadata lưu bằng SQLite.
- Media lưu trên filesystem local.

Trạng thái mục tiêu sau này:
- Backend inference/runtime chạy trên server cloud.
- Metadata lưu trên Neon Postgres.
- Media lưu trên Cloudflare R2.
- Frontend/API edge có thể dùng Cloudflare Workers.
- Nếu cần Worker truy cập Neon thì dùng Cloudflare Hyperdrive.

## 3. Nguyên tắc kiến trúc

### 3.1. Local-first, production-shape

Hệ thống local không phải demo riêng. Đây là production architecture với local adapters.

Cái được phép khác so với production:
- `VideoFileStreamSource` thay cho `RtspStreamSource`
- `SQLiteEventStore` thay cho `NeonEventStore`
- `LocalMediaStore` thay cho `R2MediaStore`

Còn lại:
- pipeline runtime
- event contracts
- alert schema
- control plane
- evidence lifecycle

đều phải giữ nguyên tư duy production.

### 3.2. Modular monolith trước, tách adapter sau

Giai đoạn này không dùng microservice thật và không bắt buộc Kafka thật.

Lựa chọn:
- Một backend process chính.
- Nhiều worker/runtime trong cùng process.
- Queue nội bộ per-camera.
- Adapter abstraction để sau này thay bằng broker/service thật.

Lý do:
- Dễ debug.
- Dễ iterate threshold/rule nhanh.
- Không khóa tay kiến trúc sau này.

### 3.3. Cô lập state theo camera

Mỗi camera có state riêng:
- tracker
- zone monitor
- till state
- face candidate window
- ring buffer video
- alert cooldown
- runtime metrics

Không được trộn state giữa các camera.

### 3.4. Batch nếu có thì chỉ ở inference layer

Stateful logic không được batch chung:
- tracker
- FSM
- zone state
- alerting
- evidence recorder

Nếu sau này tối ưu:
- chỉ batch detector/embedder stateless
- thông qua shared `InferenceExecutor`

## 4. Kịch bản nghiệp vụ mục tiêu

Giai đoạn xây dựng đầu tiên hỗ trợ 4 luồng camera:

1. `counter`
   - Pipeline: `counter_cash_theft_v1`
   - Scope: phát hiện hành vi lấy tiền từ ngăn kéo khi không có khách ở vị trí checkout/thanh toán.
   - Không tách thành một bài toán riêng "mở ngăn kéo".

2. `storage`
   - Pipeline: `storage_intrusion_v1`
   - Rule:
     - `unknown` vào kho => alert
     - `staff` vào kho ngoài giờ => alert

3. `cold_storage`
   - Pipeline: `cold_storage_intrusion_v1`
   - Cùng rule với `storage`, khác camera config/zone/allowed window.

4. `whole_store`
   - Pipeline: `whole_store_tracking_v1`
   - Tracking customer là phạm vi hiện tại.
   - Sau này có thể thêm `shoplifting_v1`.

Mở rộng đã được dự tính:
- `product_counting_v1`
- `shelf_restocking_v1`
- `cashier_presence_v1`
- các pipeline custom theo từng store

## 5. Tài sản POC hiện có và cách tái sử dụng

POC code hiện tại là input cho backend mới, không phải đích đến cuối.

| Module hiện có | Vai trò backend mới |
|---|---|
| `scripts/stream_track.py` | nguồn detector/embedder/draw helper; tách lại thành perception adapter |
| `scripts/uae_tracker.py` | core tracker stateful, tái sử dụng |
| `scripts/run_counter.py` | tham chiếu cho till detector + role assignment counter |
| `scripts/problem2_fsm.py` | rule engine counter cash theft để đưa vào pipeline runtime |
| `scripts/warehouse.py` | zone state machine cho storage/cold_storage |
| `scripts/run_warehouse.py` | tham chiếu offline runner |
| `scripts/face_align.py` | face detect + 5-landmark align |
| `scripts/face_identity.py` | face embedding + gallery + matching |

Nguyên tắc:
- Tái sử dụng logic nghiệp vụ.
- Không tái sử dụng nguyên xi runner/demo offline.
- Runtime mới phải dựa vào contracts thống nhất.

## 6. Kiến trúc tổng thể

```mermaid
flowchart LR
    A[Store Config] --> B[Runtime Supervisor]
    B --> C[Camera Runtime 1]
    B --> D[Camera Runtime 2]
    B --> E[Camera Runtime N]

    C --> F[Virtual or RTSP Stream Source]
    D --> F2[Virtual or RTSP Stream Source]

    C --> G[Perception Stack]
    D --> G2[Perception Stack]

    G --> H[Pipeline Logic]
    G2 --> H2[Pipeline Logic]

    H --> I[Alert Service]
    H2 --> I
    I --> J[SQLite or Neon Event Store]
    I --> K[Local FS or R2 Media Store]
    J --> L[API / Frontend Feed]
    K --> L
```

## 7. Tổ chức theo multi-store

Đây là hệ thống multi-tenant nhẹ theo `store_id`.

Hierarchy:

```text
Store
  -> Cameras
  -> Pipeline bindings
  -> Zone assets
  -> Allowed windows
  -> Identity gallery scope
  -> Alert policies
```

Phải thiết kế sao cho:
- store A có bộ pipeline riêng
- store B thêm module mới
- store C tắt face matching

mà không cần fork code runtime.

Điều kiện để scale được:
- Pipeline registry theo tên/version
- Camera bind vào pipeline bằng config
- Pipeline params override theo store/camera

## 8. Cấu trúc thư mục để xây dựng hệ thống mới

```text
backend/
├── docs/
│   └── SOLUTION_ARCHITECTURE.md
├── src/
│   ├── app/
│   ├── domain/
│   ├── pipelines/
│   ├── adapters/
│   ├── runtime/
│   ├── infra/
│   └── api/
├── config/
│   ├── stores/
│   ├── pipelines/
│   └── zones/
├── sql/
└── tests/
```

Ý nghĩa từng nhóm:

- `src/app/`
  - wiring, DI nhẹ, startup

- `src/domain/`
  - entities, enums, event contracts, schemas

- `src/pipelines/`
  - pipeline implementations theo nghiệp vụ

- `src/adapters/`
  - wrappers cho detector, tracker, face identity, till detector

- `src/runtime/`
  - `CameraRuntime`, `RuntimeSupervisor`, evidence buffer, queue

- `src/infra/`
  - SQLite, filesystem, Neon, R2, metrics, config loader

- `src/api/`
  - REST/SSE/WebSocket cho frontend và control plane

## 9. Thành phần hệ thống

### 9.1. Control Plane

Trách nhiệm:
- Nạp cấu hình store/camera/pipeline
- Start/stop runtime
- Query health, metrics, active alerts
- Expose API cho frontend

Không làm:
- Không chạy CV inference
- Không giữ state track logic

### 9.2. Runtime Supervisor

Trách nhiệm:
- Tạo `CameraRuntime` cho mỗi binding active
- Quản lý lifecycle runtime
- Restart worker nếu lỗi
- Tổng hợp metrics

### 9.3. Camera Runtime

Đây là đơn vị xử lý stateful cơ bản nhất.

Mỗi `CameraRuntime` gồm:
- stream source
- frame sampler
- perception stack
- event pipeline
- evidence ring buffer
- alert emitter
- local metrics

Pseudo-shape:

```text
CameraRuntime
├── camera_config
├── stream_source
├── frame_queue
├── perception_context
├── pipeline_context
├── evidence_buffer
├── alert_sink
└── metrics
```

### 9.4. Stream Source

Interface:
- `VideoFileStreamSource`
- `RtspStreamSource`

Hợp đồng:
- Trả frame theo wall-clock.
- Downsample theo `target_fps` của từng camera.
- Có thể drop frame nếu mode streaming thật được bật.

Lưu ý:
- Giai đoạn local simulation vẫn phải có pacing thời gian thật.
- Không được đọc nhanh hết video rồi mới xử lý.
- `stream.input_mode` là config, không hardcode:
  - `video_file`: local-dev, replay theo thời gian thật
  - `rtsp`: production hoặc staging với camera thật

### 9.4.1. Mô phỏng giờ nghiệp vụ trong local-dev

Đây là điểm quan trọng cho bài toán kho.

Trong production:
- thời gian check rule lấy từ clock thật của server theo timezone của store

Trong local-dev:
- video vẫn là file
- nhưng mỗi frame phải được gán một `simulated_local_time`
- thời gian này được suy ra từ:
  - `store.timezone`
  - `runtime.simulation_clock.started_at_local`
  - thời gian phát của stream

Ví dụ:
- manifest local-dev đặt `started_at_local = 2026-08-23T20:30:00`
- video chạy 3 phút
- vậy toàn bộ runtime sẽ mô phỏng khoảng `20:30 -> 20:33` theo giờ store

Khi đó pipeline `storage_intrusion_v1` và `cold_storage_intrusion_v1` vẫn đánh giá rule "vào trong giờ hay ngoài giờ" giống production, chỉ khác nguồn thời gian là đồng hồ mô phỏng.

### 9.5. Perception Stack

Perception stack là tập module có thể tái sử dụng giữa nhiều pipeline:
- person detector / pose
- tracker
- role assignment
- till detector
- face detect + align + embed + match

Đầu ra của nó là `PerceptionFrame`.

### 9.6. Pipeline Logic

Mỗi pipeline nhận:
- `FrameEnvelope`
- `PerceptionFrame`
- `PipelineContext`

Và trả:
- zero hoặc nhiều `DomainEvent`
- zero hoặc nhiều `AlertCandidate`

### 9.7. Alert Service

Trách nhiệm:
- Chuyển `AlertCandidate` thành alert persisted
- Cắt evidence clip/snapshot/face chip
- Ghi DB
- Đẩy thông báo sang frontend stream

### 9.8. Event Store

Phase local:
- SQLite

Phase cloud:
- Neon

Lưu ý:
- DB chính lưu business objects
- Không lưu full frame-perception vào bảng chính

### 9.9. Media Store

Phase local:
- filesystem local

Phase cloud:
- Cloudflare R2

Media cần lưu:
- alert clip
- snapshot
- face chip
- optional debug montage

## 10. Quy tắc cho person tracking nhiều camera

Quyết định kiến trúc:
- Tracking state luôn độc lập theo camera.
- Không batch tracker giữa nhiều camera.

Có thể tối ưu sau:
- shared inference executor cho detector/embedder
- micro-batching frame stateless trong cửa sổ 10-30 ms

Nhưng:
- `track_id`
- `zone state`
- `FSM state`
- `face window`
- `evidence buffer`

vẫn thuộc từng camera runtime.

## 11. Internal contracts

### 11.1. FrameEnvelope

```text
FrameEnvelope
- store_id
- camera_id
- camera_type
- frame_idx
- ts_utc
- simulated_local_ts (optional nhưng nên có trong local-dev)
- source_kind
- source_uri
- image_bgr
```

### 11.2. PerceptionFrame

```text
PerceptionFrame
- persons[]
- tracks[]
- roles[]
- till_states[]
- faces[]
- zone_hits[]
- debug_refs
```

### 11.3. DomainEvent

```text
DomainEvent
- store_id
- camera_id
- pipeline_type
- event_type
- ts_utc
- track_ref
- identity_ref
- reason_codes[]
- payload_json
```

### 11.4. AlertCandidate

```text
AlertCandidate
- store_id
- camera_id
- pipeline_type
- event_type
- severity
- opened_at
- identity_decision
- reason_codes[]
- evidence_request
- metadata_json
```

### 11.5. PersistedAlert

```text
PersistedAlert
- alert_id
- store_id
- camera_id
- pipeline_type
- event_type
- severity
- status
- opened_at
- closed_at
- identity_person_id
- identity_label
- identity_score
- reason_codes_json
- metadata_json
```

## 12. Data model cho DB chính

### 12.1. Config tables

#### `stores`
- `store_id` PK
- `store_code`
- `display_name`
- `timezone`
- `metadata_json`

#### `cameras`
- `camera_id` PK
- `store_id` FK
- `camera_code`
- `display_name`
- `camera_type`
- `source_type`
- `source_uri`
- `is_active`
- `metadata_json`

#### `pipeline_templates`
- `pipeline_template_id` PK
- `pipeline_type`
- `version`
- `description`

#### `camera_pipeline_bindings`
- `binding_id` PK
- `camera_id` FK
- `pipeline_template_id` FK
- `is_active`
- `config_json`

#### `zone_assets`
- `zone_asset_id` PK
- `camera_id` FK
- `asset_type`
- `path`
- `checksum`

### 12.2. Runtime/business tables

#### `pipeline_runs`
- `run_id` PK
- `camera_id`
- `started_at`
- `stopped_at`
- `status`
- `source_ref`
- `metrics_json`

#### `alerts`
- `alert_id` PK
- `store_id`
- `camera_id`
- `run_id`
- `pipeline_type`
- `event_type`
- `severity`
- `status`
- `opened_at`
- `closed_at`
- `identity_person_id`
- `identity_label`
- `identity_score`
- `reason_codes_json`
- `metadata_json`

#### `alert_media`
- `media_id` PK
- `alert_id` FK
- `kind`
- `path`
- `mime_type`
- `started_at`
- `ended_at`
- `metadata_json`

#### `alert_identities`
- `id` PK
- `alert_id` FK
- `track_id`
- `person_id`
- `display_name`
- `top1_score`
- `top2_score`
- `margin_score`
- `decision_reason`
- `metadata_json`

### 12.3. Identity tables

#### `persons`
- `person_id` PK
- `display_name`
- `is_staff`
- `company_id`
- `store_id`
- `metadata_json`
- `created_at`
- `updated_at`

#### `face_embeddings`
- `embedding_id` PK
- `person_id` FK
- `embedding`
- `detector_score`
- `image_path`
- `source`
- `metadata_json`
- `created_at`

## 13. Điểm quan trọng: cái gì không đưa vào DB chính

Không nên lưu vào DB chính:
- mỗi frame track result
- mỗi frame face observation
- full keypoint stream
- raw debug overlays

Nếu cần debug sau:
- lưu JSONL sidecar theo run/camera
- lưu artifact file trong `outputs/` hoặc object storage
- bật bằng `debug_mode`

Lý do:
- frame-level data tăng quá nhanh
- SQLite/Neon không phải nơi tốt nhất cho telemetry thô
- frontend business UI không cần query đến mức frame

## 14. Face identity policy cho storage/cold_storage

### 14.1. Detect và align

Rule đã chốt:
- Không detect face trên full frame.
- Chỉ detect trong `person crop`.
- Face detector phải có 5 landmarks.
- Align về `112x112`.

### 14.2. Match rule

Rule đã chốt:
- `top1 >= 0.3`
- `top1 - top2 >= 0.1`
- Nếu không đạt => `unknown`

### 14.3. Entry window policy

Rule đã chốt:
- Bắt đầu thu thập face candidate từ lúc track vào `PENDING_IN`
- Giữ cửa sổ đến `INSIDE + 1s`
- Lấy candidate tốt nhất trong toàn bộ cửa sổ
- Nếu hết cửa sổ vẫn không có match chắc chắn => `unknown`

Lý do:
- Mặt đẹp nhất không nhất thiết nằm đúng ở frame `INSIDE`
- Nếu chỉ chụp tại `INSIDE`, dễ mất cả frame đẹp trước/sau
- Nếu chỉ đợi sau `INSIDE`, có nguy cơ người quay đầu đi mất

## 14.4. Access schedule policy

Rule nghiệp vụ không hardcode trong code.

Pipeline kho phải đọc config kiểu:

```yaml
access_schedule:
  timezone: Asia/Ho_Chi_Minh
  allowed_windows:
    - days: [mon, tue, wed, thu, fri, sat, sun]
      start: "08:00"
      end: "09:00"
    - days: [mon, tue, wed, thu, fri, sat, sun]
      start: "21:00"
      end: "22:00"
```

Ý nghĩa:
- `unknown` vào kho: luôn alert
- `staff` vào kho:
  - nếu nằm trong `allowed_windows` => không alert theo rule ngoài giờ
  - nếu ngoài `allowed_windows` => alert

Lưu ý:
- schedule là tham số per-pipeline per-camera
- `storage_1` và `cold_storage_1` có thể khác giờ nhau
- sau này nếu có multi-store thì mỗi store/camera có thể khác hoàn toàn

## 15. Counter pipeline scope

Counter pipeline giai đoạn này được chốt là:
- `counter_cash_theft_v1`

Scope:
- phát hiện chuỗi hành vi lấy tiền từ ngăn kéo
- chỉ alert khi không có khách ở vị trí checkout/thanh toán

Không tồn tại một pipeline riêng:
- `drawer_open_without_customer_v1`

Nếu cần thông tin phụ:
- event "drawer_open" có thể tồn tại trong internal reasoning/debug
- nhưng không là business alert riêng

## 16. Whole-store pipeline scope

Giai đoạn đầu:
- tracking customer
- lưu runtime state, people counts, debug overlays nếu cần

Sau này mở rộng:
- `shoplifting_v1`
- behavioral analytics
- heatmap/flow analytics

## 17. Storage plan: SQLite -> Neon + Cloudflare

### 17.1. Phase local

- `SQLite`
  - config
  - alerts
  - identity metadata

- `Local filesystem`
  - clip
  - snapshot
  - face chip

### 17.2. Phase cloud

- `Neon Postgres`
  - config
  - runtime metadata
  - alerts
  - identities

- `Cloudflare R2`
  - alert clip
  - snapshot
  - face chip

- `Cloudflare Workers`
  - frontend-facing API
  - auth/session
  - optional control-plane edge functions

- `Cloudflare Hyperdrive`
  - chỉ dùng khi Worker cần truy cập Neon/Postgres theo cách tối ưu kết nối

Quyết định kiến trúc:
- CV inference và stateful runtime không chạy trong Worker
- nó phải chạy trên backend server/app

## 18. Cấu hình và registry

Hệ thống phải config-driven.

### 18.1. Store manifest

Mỗi store cần có manifest:

```yaml
store_id: store_hcm_001
timezone: Asia/Ho_Chi_Minh
runtime:
  default_target_fps: 5
  target_duration_s: 180
  simulation_clock:
    enabled: true
    started_at_local: "2026-08-23T20:30:00"
cameras:
  - camera_id: counter_1
    type: counter
    stream:
      input_mode: video_file
      uri: videos/cctv_counter/top-side-view.mp4
      simulation:
        strategy: trim
        start_s: 0
        target_duration_s: 180
    runtime:
      target_fps: 5
      device: cpu
    perception:
      person_detector:
        conf_thres: 0.5
      person_tracker:
        match_thres: 0.65
    pipelines:
      - pipeline_id: counter_cash_theft_v1
  - camera_id: storage_1
    type: storage
    stream:
      input_mode: rtsp
      uri: rtsp://user:pass@cam-ip/stream
    runtime:
      target_fps: 5
      device: cuda:0
    perception:
      person_detector:
        conf_thres: 0.5
      person_tracker:
        match_thres: 0.5
      face_identity:
        match_threshold: 0.3
        match_margin: 0.1
    pipelines:
      - pipeline_id: storage_intrusion_v1
        params:
          entry_confirm_seconds: 1.0
          access_schedule:
            timezone: Asia/Ho_Chi_Minh
            allowed_windows:
              - days: [mon, tue, wed, thu, fri, sat, sun]
                start: "08:00"
                end: "09:00"
              - days: [mon, tue, wed, thu, fri, sat, sun]
                start: "21:00"
                end: "22:00"
```

Nguyên tắc của schema:
- `runtime` cấp store chứa default chung
- `camera.runtime` override cho từng luồng
- `camera.perception` chứa threshold, device, module toggle
- `camera.pipelines[]` chứa business params riêng của pipeline
- cùng một pipeline nhưng camera A và camera B có thể khác config hoàn toàn

### 18.2. Source mode

`stream.input_mode` hiện được chốt hỗ trợ hai loại:
- `video_file`
  - dùng cho local-dev hoặc replay
  - cần `simulation.strategy`, `start_s`, `target_duration_s`
- `rtsp`
  - dùng cho camera thật
  - có thể mở rộng thêm `transport`, `reconnect`, `auth_ref`

Điều quan trọng:
- business schema và pipeline params không đổi khi đổi từ `video_file` sang `rtsp`
- chỉ adapter `StreamSource` thay đổi

### 18.3. Device override

Thiết kế config phải cho phép override theo camera, ví dụ:
- `counter_1.person_tracker.match_thres = 0.65`
- `storage_1.person_tracker.match_thres = 0.50`
- `storage_1.runtime.device = cuda:0`
- `cold_storage_1.face_identity.device = cpu`

Nếu sau này cần sâu hơn, cho phép tách device theo module:
- detector -> `cuda:0`
- face_identity -> `cuda:1`
- tracker/FSM -> `cpu`

### 18.4. Pipeline registry

Pipeline registry là map:

```text
pipeline_type -> pipeline factory
```

Ví dụ:
- `counter_cash_theft_v1`
- `storage_intrusion_v1`
- `cold_storage_intrusion_v1`
- `whole_store_tracking_v1`

## 18.5. Manifest local-dev hiện tại

Manifest mẫu đã được nâng cấp tại:
- [backend/config/stores/local_dev_store.json](/Users/ngoquangduc/Desktop/workspace/liquir_store/backend/config/stores/local_dev_store.json)

Nó đã thể hiện sẵn:
- source mode `video_file`
- replay strategy `trim` hoặc `loop_to_duration`
- `simulation_clock` để giả lập giờ nghiệp vụ
- `access_schedule` cho kho và kho lạnh
- override threshold tracker theo từng camera
- config face matching với threshold `0.3`

## 19. Evidence policy

Mỗi camera runtime có ring buffer riêng.

Mặc định đề xuất:
- pre-event: `15s`
- post-event: `15s`
- tổng clip: `30s`

Một số pipeline có thể override:
- storage intrusion có thể cần `10s + 20s`
- counter có thể cần `8s + 12s`

Evidence artifact:
- `alert clip`
- `snapshot at trigger`
- `face chip` nếu có identity

## 20. Observability

Cần có ngay từ đầu:
- runtime status theo camera
- processing FPS
- queue depth
- inference latency
- alert count
- dropped frames
- failed matches

Phase local có thể:
- log file
- SQLite runtime table
- simple `/health` và `/metrics` JSON

## 21. Bảo mật và boundary

### 21.1. Identity data

Face embeddings là dữ liệu nhạy cảm.

Cần quy định:
- không hiện raw embedding ra frontend
- frontend chỉ nhận person id, display label, score cần thiết
- media/face chip có phân quyền truy cập
- gallery match mặc định phải bị giới hạn trong `store_id`; không match cross-store nếu không có policy đặc biệt

### 21.2. Store isolation

Mỗi query frontend phải có `store_id` scope.

Không được:
- query alert cross-store mặc định
- xem camera của store khác nếu không được phân quyền

## 22. Implementation roadmap

### Phase 0 - Foundation
- Tạo `backend/`
- Chốt contracts và schema
- Tạo config store/camera/pipeline manifest

### Phase 1 - Local runtime skeleton
- Runtime supervisor
- Camera runtime
- VideoFileStreamSource
- SQLite + local filesystem adapters
- alert API feed cơ bản

### Phase 2 - Storage and cold storage
- Port `warehouse.py` vào pipeline runtime
- Nối `face_identity.py`
- Entry face window `PENDING_IN -> INSIDE + 1s`
- Alert persistence + clip export

### Phase 3 - Counter
- Port till detector + role assignment
- Port FSM cash theft
- Chốt customer-at-checkout gating

### Phase 4 - Whole store
- Tracking-only pipeline
- Metrics/event cơ bản

### Phase 5 - Cloud-ready adapters
- Neon event store
- R2 media store
- Worker/edge API bridge nếu cần

## 23. Quy tắc code migration từ `scripts/`

Khi đưa code vào backend mới:
- tách `logic` khỏi `runner`
- tách `draw/debug` khỏi `domain`
- đặt adapter ONNX ở `src/adapters/`
- đặt state machine ở `src/pipelines/`
- đặt dataclass/schema ở `src/domain/`

Không copy nguyên file runner vào backend.

## 24. Open questions cần ADR riêng

- Dùng framework API nào cho backend: FastAPI hay sanic/Starlette?
- Queue nội bộ dùng `asyncio.Queue`, threads hay process workers?
- Cần GPU/Metal scheduling layer riêng hay không?
- Frontend sẽ đọc alert bằng SSE hay WebSocket?
- Debug artifacts có đòi hỏi retention policy riêng hay không?

## 25. Kết luận

Kiến trúc đề xuất là:
- local-first
- production-shape
- modular monolith
- config-driven multi-store
- state cô lập theo camera
- SQLite/local filesystem trước
- Neon + Cloudflare sau

Đây là hướng có rủi ro thấp nhất và phù hợp nhất với trạng thái code hiện tại:
- giữ lại được các module POC đã verify
- không bị khóa bởi một stack hạ tầng quá sớm
- vẫn sẵn sàng để scale lên multi-store và thêm pipeline mới
