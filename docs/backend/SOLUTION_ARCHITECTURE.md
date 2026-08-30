# Solution Architecture

Phiên bản: `v0.2`

Ngày cập nhật: `2026-08-30` (bản gốc `2026-08-23`; mục 12 và 17 vá `2026-08-26`, sau đó
mục 5, 6, 8, 9, 11, 18, 22-24 đối chiếu lại với code thật `2026-08-30` — xem ghi chú
"Cập nhật" trong từng mục)

**Cảnh báo khi đọc: bản gốc đặt tên cho nhiều class trước khi chúng được viết, và một số
cái tên đó không bao giờ tồn tại.** Mọi tên class trong doc này đã được đối chiếu với
`backend/src` ngày 30/08; chỗ nào lệch đều có ghi chú tại chỗ.

**Runtime/process architecture (decode/inference/business/face, số đo thật, process/thread
map) không còn nằm ở đây nữa — tài liệu sống hiện tại cho phần đó là**
`docs/backend/split_architecture_design.html`**, mục 1-12.** Doc này giữ vai trò blueprint
nghiệp vụ/data-model ban đầu; phần data model đã sửa lại cho khớp schema thật ở mục 12, phần
storage cloud sửa lại ở mục 17.

## 1. Mục tiêu tài liệu

Tài liệu này là blueprint kỹ thuật cho hệ thống backend surveillance retail multi-store.

Tài liệu phục vụ 3 mục đích:
- Chốt hướng kiến trúc để phát triển local trước, production sau.
- Xác định boundary giữa runtime, pipeline nghiệp vụ, storage và frontend delivery.
- Tạo reference implementation plan để code trong `backend/`.

Tài liệu này không thay thế business docs đã có trong `docs/01_quay_thu_ngan.md` và `docs/02_kho_ngoai_gio.md`.
Nó đóng vai trò cầu nối giữa:
- business/problem definition
- perception stack (nay ở `backend/src/perception/`, xem mục 5)
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

Cái được phép khác so với production (tên class **đã sửa 30/08** — ba cái tên bản gốc
dùng, `VideoFileStreamSource` / `SQLiteEventStore` / `NeonEventStore` / `R2MediaStore`,
không tồn tại trong code):
- `VirtualVideoStreamSource` thay cho `RtspStreamSource`
- `SqliteAlertStore` thay cho `PostgresAlertStore`
- `LocalMediaStore` thay cho `CloudAlertSink` (R2 upload nằm trong sink, không phải một
  media store riêng)

Còn lại:
- pipeline runtime
- event contracts
- alert schema
- control plane
- evidence lifecycle

đều phải giữ nguyên tư duy production.

### 3.2. Modular monolith trước, tách adapter sau

Giai đoạn này không dùng microservice thật và không bắt buộc Kafka thật.

**Cập nhật 2026-08-30: "một backend process chính, nhiều worker trong cùng process" đã
bị thay.** Đo được là hình dạng đó bị GIL chặn, nên runtime hiện tại là nhiều process
tách theo tier (mục 6). Nguyên tắc "chưa dùng microservice/broker thật" thì vẫn giữ.

Lựa chọn:
- Nhiều process tách theo tier, nối bằng shared-memory ring một chiều.
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
- thông qua `SharedInferenceRegistry` + `_BatchRunner` (bản gốc gọi là
  `InferenceExecutor`, tên đó không được dùng)

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

## 5. Perception stack

**Cập nhật 2026-08-30: mục này từng mô tả code POC trong `scripts/` như "input cho
backend mới, không phải đích đến". Việc chuyển đã xong — các module dưới đây nằm
trong `backend/src/perception/` và là code chạy thật, không còn là tài sản tham
chiếu.**

