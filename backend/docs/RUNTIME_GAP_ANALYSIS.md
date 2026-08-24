# Phân tích kiến trúc runtime và bottleneck hiện tại

Ngày cập nhật: `Monday, August 24, 2026`

Tài liệu này có 3 mục tiêu:

1. Mô tả rõ `uae-main` đang đi theo kiến trúc nào.
2. So sánh với runtime hiện tại trong repo này.
3. Chỉ ra chính xác các điểm nghẽn, các phép đo cần có, và thứ tự tối ưu nên làm.

## 1. UAE đang làm theo hướng nào

Về bản chất, `uae-main` đi theo hướng:

- mỗi camera có config và state riêng
- các module model như detection / reid là shared service
- các service giao tiếp qua queue
- worker lấy item từ queue, gom batch opportunistic rồi mới infer
- ưu tiên `multithreading + shared queue` hơn `multiprocessing`

Luồng tổng quát của UAE có thể hình dung như sau:

```text
camera stream
  -> reader / sampler theo từng stream
  -> queue chung cho detection
  -> detection workers
  -> gom batch opportunistic
  -> detection infer
  -> trả kết quả về stream tương ứng
  -> tracker / FSM / rule theo từng stream
  -> nếu cần crop patch
  -> queue chung cho reid
  -> reid workers
  -> gom batch opportunistic
  -> reid infer
  -> trả embedding về stream tương ứng
  -> alert / log / evidence writer
```

Những ý này được viết khá rõ trong:

- `uae-main/docs/NOTES.md`
- `uae-main/README.md`

## 2. Runtime hiện tại của repo này

Hiện tại repo này đang có 2 mode chính:

### 2.1. `process`

```text
mỗi camera = 1 process riêng
  -> VideoCapture riêng
  -> pose detector riêng
  -> reid riêng
  -> till riêng
  -> face det / arcface riêng
  -> tracker / FSM / alert riêng
```

Ưu điểm:

- dễ hiểu
- ít phụ thuộc lẫn nhau
- baseline tốt ở tải thấp, ví dụ 4 camera

Nhược điểm:

- nhân toàn bộ resource theo số camera
- không tận dụng batching xuyên camera
- dễ chạm giới hạn decode / codec / memory / session khi scale số luồng

### 2.2. `thread` + shared inference

```text
mỗi camera vẫn có source riêng
  -> camera runtime riêng
  -> gửi request vào shared pose queue
  -> shared pose workers batch infer
  -> trả kết quả về từng camera
  -> nếu cần thì shared till / shared face_det / shared arcface
  -> tracker / FSM / alert vẫn là per-camera
```

Ưu điểm:

- gần với tư duy của UAE hơn
- có khả năng gom batch xuyên camera
- khi tăng số luồng, batching bắt đầu có tác dụng thật

Nhược điểm:

- source/decode vẫn nằm trong từng camera runtime
- chưa có tầng decode worker riêng
- queue/orchestration vẫn còn khá thô
- một số metric source trước đây chưa đủ chi tiết để bóc nghẽn

## 3. Kiến trúc đích nên hướng tới

Kiến trúc đích nên gần hơn với hình dưới:

```text
decode workers
  -> frame queue / stream mailbox
  -> model service: pose
       -> opportunistic batching
       -> worker pool / session pool
  -> per-stream tracker / FSM
  -> nếu cần crop patch
  -> model service: reid
       -> opportunistic batching
  -> model service: till
       -> opportunistic batching
  -> model service: face_det / arcface
       -> opportunistic batching
  -> alert writer / clip writer / DB writer
```

Điểm quan trọng:

- `state nghiệp vụ` vẫn theo từng camera
- `model inference` phải là shared service
- `decode/source` cũng cần được nhìn như một tầng riêng, không chỉ là chi tiết bên trong camera runtime

## 4. Kết quả benchmark đã biết

## 4.1. 4 camera

`process` mode trên L40S:

- đã rerun lại thật
- aggregate khoảng `28.49 FPS`
- đủ xa so với target hiện tại `16 FPS aggregate`

Ý nghĩa:

- phần cứng L40S đủ sức cho bài toán hiện tại ở tải 4 camera
- baseline tốt không đồng nghĩa kiến trúc này sẽ scale tốt lên nhiều camera hơn

## 4.2. 16 camera giả lập

Manifest 16 camera được tạo bằng cách nhân 4 camera gốc lên 4 lần.

`process` mode:

- fail ở pha mở / khởi tạo decode
- log thực tế cho thấy lỗi kiểu:
  - `Failed initializing scaling graph (Resource temporarily unavailable)`
  - `Could not open codec h264, error: -11`

Điểm quan trọng:

- file video vẫn tồn tại
- failure thực tế là `VideoCapture` / FFmpeg open fail do resource
- trước đây code ném ra `FileNotFoundError`, dễ gây hiểu nhầm

`thread + shared batching` mode:

- chạy xong
- tổng `3840 frames`
- wall time `306.10s`
- aggregate throughput `12.54 FPS`

Batch size thực tế ở 16 camera:

- `cold_storage`: `pose_batch_avg ~ 3.56`
- `storage`: `pose_batch_avg ~ 3.62`
- `whole_store`: `pose_batch_avg ~ 2.88`
- `counter`: `pose_batch_avg ~ 2.55`
- `pose_batch_max`: lên tới `8`
- `counter till_batch_avg`: `~2.18`

Ý nghĩa:

- ở 16 camera, batching đã là batching thật
- nghẽn không còn chỉ là infer
- decode/source/orchestration đang giữ vai trò lớn

