# Checklist debug và tối ưu runtime

Ngày cập nhật: `2026-08-25`

Tài liệu này là playbook cho phase debug/tối ưu backend multi-camera. Mục tiêu là tối ưu có kiểm soát: mỗi lần chỉ thay đổi một biến, xác nhận bằng số đo, không làm hỏng correctness và không khóa đường tối ưu của tầng sau.

## 1. Mục tiêu phase này

- [ ] 16 camera chạy đúng end-to-end, không deadlock, không crash, không leak shared memory.
- [ ] Mỗi camera giữ đúng `target_fps`: counter/whole_store `5 FPS`, storage/cold_storage `3 FPS`.
- [ ] Tổng target hiện tại của manifest 16 cam là `64 FPS aggregate`.
- [ ] Benchmark phase này cần đạt tối thiểu `128 FPS aggregate` để dư `200% target`.
- [ ] Benchmark stretch goal là `192 FPS aggregate` để đạt `300% target`.
- [ ] Tích hợp thêm module mới sau này không cần đổi lại topology runtime lớn.

## 2. Nguyên tắc bắt buộc

- [ ] Không tối ưu khi correctness gate chưa pass.
- [ ] Không thay đổi hơn một biến trong cùng một lần benchmark.
- [ ] Không giữ một tối ưu chỉ vì một tầng nhanh hơn nếu tổng pipeline chậm hơn.
- [ ] Không chấp nhận tối ưu làm mất frame order của từng camera.
- [ ] Không chấp nhận tối ưu làm sai alert/event hoặc làm drift tracker bất thường.
- [ ] Không truyền frame `np.ndarray` lớn qua `multiprocessing.Queue` bằng pickle.
- [ ] Không hard-code theo video/model hiện tại nếu config có thể thay đổi sau này.
- [ ] Không tối ưu bằng cách tắt bỏ logic production cần có, trừ khi đó là benchmark layer riêng có ghi rõ.
- [ ] Mỗi kết quả benchmark phải lưu đủ config, command, commit/diff state, metrics JSON và nhận xét.

## 3. Điều kiện freeze baseline

- [ ] Chốt manifest dùng cho benchmark 16 cam.
- [ ] Chốt danh sách camera và `target_fps` từng camera.
- [ ] Chốt video window từng camera: `start_s`, `target_duration_s`, `strategy`.
- [ ] Chốt model path: pose, reid, till, face, arcface.
- [ ] Chốt provider: CPU/CUDA/CoreML/TensorRT nếu có.
- [ ] Chốt topology ban đầu: số decoder process, inference process, inference lanes, business process, slots per camera.
- [ ] Chốt media mode: bật/tắt snapshot, bật/tắt clip, media root riêng cho benchmark.
- [ ] Chốt DB path riêng cho benchmark để không lẫn với run khác.
- [ ] Lưu command baseline vào file benchmark note.
- [ ] Lưu output JSON baseline vào `docs/benchmarks/` hoặc thư mục benchmark riêng.

## 4. Correctness gate

Chưa pass phần này thì không tối ưu performance.

- [ ] `python3 -m compileall` pass với `PYTHONPYCACHEPREFIX` trong `/private/tmp`.
- [ ] Test frame ring pass: producer/consumer đọc đúng slot, blob, free list.
- [ ] Decode-only chạy đủ frame kỳ vọng cho từng camera.
- [ ] Split pipeline không treo khi child process load model chậm.
- [ ] Split pipeline không treo khi decoder lỗi mở video.
- [ ] Split pipeline không treo khi inference lane exception một frame.
- [ ] Split pipeline không treo khi face model/gallery lỗi.
- [ ] Mỗi `FaceRequest` phải có một kết quả hoặc một failure answer tương ứng.
- [ ] Slot shared memory được release đúng một lần sau khi business tier dùng xong frame.
- [ ] Khi exception trong business handler, slot vẫn được release hoặc process fail rõ ràng.
- [ ] Parent process cleanup ring/shared memory trong `finally`.
- [ ] Local mode và split mode cho cùng video window có frame count giống nhau.
- [ ] Local mode và split mode không lệch bất thường về tracks created/live track total.
- [ ] Counter FSM không dùng global config có thể bị camera khác ghi đè.
- [ ] `stream_track` global model/conf/provider không bị race giữa pipeline/camera.
- [ ] Storage schedule đúng với khung giờ bình thường.
- [ ] Storage schedule có test cho khung giờ qua nửa đêm, ví dụ `22:00-06:00`.
- [ ] Media write failure được detect, không trả path thành công giả.
- [ ] SQLite foreign keys được bật nếu schema có FK.