| Module | Vai trò |
|---|---|
| `stream_track.py` | detector + ReID embedder |
| `uae_tracker.py` | DeepSORT tracker stateful |
| `run_counter.py` | till segmentation (`Till`, `TillRegistry`) |
| `problem2_fsm.py` | rule engine counter cash theft |
| `warehouse.py` | zone state machine cho storage/cold_storage |
| `zones.py` | divider, role voter, anchor |
| `face_align.py` | face detect + 5-landmark align |
| `face_identity.py` | face embedding + gallery + matching |

Runner offline (`run_warehouse.py`, các `demo_*.py`) **không** đi cùng — chúng ở
`dev/scripts/` và `demo/`, và phụ thuộc một chiều: dev/demo import vào backend,
backend không import ngược ra. `backend/tests/test_backend_standalone.py` giữ luật
này bằng cách copy `backend/` ra thư mục trống rồi import lại từng module.

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

**Cập nhật 2026-08-30: sơ đồ trên là đường `local` — mỗi camera một
`CameraRuntime` tự cầm perception stack của nó. Nó vẫn tồn tại và vẫn dùng
(`run_local_backend.py`), nhưng KHÔNG phải hình dạng đo benchmark.**

Hình dạng production là **split architecture**: ba tier tách process, nối bằng
shared-memory ring một chiều.

| tier | process | giữ CUDA context |
|---|---|---|
| decode | 8 | không |
| inference | 2 × 8 thread | **có — chỉ ở đây** |
| business | 4 | không |

Lý do chia như vậy là số đo, không phải thẩm mỹ: decode và business giữ GIL,
còn CUDA context tốn 19 ms ở 2 context, 45 ms ở 4, 153 ms ở 8 — nên gom toàn bộ
GPU vào một tier và để hai tier kia scale CPU tự do.

Chi tiết và số đo: `docs/backend/split_architecture_design.html`,
`benchmarks/2026-08-25-split-16cam-l40s.md`.

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

**Cập nhật 2026-08-30: đây là cấu trúc THẬT, không phải đề xuất. Bản cũ vẽ
`src/adapters/` và `src/api/` — cả hai chưa từng được tạo; adapter perception
nằm thẳng trong `src/perception/`, còn API frontend vẫn chưa làm (mục 24).**

```text
backend/                     # đơn vị ship, chạy độc lập
├── src/
│   ├── app/                 # entrypoint: run_split_pipeline, run_local_backend, regression
│   ├── domain/              # dataclass thuần, không I/O
│   ├── perception/          # detector, tracker, zone FSM, face, till
│   ├── pipelines/           # nghiệp vụ theo camera_type
│   ├── runtime/             # tier, ring buffer, supervisor, alert outbox
│   └── infra/               # SQLite, Postgres, filesystem, R2, config loader
├── config/                  # stores/, zones/, staff_seed/
├── models/                  # ONNX runtime thật sự nạp
├── sql/
├── tools/                   # bench + test runner
├── tests/
└── requirements.txt
```

Docs không nằm trong `backend/` — chúng ở `docs/backend/`, vì tài liệu không
cần để chạy hệ thống.

Ý nghĩa từng nhóm:

- `src/app/` — wiring, startup
- `src/domain/` — entities, enums, event contracts
- `src/perception/` — wrapper ONNX cho detector, tracker, face identity, till
- `src/pipelines/` — pipeline theo nghiệp vụ
- `src/runtime/` — `CameraRuntime`, `RuntimeSupervisor`, ba tier tách process, frame ring
- `src/infra/` — SQLite, filesystem, Neon, R2, metrics, config loader

## 9. Thành phần hệ thống

### 9.1. Control Plane

**Cập nhật 2026-08-30: không có component nào tên Control Plane. Trách nhiệm bị
chia ra và hai trong bốn việc chưa tồn tại.**

| trách nhiệm | thực tế |
|---|---|
| Nạp cấu hình store/camera/pipeline | `infra/local_config.py` |
| Start/stop runtime | entry point (`run_split_pipeline.py`, `run_local_backend.py`) — chạy một lần rồi thoát, không có start/stop tại runtime |
| Query health, metrics, active alerts | **chưa có** |
| Expose API cho frontend | **chưa có** — không có HTTP server nào trong `backend/src` |

