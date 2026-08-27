# 2026-08-27 — Một cửa hàng (4 camera) trên L40S: định cỡ phần cứng edge box

Máy: NVIDIA L40S 46 GB (vast.ai). Manifest `backend/config/stores/store_1.json`.
Split arch, `--duration 180`, `--layer all`. Dữ liệu thô:
`outputs/bench_store1_20260827/`.

**Mục đích khác hẳn các benchmark trước.** Các mốc 16 camera đo *thông lượng tối đa*
của một máy. Lần này đo *một cửa hàng thật chiếm bao nhiêu phần của máy*, để trả lời
câu nên mua card gì cho edge box đặt tại store.

## Vì sao không dùng `--cameras 4` trên manifest 16 cam

Manifest 16 cam xếp theo nhóm (`counter_1-4`, `cold_storage_1-4`, ...), nên cắt 4
camera đầu cho ra **4 camera counter** — 100% tải till, không có camera nào chạy
face. Lệch tải khoảng 3.2× so với cửa hàng thật.

`store_1.json` có đúng hình dạng một cửa hàng: mỗi loại một camera.

| camera | pipeline | target FPS |
|---|---|---|
| `counter_1` | `counter_cash_theft_v1` (till) | 5 |
| `whole_store_1` | `whole_store_tracking_v1` | 5 |
| `cold_storage_1` | `cold_storage_intrusion_v1` (face) | 3 |
| `storage_1` | `storage_intrusion_v1` (face) | 3 |
| | | **tổng 16** |

## Kết quả

| | A — 1 inferer × 4 lane | B — 2 inferer × 2 lane |
|---|---|---|
| CUDA context | 2 (1 infer + 1 face) | 3 (2 infer + 1 face) |
| Thời gian thực chạy | 46.6 s | **41.1 s** |
| Bội số realtime | 3.86× | **4.38×** |
| FPS tổng | 61.75 | **70.05** |
| VRAM đỉnh | **2630 MiB (2.57 GB)** | 3547 MiB (3.46 GB) |
| GPU utilization | 48–52% | 78–84% |
| Alert | 6 | 6 |

FPS từng camera:

| Camera | Target | A | B |
|---|---|---|---|
| `counter_1` | 5 | 19.30 | 21.89 |
| `whole_store_1` | 5 | 19.29 | 21.89 |
| `cold_storage_1` | 3 | 11.58 | 13.13 |
| `storage_1` | 3 | 11.58 | 13.14 |

Cả 4 luồng trong mỗi lượt đều ra **đúng cùng một bội số** — backpressure chia đều,
không luồng nào bị bỏ đói. Alert giống hệt nhau giữa hai cấu hình (4 cold_storage +
2 storage), nên B nhanh hơn thật, không phải nhanh do làm ít việc hơn.

## Nút thắt là đúng một camera: counter

```
A  lane 0 (counter_1)      busy 46.9s  idle  3.4s   pose=18.1 [wait 5.6/run 12.5] reid=16.6 till=12.2
A  lane 1 (cold_storage_1) busy 19.2s  idle 31.1s
A  lane 2 (storage_1)      busy 17.7s  idle 32.6s
A  lane 3 (whole_store_1)  busy 30.4s  idle 19.9s

B  infer-1 lane 0 (counter_1)  busy 41.2s  idle  5.4s   pose=14.7 [wait 1.3/run 13.4] reid=15.2 till=11.1
```

Camera counter gần bão hoà trong cả hai cấu hình (idle 3.4s / 5.4s) trong khi ba
camera còn lại rỗi 20–33 giây. **Toàn bộ nhịp của một cửa hàng do đúng một camera
counter quyết định** — vì nó là camera duy nhất chạy till.

B nhanh hơn 13% nhờ tách counter sang context riêng: `pose wait` của nó giảm
**5.6s → 1.3s**. Đây là cùng một hiệu ứng đã đo ở mốc 16 camera (gom việc giống nhau
vào một process thắng chia đều camera), chỉ khác quy mô.

## Decode là đồng-nút-thắt, không phải chuyện nhỏ

| decode | A | B |
|---|---|---|
| `counter_1` | 48.4 s | 43.2 s |
| `whole_store_1` | 31.8 s | 24.1 s |
| `cold_storage_1` | 27.6 s | 20.6 s |
| `storage_1` | 21.6 s | 16.9 s |

Decode `counter_1` (43.2 s) **dài hơn cả thời gian chạy của toàn hệ** (41.1 s) ở lượt
B. Decode là CPU thuần, và `VirtualVideoStreamSource` `grab()` mọi frame nguồn rồi chỉ
`retrieve()` frame được phát — ở target 5 FPS trên phim 25–30 FPS, nó duyệt 5–6× số
frame mà con số FPS gợi ý.

Đây là hạng mục cần theo dõi nhất khi chuyển sang edge box, vì đó là nơi CPU yếu đi
nhiều nhất so với máy benchmark.

## CPU thật của máy benchmark: 23 core, không phải 192

`nproc` báo 192 và affinity `0-191`, nhưng cgroup v2 `cpu.max = 2304000 100000` →
quota thật **23.04 core**. Mọi so sánh CPU lấy từ 192 đều sai ~8×.

Điều này thu hẹp khoảng cách với edge box: 23 core so với 8–16 core chỉ là **1.4–2.9×**,
không phải ~15×. Decode CPU trên edge box khả thi hơn nhiều so với ước tính ban đầu.

## Kết luận cho việc chọn phần cứng

**Một cửa hàng dùng khoảng một nửa L40S** (GPU 48–52% ở cấu hình A). L40S là thừa cho
một store.

**VRAM: 2.6–3.5 GB.** Card **8 GB là đã dư**. Ước tính trước đó (~6 GB, suy từ mốc
hybrid 6.2 GB/16 camera) là **sai theo hướng thừa** — VRAM còn phụ thuộc số camera qua
arena/activation, không chỉ số CUDA context.

### Cảnh báo về ngoại suy sang card yếu hơn

`utilization.gpu` của `nvidia-smi` đo *có kernel nào đang chạy hay không*, không đo mức
lấp đầy. Với 3 context (lượt B) các kernel chia thời gian nên chỉ số này bị đội lên —
so 50% (A) với 80% (B) **không** có nghĩa B tốn nhiều tính toán hơn; hai lượt làm đúng
cùng khối lượng model. Vì vậy không được nhân thẳng tỉ lệ util để suy ra card yếu hơn.

Ước tính thô cho card ~1/3 compute L40S (RTX 4070-class): còn khoảng **2–2.5× realtime**
cho một cửa hàng. Đủ biên an toàn, nhưng **là ngoại suy, chưa đo**.

### Chưa đo, phải đo trước khi chốt mua

- Toàn bộ số trên đọc **file video từ đĩa**, `pace_realtime=False`. `source_kind=rtsp`
  vẫn `NotImplementedError` — chưa có số nào tính jitter mạng, mất gói, hay chờ keyframe.
- Chưa chạy trên card tiêu dùng thật.