## 5. Instrumentation gate

Chưa có metric thì chưa tối ưu tầng đó.

- [ ] Mỗi stage có timing `count`, `total_s`, `avg_ms`, `p50_ms`, `p95_ms` nếu có thể.
- [ ] Source có metric: `open`, `grab`, `retrieve`, `reopen`, `eof`, `skipped_source_frames`.
- [ ] Decode tier có metric: `free_q_wait`, `copy_frame`, `letterbox`, `cvtColor_transpose_normalize`, `decode_q_put`.
- [ ] Inference tier có metric: `decode_q_get_wait`, `pose_infer`, `pose_decode`, `reid_crop_pack`, `reid_infer`, `till_infer`, `business_q_put`.
- [ ] Business tier có metric: `business_q_get_wait`, `tracker_prepare`, `tracker_update`, `zone_update`, `fsm_logic`, `face_request_put`, `clip_buffer_push`.
- [ ] Face tier có metric: `face_q_get_wait`, `face_detect`, `arcface`, `gallery_match`, `answer_put`, `failure_count`.
- [ ] Persistence có metric: `sqlite_insert_alert`, `sqlite_insert_media`, `snapshot_write`, `clip_write`.
- [ ] Queue nào cũng có metric depth/lag hoặc approximation: decode queue, business queue, face queue, free slot queue.
- [ ] Mỗi process report pid, camera shard, provider, model path, lane index.
- [ ] Kết quả benchmark có `frames_processed`, `alerts_emitted`, `events_emitted`, `serving_s`, `fps`.
- [ ] Metrics tách riêng `model_load_s` và `serving_s`.
- [ ] Metrics không chỉ in ra stderr; phải có JSON machine-readable.

## 6. Benchmark ladder

Chạy theo thứ tự này. Không nhảy lên full pipeline khi tầng trước chưa pass.

### B0 - Decode-only

- [ ] Tắt tất cả pipeline/model.
- [ ] Đo source/decode/downsample/free slot.
- [ ] Xác nhận frame count từng camera đúng.
- [ ] Xác nhận decode aggregate >= target `64 FPS`.
- [ ] Nếu B0 đã chậm, không tối ưu model trước.

### B1 - Decode + pose

- [ ] Bật pose only.
- [ ] Tắt ReID/till/face/business nặng.
- [ ] Đo pose latency, batch size, provider, queue wait.
- [ ] Xác nhận GPU có việc đều, CPU preprocess không làm GPU đợi quá lâu.
- [ ] Nếu pose nhanh riêng lẻ nhưng queue wait cao, tối ưu topology/queue trước model.

### B2 - Decode + pose + ReID

- [ ] Bật ReID với output embedding thật.
- [ ] Đo số box/frame, crop/pack/infer/post.
- [ ] Xác nhận tracker input không mất order.
- [ ] Xem ReID có nên gom theo frame hay theo mini-batch patch.
- [ ] Kiểm tra ReID batching không làm tracker nhận embedding quá muộn.

### B3 - Perception + business, tắt media write

- [ ] Bật tracker/zone/FSM/till state.
- [ ] Tắt snapshot/clip write nếu cần tách I/O.
- [ ] Đo business CPU time từng camera type.
- [ ] Xác nhận alert/event count hợp lý.
- [ ] Nếu tracker/FSM nghẽn, tối ưu business trước khi đụng tới DB/media.

### B4 - Full pipeline

- [ ] Bật alert persistence.
- [ ] Bật snapshot.
- [ ] Bật clip nếu production cần.
- [ ] Đo DB/media overhead.
- [ ] Xác nhận media file tạo ra đọc được.
- [ ] Xác nhận alert_media liên kết đúng alert.

### B5 - Stress run

- [ ] Chạy 16 cam trong thời lượng dài hơn smoke test, tối thiểu 3-5 phút video time.
- [ ] Xác nhận không deadlock khi shutdown.
- [ ] Xác nhận memory không tăng liên tục.
- [ ] Xác nhận queue depth không tăng liên tục.
- [ ] Xác nhận FPS ổn định sau model warmup.

## 7. Checklist tối ưu theo từng tầng

### 7.1. Source và decode