Ràng buộc vẫn giữ: không chạy CV inference, không giữ state track logic.

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

Hai class, chọn bằng `open_stream_source(spec)` theo `spec.source_kind` — caller
không đặt tên class nào cả:
- `VirtualVideoStreamSource` (`virtual_video_stream.py`) — bản cũ gọi nhầm là
  `VideoFileStreamSource`
- `RtspStreamSource` (`rtsp_stream.py`)

`camera_runtime` và `decode_tier` đều đi qua `open_stream_source`, nên đổi manifest
sang camera thật không cần sửa code. Mỗi call site còn hardcode một trong hai class
là một chỗ sẽ hỏng khi chuyển — hiện chỉ còn `backend/tools/diag_infer_modules.py`,
là công cụ bench nên chấp nhận được.

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

Perception stack là tập module tái sử dụng giữa nhiều pipeline
(`backend/src/perception/`):
- person detector / pose — `stream_track.py`
- tracker — `uae_tracker.py`
- role assignment — `zones.py`
- till detector — `run_counter.py`
- face detect + align + embed + match — `face_align.py`, `face_identity.py`

**Cập nhật 2026-08-30: bản cũ nói "đầu ra của nó là `PerceptionFrame`". Class đó
chưa từng tồn tại.** Ở kiến trúc split, model chạy trong inference tier và kết quả
đi qua queue dưới dạng mảng/tuple thuần, không đóng gói thành một object chung —
`SharedInferenceRegistry` giữ session, `_BatchRunner` gom batch.

### 9.6. Pipeline Logic

**Cập nhật 2026-08-30: bản cũ mô tả interface `(FrameEnvelope, PerceptionFrame,
PipelineContext) -> (DomainEvent[], AlertCandidate[])`. Không có class nào trong
bốn cái đó tồn tại.** Interface thật:

```python
class SomePipeline:
    def process_frame(self, frame: FrameEnvelope) -> None: ...
    def finalize(self) -> int: ...
```

Pipeline **không trả event**. Nó tự gọi `alert_store.create_alert()` /
`add_alert_media()` (mục 11.4) khi quyết định có alert, và giữ state của chính nó
giữa các frame — đó là lý do mỗi camera phải có một instance riêng (mục 3.3), và
lý do `problem2_fsm` phải được nạp thành bản copy riêng cho từng camera.

`finalize()` trả số alert còn treo được đóng lại lúc kết thúc run.

### 9.7. Đường alert

**Cập nhật 2026-08-30: bản cũ mô tả một "Alert Service" gom hết trách nhiệm. Không
có service nào như vậy; việc chia cho bốn thành phần, và ranh giới giữa chúng là
điều đáng biết.**

| thành phần | việc |
|---|---|
| `AlertClipBuffer` | ring buffer pre/post-event, cắt clip và snapshot |
| `LocalMediaStore` | ghi file xuống đĩa |
| `AlertOutbox` | sinh `alert_id` trả về **ngay**, đẩy việc ghi cho một writer thread |
| `SqliteAlertStore` / `PostgresAlertStore` / `CloudAlertSink` | sink thật, chọn qua `sink_factory` |

"Đẩy thông báo sang frontend stream" ở bản cũ **chưa tồn tại** — chưa có API
(mục 23).

Vì sao `AlertOutbox` đứng giữa: pipeline cần `alert_id` ngay lập tức (dùng làm tên
thư mục ghi clip) nhưng **không** cần row đã nằm trong DB tại thời điểm đó — trong
một run không ai đọc alert ngược ra. Khoảng hở đó cho phép đẩy việc ghi ra khỏi
thread pipeline. Một writer thread chứ không phải pool, vì thứ tự kiểu khoá ngoại
(row `alerts` phải có trước `alert_media` trỏ vào nó) chỉ đúng khi có một người ghi.

### 9.8. Event Store và Media Store

