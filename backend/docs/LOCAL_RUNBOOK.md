# Runbook Local Backend

## Mục tiêu

Chạy backend local trên MacBook/CPU theo đúng shape production:

- mỗi camera là một luồng riêng
- input là video file nhưng phát dần như streaming
- downsample theo `target_fps` của từng camera
- backend xử lý pipeline, ghi alert vào SQLite và ghi evidence media ra disk

Hiện tại runtime local đang ở chế độ:

- `synchronous_no_queue_no_drop`
- chưa có Kafka / gateway / RTSP thật
- chưa có batch multi-camera inference

## Lệnh decode-only

Lệnh này chỉ đo tầng source/decode/downsample, không chạy pipeline:

```bash
python3 -u backend/src/app/run_local_backend.py \
  --db-path backend/data/runtime_decode_only.sqlite \
  --gallery-db backend/data/local_dev.sqlite \
  --media-root backend/data/alerts_decode_only \
  --smoke-test \
  --decode-only
```

Ý nghĩa:

- dùng để tách riêng bottleneck source/decode khỏi inference/runtime logic
- đặc biệt hữu ích khi benchmark 16 camera hoặc nhiều hơn

## Lệnh smoke test nhanh

```bash
python3 -u backend/src/app/run_local_backend.py \
  --db-path backend/data/runtime_smoke.sqlite \
  --gallery-db backend/data/local_dev.sqlite \
  --media-root backend/data/alerts_smoke \
  --smoke-test
```

## Lệnh test trúng đoạn có sự kiện

### Storage

```bash
python3 -u backend/src/app/run_local_backend.py \
  --db-path backend/data/runtime_targeted.sqlite \
  --gallery-db backend/data/local_dev.sqlite \
  --media-root backend/data/alerts_targeted \
  --camera-ids storage_1 \
  --camera-starts storage_1=55 \
  --camera-durations storage_1=20
```

Kỳ vọng:

- `storage_1` phát hiện người vào kho
- face match ra `Staff B`
- do clock local-dev đang là `20:30`, ngoài khung giờ hợp lệ `08:00-09:00` và `21:00-22:00`
- backend tạo alert `staff_outside_schedule`

### Cold Storage

```bash
python3 -u backend/src/app/run_local_backend.py \
  --db-path backend/data/runtime_targeted.sqlite \
  --gallery-db backend/data/local_dev.sqlite \
  --media-root backend/data/alerts_targeted \
  --camera-ids cold_storage_1 \
  --camera-starts cold_storage_1=100 \
  --camera-durations cold_storage_1=20
```

Kỳ vọng:

- `cold_storage_1` phát hiện entry
- face không match staff gallery
- backend tạo alert `unknown_entry`

### Counter

```bash
python3 -u backend/src/app/run_local_backend.py \
  --db-path backend/data/runtime_targeted.sqlite \
  --gallery-db backend/data/local_dev.sqlite \
  --media-root backend/data/alerts_targeted \
  --camera-ids counter_1 \
  --camera-starts counter_1=52 \
  --camera-durations counter_1=25
```

Kỳ vọng hiện tại với video `cash_till_open_2.mp4`:

- FSM drawer -> hand in drawer -> hand to body vẫn chạy
- event bị `suppressed` vì có customer ở checkout gần thời điểm đó
- vì business rule hiện tại yêu cầu “không có khách ở vị trí thanh toán” mới alert, nên clip này chưa tạo alert counter thật

Nếu muốn ép test đường alert của counter:

- dùng staged clip đã biết có concealment thật
- hoặc tạm set `suppress_if_customer_recent=false` ở config camera counter chỉ cho mục đích bench/demo nội bộ

## Kiểm tra alert đã ghi

```bash
python3 - <<'PY'
import sqlite3
conn = sqlite3.connect('backend/data/runtime_targeted.sqlite')
cur = conn.cursor()
print('ALERTS')
for row in cur.execute(
    "select camera_id, event_type, identity_label, reason_codes_json from alerts order by opened_at"
):
    print(row)
print('\\nMEDIA')
for row in cur.execute(
    "select alert_id, kind, path from alert_media order by rowid"
):
    print(row)
conn.close()
PY
```

## Trạng thái hiện tại

- `storage_intrusion_v1`: chạy được, đã phát alert thật
- `cold_storage_intrusion_v1`: chạy được, đã phát alert thật
- `counter_cash_theft_v1`: FSM thật đã nối vào runtime; clip mẫu hiện tại cho ra suppression đúng rule
- `whole_store_tracking_v1`: đang ở baseline tracking, chưa có shoplifting