- [ ] Đo `VideoCapture.open` time và failure rate.
- [ ] Đo `grab`/`retrieve` riêng, không gom chung.
- [ ] Đo `source_step` và số frame bị skip do downsample.
- [ ] Đo `free_q.get()` wait để biết downstream có gây backpressure không.
- [ ] Thử shard camera vào decoder process khác nhau.
- [ ] Thử số decoder process khác nhau: `1`, `2`, `4`, `8`.
- [ ] Xác nhận tăng decoder process không làm codec resource fail.
- [ ] Nếu RTSP thật, tách network jitter metric khỏi decode CPU metric.
- [ ] Cân nhắc GStreamer nếu OpenCV/FFmpeg open/decode là bottleneck.
- [ ] Không drop frame tùy tiện với camera có FSM/till debounce cần chuỗi frame liên tiếp.

### 7.2. Preprocess

- [ ] Đo letterbox riêng.
- [ ] Đo BGR->RGB/transpose/normalize riêng.
- [ ] Kiểm tra copy thừa trong path decode -> ring -> blob.
- [ ] Kiểm tra dtype blob khớp model input dtype.
- [ ] Thử FP16 blob nếu model FP16 và provider hỗ trợ.
- [ ] Không chuyển preprocess lên GPU nếu làm GPU tranh tải với pose/ReID mà tổng FPS giảm.
- [ ] Nếu model input khác `640`, blob shape phải đi theo model config, không hard-code.

### 7.3. Queue và IPC

- [ ] Đo `put`/`get` wait từng queue.
- [ ] Đo queue depth trước và sau mỗi stage.
- [ ] Kiểm tra decode queue có bị một camera làm đầy ảnh hưởng camera khác không.
- [ ] Kiểm tra business queue có đúng one queue per camera không.
- [ ] Kiểm tra face queue maxsize có đủ với burst entry không.
- [ ] Giảm số message/frame nếu có thể gom metadata.
- [ ] Không đưa full frame qua multiprocessing queue.
- [ ] Nếu queue wait cao ở cả input và output của inference, xem lại lane/process topology trước.

### 7.4. Pose inference

- [ ] Đo `pose_infer_s`, `pose_decode_s`, `queue_wait_s`, `batch_wait_s`.
- [ ] Ghi lại provider thật sự đang dùng.
- [ ] Thử số inference process: `1`, `2`, `4`.
- [ ] Thử số lane/thread mỗi process: `1`, `2`, `4`.
- [ ] Thử batch size/pad batch: `1`, `2`, `4`, `8`.
- [ ] Nếu batch size lớn làm CUDA re-plan hoặc latency cao, ưu tiên fixed shape/pad batch.
- [ ] Nếu nhiều CUDA context làm latency tăng, giảm inference process.
- [ ] Nếu GPU idle mà CPU preprocess cao, quay lại tầng preprocess/decode.
- [ ] Kiểm tra output pose không drift so với baseline sau mỗi thay đổi provider/model.

### 7.5. ReID

- [ ] Đo box count trung bình và p95 per frame.
- [ ] Đo crop/resize/pack normalize.
- [ ] Đo infer batch size và latency.
- [ ] Kiểm tra clone embedder không share mutable scratch buffer.
- [ ] Thử batching theo frame hoặc mini-batch patch.
- [ ] Kiểm tra batching ReID không làm tracker update trễ.
- [ ] Nếu ReID chỉ cần cho một số camera, không chạy cho pipeline không cần.
- [ ] Nếu object count cao, cần có gate/filter trước ReID nếu không làm sai tracker.

### 7.6. Till

- [ ] Đo till sampling FPS thực tế.
- [ ] Đo till infer và mask decode.
- [ ] Kiểm tra `till_confirm_frames` được rescale đúng khi `till_fps` giảm.
- [ ] Thử `till_fps`: every frame, `3Hz`, `2Hz`, `1Hz`.
- [ ] Xác nhận giảm till FPS không làm miss drawer open/close event.
- [ ] Không feed repeated till reading vào confirm streak trên skipped frames.
- [ ] Kiểm tra till batch size không làm provider re-plan liên tục.

### 7.7. Face identity

- [ ] Đo số FaceRequest/frame và FaceRequest/event.
- [ ] Đo face queue depth và latency.
- [ ] Mỗi FaceRequest có FaceAnswer thành công hoặc failure answer.
- [ ] Nếu gallery empty theo scope, log thành startup warning rõ.
- [ ] Kiểm tra crop patch là copy, không phải view vào ring slot.
- [ ] Kiểm tra face answer đến muộn không làm finalize sai.
- [ ] Kiểm tra business shutdown không chờ vô hạn khi face process fail.
- [ ] Thử batch face detect/arcface nếu face traffic cao.