**Cập nhật 2026-08-30: bản cũ chia "phase local" và "phase cloud". Không còn là hai
giai đoạn nối tiếp — cả hai chạy song song hôm nay, chọn bằng
`--storage-mode {local-db,cloud-db}` (mục 17).**

| | metadata | media |
|---|---|---|
| `local-db` | SQLite | filesystem |
| `cloud-db` | Neon Postgres | Cloudflare R2 |

Media lưu: alert clip, snapshot, face chip. Ở `cloud-db`, file **luôn ghi xuống đĩa
local trước** (nhanh, không phụ thuộc mạng) rồi mới upload R2 và lưu **object key**
thay vì path local.

DB chính chỉ lưu business object. Không lưu perception từng frame vào bảng chính
(mục 13).

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

**Cập nhật 2026-08-30: mục này từng liệt kê 5 contract. Bốn trong số đó —
`PerceptionFrame`, `DomainEvent`, `AlertCandidate`, `PersistedAlert` — **chưa từng
được viết**; grep toàn bộ `backend/src` cho ra 0 kết quả cho cả bốn cái tên. Dưới
đây là các contract thật, đọc từ `backend/src/domain/models.py`.**

### 11.1. FrameEnvelope

Một frame kèm toàn bộ ngữ cảnh của nó. Bản cũ thiếu 5 field:

```text
FrameEnvelope
- company_id, store_id, camera_id, camera_type
- frame_idx, ts_utc, simulated_local_ts
- source_kind, source_uri, source_path
- source_frame_idx, source_time_s, loop_index   # vị trí trong file nguồn
- image_bgr
```

`source_frame_idx` / `source_time_s` / `loop_index` tách "frame thứ mấy của
pipeline" khỏi "frame thứ mấy của file nguồn" — cần thiết vì
`loop_to_duration` phát lại cùng một file nhiều vòng.

### 11.2. FrameMeta — không có trong bản cũ, nhưng là trục của split architecture

`FrameEnvelope` trừ đi pixel.

`image_bgr` là field duy nhất quá lớn để đi qua queue multiprocessing: pickle một
mảng 1080p tốn **35.6 ms**, so với **0.85 ms** cho một chỉ số slot (đo trong
`dev/tools/diag_ipc.py`). Nên decode tier giữ pixel trong `FrameRing` và chỉ gửi
`FrameMeta`; business tier ghép lại thành `FrameEnvelope` đầy đủ bằng
`to_envelope()` sau khi đọc frame ra khỏi ring.

Đây chính là lý do kiến trúc split khả thi — bỏ qua nó thì §6 không giải thích được.

### 11.3. CameraStreamSpec / CameraPipelineBindingSpec

Manifest sau khi parse. `camera_specs_from_manifests()` gộp nhiều store thành một
danh sách phẳng; `store_id` chỉ là một field trên mỗi spec, và đó là toàn bộ cơ chế
multi-store ở tầng compute.

### 11.4. Đường alert: không phải dataclass

Bản cũ vẽ `AlertCandidate` → `PersistedAlert` như hai contract. Thực tế pipeline gọi
thẳng **một chữ ký hàm**, giống nhau ở cả ba store backend (SQLite, Postgres, Cloud):

```python
create_alert(*, store_id, camera_id, run_id, pipeline_type, event_type,
             severity, opened_at, identity_person_id, identity_label,
             identity_score, reason_codes, metadata=None,
             status="open", alert_id=None) -> alert_id
```

Chính chữ ký này — chứ không phải một class — là thứ cho `AlertOutbox` thay sink
mà business tier không đổi dòng nào. Hình dạng đã lưu là **các cột bảng `alerts`**
(mục 12.2), và có hai cột bản cũ không nhắc: `reviewed_by`, `review_note` (thêm
cùng owner accounts, mục 12.4).

## 12. Data model cho DB chính