## 5. Những bottleneck đã xác nhận

### 5.1. Bước source/decode

Đây là bottleneck đầu tiên cần tách riêng.

Biểu hiện:

- `process` 16 camera fail ngay ở tầng open/decode
- `source_grab` và `source_retrieve` từng dao động lớn trong benchmark 4 camera
- khi scale camera, tầng decode có thể chạm giới hạn trước cả infer

Việc đã làm:

- runtime hiện đã có `source_metrics`
- phân biệt rõ open attempts / failures / reopen / grab failures / retrieve failures
- lỗi mở source khi file vẫn tồn tại sẽ không còn bị báo như `FileNotFoundError`

### 5.2. Queue/orchestration

Ở 4 camera, shared batching ban đầu thua nặng vì:

- queue wait lớn
- batch size thực tế gần 1

Sau khi tăng pose workers, queue wait giảm mạnh nhưng vẫn chưa đủ thắng baseline 4 camera.

Điều đó cho thấy:

- vấn đề không chỉ là “có batching hay không”
- còn là worker topology, session topology, và cách dồn item vào queue

### 5.3. CPU-side preprocess / postprocess

Các khâu này tuy nhỏ hơn infer, nhưng khi nhiều camera cùng chạy sẽ cộng dồn đáng kể:

- letterbox
- color convert / transpose / normalize
- mask decode của till
- crop / pack norm / concat cho reid

Theo `uae-main/docs/NOTES.md`, đây cũng là chỗ họ tự ghi nhận có thể chuyển dần thêm lên GPU hoặc tối ưu C/C++ path về sau.

## 6. Những chỗ có thể học trực tiếp từ UAE

### 6.1. Không lạm dụng multiprocessing cho ảnh `np.ndarray`

Đây là điểm UAE ghi rất rõ:

- truyền frame qua `multiprocessing.Queue` đắt
- pickle/unpickle ảnh là bottleneck lớn

Suy ra với repo này:

- không nên xem `mỗi camera một process` là kiến trúc đích
- process mode chỉ nên là baseline/reference hoặc fallback

### 6.2. Giảm số lượng queue item

UAE cũng chỉ ra rằng nếu 1 frame có nhiều box mà cứ `put/get` từng patch riêng lẻ thì context switch và lock cost tăng vô ích.

Suy ra với repo này:

- reid / face / patch-based stage về sau nên ưu tiên submit theo mini-batch logic
- không nên để mỗi patch là một queue item riêng nếu không cần

### 6.3. Tách model stage thành service thực sự

Hiện shared inference của repo này đã đi được nửa bước:

- pose shared
- till shared
- face_det shared
- arcface shared

Nhưng vẫn còn thiếu:

- decode workers tách biệt
- mailbox hoặc queue rõ ràng cho từng stage
- session pool / worker pool được thiết kế nhất quán hơn

## 7. Những việc nên làm ngay để benchmark chuẩn hơn

### 7.1. Decode-only benchmark

Đã thêm `--decode-only` vào entrypoint runtime.

Mục tiêu:

- đo riêng source/decode/downsample
- biết 4 camera, 16 camera đang nghẽn bao nhiêu ở tầng này trước khi chạy model

### 7.2. Báo lỗi source cho đúng bản chất

Đã sửa để:

- file không tồn tại -> `FileNotFoundError`
- file có tồn tại nhưng `VideoCapture` mở không được -> `StreamOpenError`

Điều này giúp tránh kết luận sai khi thực chất hệ thống đang chạm trần resource decode.

### 7.3. Ghi source metrics vào kết quả runtime

Hiện mỗi camera sẽ có thêm:

- `open_attempts`
- `open_successes`
- `open_failures`
- `open_avg_ms`
- `reopen_count`
- `grab_failures`
- `retrieve_failures`
- `eof_count`
- `last_open_error`

## 8. Thứ tự tối ưu đề xuất

### Ưu tiên 1

Chạy `decode-only` ở 4 camera và 16 camera, cả `process` và `thread`.

Mục tiêu:

- tách riêng bottleneck source/decode khỏi infer
- xác nhận 16 camera process fail do decode resource hay do thứ khác

### Ưu tiên 2

Tối ưu topology runtime:

- giảm queue chatter
- xem lại worker/session mapping cho shared inference
- cân nhắc session pool riêng cho worker thay vì chia sẻ một session quá thô

### Ưu tiên 3

Đánh giá lại từng phase CPU-side:

- pose preprocess
- till mask decode
- reid packing/crop path
- face path khi bị kích hoạt dày hơn

### Ưu tiên 4

Nếu decode tiếp tục là điểm nghẽn lớn:

- nghiên cứu thay OpenCV/FFmpeg path hiện tại bằng decode worker tốt hơn
- hoặc ít nhất stagger startup / giới hạn số source mở đồng thời

## 9. Kết luận ngắn

Hiện tại:

- `process` mode vẫn là baseline rất tốt ở 4 camera
- nhưng không phải kiến trúc đích để scale
- `thread + shared batching` đã chứng minh được giá trị khi lên 16 camera vì batch size tăng thật và run sống
- bottleneck lớn tiếp theo không còn chỉ ở infer, mà là source/decode và orchestration

Tức là:

- chưa có lý do để bỏ batching
- nhưng cũng chưa thể kết luận batching đủ tốt
- bước đúng là tiếp tục bóc tách `decode` và `runtime topology` trước khi tối ưu sâu hơn vào model infer

