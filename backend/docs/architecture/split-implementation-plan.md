# Kế hoạch tách kiến trúc — mọi ràng buộc đã đo được

Tài liệu này gom **tất cả** những gì đã đo, đã sai, đã sửa trong quá trình khảo
sát. Mọi mục đều có số liệu kèm theo. Ai làm bước sau đọc file này là đủ, không
cần đọc lại lịch sử.

---

## 0. Mốc chuẩn và luật nghiệm thu

**Mốc chuẩn** (kiến trúc hiện tại, 16 camera × 11520 khung, 3 lượt):

| | Lượt 1 | Lượt 2 | Lượt 3 |
|---|---|---|---|
| FPS phục vụ | 123.12 | 113.42 | 115.87 |
| Alert | 24 | 24 | 24 |
| Track tạo | 396 | 396 | 396 |
| Tuổi thọ track | 38.85 | 38.85 | 38.85 |

**Luật:** alert và nhận dạng **tất định tuyệt đối** qua 3 lượt → mọi thay đổi ở
đó đều là thật. Thông lượng tản 8.6% → phán quyết theo nhóm:

> Lượt **tệ nhất** của bản mới phải thắng lượt **tốt nhất** của mốc chuẩn (**123.12**),
> **và** alert giữ nguyên 24 với đúng phân bố, **và** track không vỡ.

Công cụ: `backend/src/app/regression.py`, `run_regression_bench.sh`.

---

## 1. Ràng buộc vận chuyển khung hình

Đo trên chính máy này (`diag_ipc.py`), khung 1920×1080×3 = 6.2 MB, quy ra phần
trăm một lõi ở nhịp 112 FPS:

| Cách | ms/khung | % một lõi |
|---|---|---|
| Sàn của queue (chỉ chỉ số) | 0.92 | 10% |
| **Ghi thẳng vào khối + giữ mmap** | **0.98** | **11%** |
| Pool có copy vào | 3.08 | 35% |
| Cấp khối mới mỗi khung | 10.87 | 122% |
| Pickle mảng qua `mp.Queue` | 39.52 | 443% |

**Bắt buộc:**
- [ ] Khung đi qua `SharedMemory`; queue **chỉ** mang chỉ số ô + metadata nhỏ.
- [ ] Pool ô cố định, **không** cấp khối mới mỗi khung (uae làm thế, đắt hơn 3.5×;
      họ đã thử pool rồi bỏ — `FrameMemoryBank` còn nằm dạng comment).
- [ ] Bên đọc **giữ mmap** suốt đời tiến trình (mở lại mỗi khung phí 0.35 ms).
- [ ] Bộ giải mã **ghi thẳng** vào ô, không giải mã chỗ khác rồi copy vào.
- [ ] `/dev/shm` phải đủ lớn. Máy này 125 GB; **Docker mặc định 64 MB** — kiểm
      trước khi chạy ở môi trường khác.

---

## 2. Ràng buộc tiến trình / luồng

| Phát hiện | Số đo |
|---|---|
| Ngữ cảnh CUDA càng nhiều càng chậm | 19 ms @2 · 45 ms @4 · 153 ms @8 |
| Tầng suy luận **đơn luồng thua trắng** | 99.94 vs 118.35 FPS |
| Cho 2 luồng mỗi tiến trình suy luận | 138.81 vs 106.87 FPS |
| **4 luồng tệ hơn 2 luồng** | +13.8% vs +28.2% |
| `native_threads` càng nhiều càng tệ | 112 → 82 FPS ở 1 → 8 |

**Bắt buộc:**
- [ ] Đúng **2 tiến trình giữ CUDA**, không hơn.
- [ ] Mỗi tiến trình suy luận **2 luồng** — để CPU của luồng này che GPU của luồng
      kia. Không phải 1, không phải 4.
- [ ] `spawn`, không `fork` (CUDA cấm fork).
- [ ] `cap_native_threads(1)` mọi tiến trình.

---

## 3. Ràng buộc đúng đắn

- [ ] **Mỗi luồng suy luận một `Embedder` riêng.** `Embedder.__call__` ghi vào
      `self._pad_batch` dùng chung → chia sẻ là đua dữ liệu, hỏng đặc trưng. Lỗi
      này đã xảy ra thật, lộ ra qua 134 vs 132 track.
- [ ] **Mỗi camera ghim vào một luồng suy luận và một tiến trình nghiệp vụ.** Một
      hàng đợi có đúng một bên đọc thì giữ nguyên thứ tự ghi → **không cần tầng
      sắp xếp lại** (uae phải dựng `frame_sorting_worker` vì họ không ghim).
- [ ] Thứ tự khung là **bắt buộc**, không phải tuỳ chọn: `till_confirm` cần 5 mẫu
      **liên tiếp**.