**Cập nhật 2026-08-26:** mục này viết lúc `pipeline_templates`/`camera_pipeline_bindings`/
`zone_assets`/`alert_identities` còn là dự định. Cả bốn **không được xây** — quyết định thật
sự đi theo hướng khác: pipeline binding và zone config ở lại dưới dạng **file** (manifest
JSON per store, `backend/config/stores/*.json`, đọc qua `camera_specs_from_manifests()`),
không phải bảng DB; identity của một alert nằm ngay trong 3 cột
`identity_person_id`/`identity_label`/`identity_score` của chính bảng `alerts`, không tách
bảng riêng. `pipeline_templates`/`camera_pipeline_bindings` từng được tạo rồi xoá lại
(commit `dde29d6`) sau khi xác nhận 0 chỗ nào đọc chúng. Danh sách bảng thật hiện tại nằm ở
`backend/sql/local_dev_schema.sql`; dưới đây sửa lại cho khớp.

### 12.1. Config tables (đọc từ file, không phải bảng DB)

Cấu hình camera/store/pipeline binding/zone **là file JSON**, không phải bảng — xem
`backend/config/stores/*.json` và `backend/src/infra/local_config.py`. Bảng DB chỉ giữ hai
thứ được seed lại từ chính các file đó lúc chạy (`seed_store_topology()`), để `alerts` có gì
để FK trỏ tới:

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
- `reviewed_by` FK → `owners` — thêm cùng owner accounts (12.4)
- `review_note`

#### `alert_media`
- `media_id` PK
- `alert_id` FK
- `kind`
- `path`
- `mime_type`
- `started_at`
- `ended_at`
- `metadata_json`

Không có bảng `alert_identities` riêng — `top1`/`top2`/`margin`/`decision_reason` chỉ sống
trong `metadata_json` của chính alert đó lúc ghi (xem `StorageIntrusionPipeline`), không có
bảng con nào tách ra để lưu lại.

### 12.3. Identity tables

Từng nằm ở một file SQLite riêng (`gallery_db_path`) tách khỏi mọi thứ ở trên — vì
`face_identity.py` được viết độc lập rồi mới ghép vào. Đã gộp về chung một file
(`local_dev_schema.sql`) để `persons.store_id` là FK thật thay vì chuỗi không ai kiểm tra.

#### `persons`
- `person_id` PK
- `display_name`
- `is_staff`
- `company_id`
- `store_id` FK → `stores`
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

### 12.4. Owner accounts — mới, chưa có trong bản v0.2 gốc

Cho web dashboard sau này: `owners` (đăng nhập — email/password_hash, cộng
phone/telegram_chat_id cho kênh cảnh báo) và `owner_stores` (join nhiều-nhiều owner↔store —
một chủ nhiều cửa hàng là ca chính hiện nay, giữ dạng nhiều-nhiều để một cửa hàng nhiều chủ
sau này không cần đổi schema).

## 13. Điểm quan trọng: cái gì không đưa vào DB chính

Không nên lưu vào DB chính:
- mỗi frame track result
- mỗi frame face observation
- full keypoint stream
- raw debug overlays

Nếu cần debug sau:
- lưu JSONL sidecar theo run/camera
- lưu artifact file trong `dev/outputs/` hoặc object storage
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

Rule:
- `top1 >= match_threshold`
- `top1 - top2 >= match_margin`
- Nếu không đạt => `unknown`

Mặc định trong code là `0.30` / `0.10` (`storage_intrusion.py:284`), khớp con số
"đã chốt" của bản gốc.

**Cập nhật 2026-08-30: manifest KHÔNG thống nhất, và không rõ có chủ đích hay không.**

| manifest | `match_threshold` |
|---|---|
| `local_dev_store.json`, `local_dev_store_16cams.json` | 0.30 |
| `store_1..4.json` | **0.25** |

Tức bốn manifest mang hình dạng production chạy ở ngưỡng **lỏng hơn** doc, còn
manifest bench thì chạy đúng ngưỡng doc. Ngưỡng thấp hơn = dễ gán tên cho một
khuôn mặt lạ hơn, nên đây là khác biệt về hành vi, không phải về hiệu năng. `margin`
thì thống nhất 0.10 ở mọi nơi. **Cần chốt lại một con số.**

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

