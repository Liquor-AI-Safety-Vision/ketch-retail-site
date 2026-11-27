# Dịch vụ AI: cấu trúc và luồng chạy

Dịch vụ này không có HTTP API. Nó đọc camera, phát hiện vi phạm rồi ghi kết quả:
alert vào Neon, ảnh và clip vào R2. Dashboard đọc những kết quả đó qua Go API
trong `backend/`.

## Luồng một frame

```text
camera (MediaMTX rtsp)
  │
  ▼  decode tier      runtime/decode_tier.py    giải mã, lấy mẫu theo target_fps, letterbox
  │  (shared memory: runtime/frame_ring.py)
  ▼  inference tier   runtime/inference_server.py
  │     pose YOLO26n (box + 17 keypoint), ReID OSNet (vector cho tracker),
  │     till segmentation (chỉ camera quầy). Một tiến trình giữ model cho nhiều camera.
  ▼  business tier    runtime/business_tier.py  mỗi camera một thread
  │     1. CameraTracker (perception/camera_tracker.py): MỘT tracker cho mỗi camera
  │     2. pipeline.on_frame(tracks) cho từng pipeline gắn với camera
  │     3. face target -> face tier (bất đồng bộ) -> pipeline.on_face_answer()
  │
  ▼  AlertOutbox (runtime/alert_outbox.py)      luồng ghi riêng, có retry -> Neon + R2
```

Mọi camera dùng chung model. Mỗi camera có tracker riêng, và mọi pipeline gắn
với camera đó dùng chung tracker này: cùng một người mang cùng một track id
trong mọi pipeline, và chỉ bị track một lần.

## Thư mục

```text
src/
├── app/         run_split_pipeline.py (điểm vào), build_staff_db.py
├── common/      paths, logging, timing, stream URI
├── domain/      kiểu dữ liệu dùng chung (CameraStreamSpec, FrameEnvelope, ...)
├── perception/  model và thuật toán, không biết gì về nghiệp vụ
│     person_tracker.py      DeepSORT
│     camera_tracker.py      tracker theo camera + lọc box nhỏ
│     zone_monitor.py        vào/ra một vùng (kho, cửa vào)
│     zones.py               phía nhân viên / khách ở quầy
│     till_state.py          két tiền mở/đóng, có debounce
│     cash_concealment_fsm.py  máy trạng thái giấu tiền ở quầy
│     customer_cash_fsm.py   nhận tiền khách không bỏ vào két (chưa gắn vào pipeline)
│     face_identity.py, face_align.py   nhận diện mặt, gallery
│     stream_track.py        Detector/Embedder ONNX (kèm CLI demo cũ)
├── pipelines/   nghiệp vụ, mỗi pipeline_id một class
│     base.py                interface: TrackedFrame -> PipelineOutput
│     registry.py            pipeline_id -> class; id lạ bị từ chối
│     alert_emitter.py       ghi alert + snapshot + clip (dùng chung)
│     counter_cash_theft.py, storage_intrusion.py, entrance_face_capture.py,
│     tracking_baseline.py
├── runtime/     các tier, ring buffer, outbox
└── infra/       manifest, SQLite / Neon / R2, clip từ bản ghi MediaMTX
```

## Pipeline hiện có

| pipeline_id | Camera | Alert |
|---|---|---|
| `counter_cash_theft_v1` | quầy | `suspected_cash_concealment` (high) |
| `storage_intrusion_v1`, `cold_storage_intrusion_v1` | kho, kho lạnh | `unknown_entry`, `staff_outside_schedule` |
| `entrance_face_capture_v1` | cửa vào | `watchlist_match` |
| `whole_store_tracking_v1` | toàn cửa hàng | chưa có luật, chỉ tracking |

Mỗi camera chọn pipeline trong manifest (`config/stores/*.json`, khối
`pipelines`). Một camera có thể gắn nhiều pipeline.

## Thêm một pipeline (ví dụ phát hiện tụ tập)

1. Tạo `pipelines/crowd_gathering.py` với một class kế thừa `Pipeline`
   (`pipelines/base.py`), viết `on_frame(tracked)`. `tracked.live` là danh sách
   người đang được track (box, keypoint, track id), nên không phải tự làm
   tracking. Khi cần báo thì gọi `AlertEmitter.emit(...)`. Nếu giữ clip đang
   mở thì `finalize()` gọi `flush()`.
2. Thêm id `crowd_gathering_v1` vào `pipelines/registry.py` (hằng số,
   `KNOWN_PIPELINE_IDS`, `build_pipeline`).
3. Khai báo tham số của nó trong `infra/manifest_schema.py` (`PIPELINE_PARAMS`).
   Manifest dùng tham số lạ sẽ bị từ chối.
4. Nếu có vùng cần vẽ trên dashboard thì thêm vào `infra/camera_metadata.py`.
5. Gắn vào camera trong manifest:
   `{"pipeline_id": "crowd_gathering_v1", "enabled": true, "params": {...}}`.
6. Thêm `event_type` mới vào bộ từ vựng của database
   (`backend/docs/database-design.md`) và dashboard.

Không phải sửa business tier.

## Nhiều GPU trên một máy

`--gpus auto` (mặc định) đếm GPU bằng `nvidia-smi`. Tiến trình inference thứ k
chạy trên GPU `k % số GPU`, face tier chạy trên GPU cuối. Cần
`--inferers` ít nhất bằng số GPU, nếu không sẽ có GPU bỏ trống. Máy một GPU
chạy giống hệt như trước.

## Kiểm tra một thay đổi

- `python -m pytest ai/tests`
- `ai/tools/behaviour_snapshot.py`: chạy pipeline thật trên footage của store,
  lưu toàn bộ alert, và so trước/sau (`--compare`). Refactor đúng phải cho ra
  alert giống hệt.