### 7.8. Tracker, zone và FSM

- [ ] Đo tracker prepare detections.
- [ ] Đo tracker association/update.
- [ ] Đo zone monitor/voter.
- [ ] Đo FSM logic.
- [ ] Đo live tracks p50/p95 và tracks created.
- [ ] Loại global mutable FSM config trước khi benchmark nhiều camera khác FPS.
- [ ] Kiểm tra business state được isolate per camera.
- [ ] Nếu tracker CPU-bound, cân nhắc tối ưu data structure, vectorize, C/C++/Cython, hoặc process pool có shared input nhẹ.
- [ ] Không batch chung stateful business logic giữa camera.

### 7.9. Alert, SQLite và media

- [ ] Đo insert alert.
- [ ] Đo insert media.
- [x] Đo snapshot write và check return. `cv2.imwrite` trả `False` chứ không raise — đã check.
- [x] Đo clip writer open/write/release và check `isOpened`. VideoWriter mở hỏng vẫn nhận `write()` và `release()` sạch, chỉ là không sinh ra file — đã check.
- [x] Bật `PRAGMA foreign_keys=ON`. **Đã bật thật, xong cả hai việc từng chặn nó** (commit
  `2cab38e`): `alerts` giờ khai FK tới `stores`/`cameras`, và `seed_store_topology()` seed
  các hàng cha từ `CameraStreamSpec` của lượt chạy, gọi trước khi bất kỳ tier nào ghi. Pragma
  bật vô điều kiện trong `sqlite_alert_store.py`, không còn là nửa vời.
- [ ] Kiểm tra WAL/busy timeout khi nhiều camera cùng ghi.
- [x] Thử async writer nếu DB/media là bottleneck. **Đã làm** — `AlertOutbox`
  (`backend/src/runtime/alert_outbox.py`): mỗi business process có 1 writer thread riêng rút
  hàng đợi, camera-thread không còn block trên I/O ghi. Verify bằng benchmark thật (123.42 vs
  122.22 FPS trước khi tách — không suy giảm) và dùng làm điểm nối cho cloud-db (NeonDB + R2,
  đã verify chạy thật, xem `outputs/reports/split_architecture_design.html` mục 9 và 11).
- [ ] Không bật clip trong benchmark layer nếu đang tách I/O, nhưng full benchmark phải bật nếu production cần.
- [ ] Có retention policy cho media benchmark để không đầy disk.

## 8. One-change protocol

Dùng protocol này cho mỗi tối ưu.

- [ ] Viết hypothesis một câu.
- [ ] Ghi rõ tầng nào đang tối ưu.
- [ ] Ghi rõ metric kỳ vọng sẽ cải thiện.
- [ ] Chạy baseline trước khi sửa.
- [ ] Sửa đúng một biến.
- [ ] Chạy lại tối thiểu 3 lần với cùng config.
- [ ] So sánh median và p95, không chỉ nhìn một lần nhanh nhất.
- [ ] Kiểm tra correctness metrics: frame count, alert count, event count, track metrics.
- [ ] Kiểm tra queue wait tầng sau có tăng sau tối ưu hay không.
- [ ] Nếu tổng FPS tăng và correctness pass, giữ thay đổi.
- [ ] Nếu chỉ một stage nhanh hơn nhưng tổng FPS giảm, revert.
- [ ] Nếu kết quả không ổn định, thêm stress run trước khi quyết định.
- [ ] Ghi kết luận vào benchmark note: `keep`, `revert`, hoặc `needs more data`.

Template ghi hypothesis:

```text
Hypothesis:
- Thay đổi:
- Tầng ảnh hưởng:
- Metric kỳ vọng:
- Risk/có thể conflict với tầng sau:
- Command baseline:
- Command candidate:
- Kết quả:
- Quyết định:
```

## 9. Luật tránh conflict giữa các tối ưu

- [ ] Tối ưu decode không được phá frame order downstream.
- [ ] Tối ưu sampling không được làm FSM/till debounce mất chuỗi frame cần thiết.
- [ ] Tối ưu batch size không được làm latency tăng quá mức business tier bị đói frame.
- [ ] Tối ưu số inference process không được tạo quá nhiều CUDA context làm GPU chậm hơn.
- [ ] Tối ưu preprocess trên GPU không được tranh tải với pose/ReID nếu GPU đã saturated.
- [ ] Tối ưu ReID batching không được làm tracker update nhận embedding quá muộn.
- [ ] Tối ưu face async không được làm shutdown phụ thuộc vào answer không bao giờ tới.
- [ ] Tối ưu DB/media bằng async writer không được làm mất alert khi process crash.
- [ ] Tối ưu hard-code cho local video không được khóa camera RTSP/model shape sau này.
- [ ] Tối ưu tích hợp module mới không được đưa dependency POC runner trực tiếp vào runtime core.