**Cập nhật 2026-08-26: 17.1 và 17.2 không còn là "phase" tách biệt theo thời gian nữa —
cả hai chạy song song hôm nay, chọn bằng một cờ (`--storage-mode {local-db,cloud-db}`), cùng
một business tier, cùng một schema (chỉ khác cú pháp SQLite/Postgres).** Xem
`docs/backend/split_architecture_design.html` mục 9-11 để có số đo thật.

### 17.1. `local-db` — mặc định hôm nay

- `SQLite` (`backend/sql/local_dev_schema.sql`) — stores/cameras (seed lại từ manifest),
  alerts, identity metadata, cùng một file, không tách gallery riêng nữa.
- `Local filesystem` — clip, snapshot, face chip.

### 17.2. `cloud-db` — đã triển khai và verify bằng chạy thật, không còn là dự định

- `Neon Postgres` (`backend/sql/postgres_schema.sql`, `postgres_alert_store.py`) — cùng bảng,
  cùng FK với bản SQLite, chỉ đổi `BLOB→BYTEA` và `AUTOINCREMENT→GENERATED ALWAYS AS IDENTITY`.
- `Cloudflare R2` (`cloud_alert_sink.py`) — alert clip, snapshot; upload sau khi
  `LocalMediaStore` ghi file cục bộ xong (nhanh, không qua mạng), rồi ghi **R2 key** thay vì
  path local vào Postgres.
- Đã chạy thật 4 store / 16 camera trên Neon + R2 sống (không mock): 24 alert, 48/48
  `alert_media` khớp file trên R2, rule store-scoping đúng cả hai chiều — sau đó xoá sạch vì
  đó là bằng chứng luồng chạy đúng, không phải dữ liệu cần giữ.
- `Cloudflare Workers`/Hyperdrive — vẫn là dự định, chưa làm gì. Đây là phần frontend-facing
  API/auth, khác với phần ghi alert/media ở trên đã xong.

`AlertOutbox` (`backend/src/runtime/alert_outbox.py`) là điểm nối chọn giữa hai nhánh trên —
business tier gọi `create_alert()`/`add_alert_media()` y hệt nhau bất kể đang ở mode nào; toàn
bộ khác biệt nằm gọn trong `sink_factory`, không lộ ra ngoài.

Quyết định kiến trúc (vẫn đúng, chưa đổi):
- CV inference và stateful runtime không chạy trong Worker
- nó phải chạy trên backend server/app

## 18. Cấu hình và registry

Hệ thống phải config-driven.

### 18.1. Store manifest

**Cập nhật 2026-08-30: ví dụ dưới đây viết bằng YAML và dùng khoá `type:`.
Manifest thật là **JSON**, và khoá là **`camera_type`** — `local_config.py:88` đọc
`camera["camera_type"]`, nên `type:` sẽ `KeyError`. `access_schedule` cũng không có
khoá `timezone`; timezone khai ở cấp store. Giữ ví dụ ở dạng YAML cho dễ đọc, nhưng
đừng copy nguyên xi.**

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
    camera_type: counter        # JSON thật: "camera_type"
    stream:
      input_mode: video_file
      uri: data/videos/cctv_counter/top-side-view.mp4
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
    camera_type: storage
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
          access_schedule:          # không có khoá timezone ở đây
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

**Cập nhật 2026-08-30: chưa có registry. Thực tế là một chuỗi if/elif có nhánh mặc
định** (`business_tier.py:44-52`, `camera_runtime.py:64-76`):

```python
if pipeline_id == "counter_cash_theft_v1":            -> CounterCashTheftPipeline
if pipeline_id in {"storage_intrusion_v1",
                   "cold_storage_intrusion_v1"}:      -> StorageIntrusionPipeline
return TrackingBaselinePipeline(...)                  # mọi thứ còn lại
```