- [ ] **Nạp model xong hết** rồi mới bắt đầu đo. Barrier hiện tại phải thành bắt
      tay xuyên tiến trình.
- [ ] Pose giữ **padding shape cố định** — CUDA EP phạt ~150 ms mỗi lần đổi shape.
- [ ] Cảnh báo khi gallery rỗng (đã có, giữ lại).

---

## 4. Hình dạng chuỗi — học từ uae, sửa cho mình

uae: `stream_cap → detection → [crop → reid → gộp] → sort → track`, **thẳng một
chiều**, tracking là chặng cuối và là thread.

Lấy được:
- [ ] **Cắt ảnh nằm trong tầng suy luận**, không ở tầng nghiệp vụ — nghiệp vụ
      không bao giờ phải đụng lại pixel.
- [ ] **Fan-out theo patch rồi gộp lại**: mỗi người một item, encoder gom lô, sau
      đó ráp lại theo khung. Patch từ nhiều camera rơi chung một lô nên lô đầy tự
      nhiên — tốt hơn cách gom theo từng khung rồi pad hiện tại.
- [ ] **Không có người thì bỏ qua GPU hẳn**, đẩy thẳng sang output.
- [ ] Tracking là chặng cuối, chạy bằng thread.

Không lấy được — uae **không có** tầng face trong pipeline (`face_analyzer.py`
nằm ngoài `start_all_workers`), không có till, không có FSM.

**Lối thoát cho face, tìm được trong code của mình:** `EntryFaceWindow.consider()`
gom quyết định qua cả một cửa sổ, `resolved_decision()` chỉ đọc khi cửa sổ đóng.
Nên face **không cần trả lời trong cùng khung**:

- [ ] Face chạy **bắn-rồi-quên**: nghiệp vụ gửi yêu cầu rồi đi tiếp, kết quả về
      sau thì nhét vào cửa sổ đang mở. Không có round-trip chặn.

Chuỗi cuối cùng:

```
giải mã + tiền xử lý ─→ suy luận (pose → crop → reid) ─→ nghiệp vụ (tracker, FSM, clip, alert)
      không CUDA            2 proc × 2 luồng, giữ CUDA              không CUDA
                                                                          └╌╌→ face (bất đồng bộ)
      till ────────────────────────→ nhánh song song, độc lập với người
```

---

## 5. Các bước

### Bước 1 — Tách tầng suy luận thành server *(đang làm)*
Biến `SharedInferenceRegistry` thành client/server: server giữ model trong tiến
trình riêng, client giữ **nguyên API cũ** để pipeline không phải sửa.
`SharedPoseService`/`SharedTillService` đã có sẵn hình dạng request/queue
(`self.runner.submit({...})`) nên đây là **đổi phương tiện vận chuyển, không đổi
cấu trúc**.

- [ ] Server process: pose, reid, till, face detector, arcface
- [ ] Client proxy giữ nguyên chữ ký `detect()` / `__call__()` / `identify()`
- [ ] Khung qua shm theo mọi ràng buộc mục 1
- [ ] Bench: đo **chi phí thật của một chặng round-trip**, tách khỏi mọi thứ khác

### Bước 2 — Tách tầng giải mã
- [ ] Tiến trình giải mã không giữ CUDA, ghi thẳng vào ô shm
- [ ] Tiền xử lý pose nằm cùng chỗ (đã dời sẵn về luồng camera, đi đúng hướng)

### Bước 3 — Tách tầng nghiệp vụ
- [ ] Pipeline chạy ở tiến trình riêng, giữ ghim theo camera
- [ ] Ghi clip chuyển sang luồng nền (GĐ 2A cũ, giờ gộp vào đây)

### Bước 4 — Bench full
- [ ] 16 luồng × 3 phút, **đủ till + face + clip + alert + SQLite**
- [ ] So với mốc chuẩn theo luật mục 0

---

## 6. Đã thử, đã loại — đừng làm lại

| | Kết quả |
|---|---|
| CUDA MPS | trong nhiễu (~3%) |
| Đổi model n/s/m | 4.93 / 5.00 / 5.71 ms — không khác |
| `blobFromImage` | không đổi, đã revert |
| Chờ gom lô thật (`batch_wait_s`) | 43 → 26 FPS, càng chờ càng tệ |
| Tăng `native_threads` | 112 → 82 FPS |
| Lọc chuyển động | ngưỡng tuyệt đối chỉ fit 4 video; cần ngưỡng tự hiệu chỉnh |
| Tinh chỉnh `hybrid_workers` | sẽ bị thay thế, đừng đầu tư thêm |
| TensorRT | thiếu `libnvinfer.so.10` |

**Hoãn đến sau khi tách:** nhiều phiên theo kích thước lô. Đo bây giờ sẽ ra ~0 vì
16 camera bị chia cho 2 tiến trình; sau khi tách thì hàng đợi mới đủ dày.