## 10. Definition of done cho một benchmark

- [ ] Command được lưu.
- [ ] Config/manifest được lưu hoặc link rõ.
- [ ] Code state được ghi lại bằng commit hash hoặc `git diff --stat`.
- [ ] Metrics JSON được lưu.
- [ ] `frames_processed` đúng kỳ vọng.
- [ ] `serving_s` và aggregate FPS được tính.
- [ ] Stage timing có p50/p95 hoặc avg rõ ràng.
- [ ] Queue wait/depth có số liệu.
- [ ] Alert/event/track metrics không drift bất thường.
- [ ] Kết luận rõ: pass/fail, bottleneck hiện tại, bước tiếp theo.

## 11. Definition of done cho một tối ưu

- [ ] Correctness gate vẫn pass.
- [ ] Benchmark ladder liên quan vẫn pass.
- [ ] Tổng FPS tăng hoặc latency p95 giảm theo mức có ý nghĩa.
- [ ] Không tăng queue wait của tầng sau quá mức.
- [ ] Không tăng memory/disk usage bất thường.
- [ ] Không thêm hard-code làm mất config-driven behavior.
- [ ] Không làm module sau khó tích hợp hơn.
- [ ] Đã ghi tài liệu benchmark trước/sau.
- [ ] Đã thêm hoặc cập nhật test nếu thay đổi logic.

## 12. Thứ tự ưu tiên để làm ngay

- [ ] Sửa face async để không thể deadlock.
- [ ] Thêm timeout/health-check/finally cleanup cho split orchestrator.
- [ ] Thêm metrics JSON chuẩn cho split runtime.
- [ ] Chạy B0 decode-only 16 cam.
- [ ] Chạy B1 decode + pose 16 cam.
- [ ] Chạy B2 decode + pose + ReID 16 cam.
- [ ] Chạy B3 perception + business, tắt media.
- [ ] Chạy B4 full pipeline.
- [ ] Xác định bottleneck lớn nhất bằng số liệu.
- [ ] Mỗi lần tối ưu chỉ xử lý bottleneck lớn nhất hiện tại.

## 13. Bảng quyết định nhanh

| Dấu hiệu | Kết luận tạm thời | Bước tiếp theo |
|---|---|---|
| Decode-only đã chậm | Nghẽn source/decode | Tối ưu VideoCapture/GStreamer/sharding/sampling |
| Decode nhanh, pose queue wait cao | Nghẽn inference topology | Thử lane/process/batch/fixed shape |
| Pose infer nhanh, GPU idle, preprocess cao | Nghẽn CPU preprocess | Giảm copy, tối ưu cvtColor/normalize, cân nhắc GPU preprocess |
| ReID tăng mạnh khi đông người | Nghẽn object-level compute | Batch/filter/optimize crop pack |
| Business CPU cao | Nghẽn tracker/zone/FSM | Profile association, isolate globals, tối ưu data structure |
| Full pipeline chậm hơn B3 nhiều | Nghẽn DB/media | Async writer, batch DB, clip policy |
| Queue depth tăng liên tục | Downstream không kịp | Tìm consumer chậm nhất, không tăng producer |
| FPS cao nhưng alert drift | Tối ưu sai correctness | Revert và thêm regression test |

## 14. Format đặt tên benchmark

Để dễ so sánh, dùng format:

```text
YYYY-MM-DD_<ladder>_<topology>_<short-change>.json
YYYY-MM-DD_<ladder>_<topology>_<short-change>.md
```

Ví dụ:

```text
2026-08-25_B1_16cam_2infer2lane_pose-batch1.json
2026-08-25_B1_16cam_2infer2lane_pose-batch1.md
```

## 15. Ghi nhớ quan trọng

Kiến trúc đúng không phải là kiến trúc có nhiều queue/process nhất. Kiến trúc đúng là kiến trúc mỗi tầng có ownership rõ, đo được bottleneck, và khi tăng camera/module thì chỉ cần scale đúng tầng đang nghẽn.