Hệ quả phải biết: **`whole_store_tracking_v1` không được khớp theo tên** — nó rơi
vào nhánh mặc định. Nghĩa là một `pipeline_id` viết sai (`storage_intrusion_v2`,
hay lỗi chính tả) **không báo lỗi**, nó lặng lẽ chạy tracking baseline: camera vẫn
chạy, vẫn ra frame, chỉ là không bao giờ có alert. Đây đúng là kiểu hỏng mà một
registry có kiểm tra tên sẽ chặn được.

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

**Cập nhật 2026-08-30: `/health` và `/metrics` **chưa tồn tại** — không có HTTP
endpoint nào. Cái đang có:**

- `pipeline_runs.metrics_json` — metric mỗi lần chạy, ghi lúc `finish_run()`
- stdout của entry point (`print(json.dumps(results))` cuối `run_split_pipeline.py`)
- `TimingCollector` — profile theo tầng, dùng cho bench
- `tier_status.py` — health-check giữa các tier process, không phải cho ngoài vào hỏi

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

## 22. Trạng thái triển khai

**Cập nhật 2026-08-30: mục này từng là roadmap Phase 0-5. Cả sáu phase đã xong,
nên nó không còn là kế hoạch — dưới đây là trạng thái, kèm chỗ kiểm chứng.**

| hạng mục | trạng thái | kiểm chứng |
|---|---|---|
| Contracts, schema, manifest | xong | `domain/models.py`, `sql/`, `config/stores/` |
| Runtime supervisor, camera runtime | xong | `run_local_backend.py` |
| Split architecture 3 tier | xong | `run_split_pipeline.py`, bench 16 cam |
| Storage + cold storage pipeline | xong | `storage_intrusion.py` |
| Counter cash theft | xong | `counter_cash_theft.py` |
| Whole store tracking | xong | `tracking_baseline.py` |
| Neon Postgres + R2 | xong, verify chạy thật | mục 17.2 |
| `RtspStreamSource` | code xong, **chưa từng đo** | `RTSP_MIGRATION.md` bước 3-5 |
| API frontend (REST/SSE/WS) | **chưa làm** | mục 23 |

`RtspStreamSource` đã implement (28/08) và `open_stream_source()` đã dispatch theo
`source_kind`, nên `camera_runtime` và `decode_tier` chuyển sang camera thật không
cần sửa code. Cái còn thiếu là **số đo**: mọi benchmark đến nay đều đọc file từ đĩa
với `pace_realtime=False`. Biên hiện tại 1.55×; nếu RTSP tốn thêm 20-30% thì tụt
còn ~1.2×, và con số đó quyết định một box gánh được mấy cửa hàng.

## 23. Open questions cần ADR riêng

Đã trả lời, giữ lại vì lý do chọn mới là phần đáng giá:

- **Queue nội bộ: asyncio, threads hay process workers?** → process, nối bằng
  shared-memory ring một chiều. Request/reply đã thử và bị bác bỏ bằng số đo:
  ping tới process chỉ nạp model rồi ngồi im tốn 9.35 ms, trên frame mà chi phí
  thật ~25 ms (`benchmarks/2026-08-24-rejected-request-reply-proxy.md`).
- **Cần GPU scheduling layer riêng không?** → không. Gom toàn bộ model vào tier
  inference, batch trong runner của nó là đủ; thêm một layer nữa chỉ thêm hop.

Còn mở:

- Framework API cho backend: FastAPI hay Starlette?
- Frontend đọc alert bằng SSE hay WebSocket?
- Debug artifacts có cần retention policy riêng không?

## 24. Kết luận

- local-first, production-shape, config-driven multi-store
- state cô lập theo camera
- **local-db và cloud-db chạy song song, chọn bằng một cờ** — không còn là hai
  giai đoạn nối tiếp như bản đầu mô tả
- perception stack nằm trong backend, `dev/` và `demo/` phụ thuộc một chiều vào nó
