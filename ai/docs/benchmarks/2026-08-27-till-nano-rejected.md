# 2026-08-27 — Till nano: nhanh hơn nhiều, nhưng bị loại vì bỏ sót ngăn kéo mở

Thử thay `till_seg_v4_640.onnx` (medium, 94 MB) bằng `till_seg_n640_last.pt`
export sang ONNX (nano, 11 MB). **Kết luận: giữ v4 medium, loại nano.**

Dữ liệu thô: `ai/dev/outputs/till_quality/`. Công cụ: `ai/dev/tools/diag_till_quality.py`.

## Tốc độ — nano thắng rõ ràng

Bench thô trên RTX 4070 (batch 1/2/4, 40 iter):

| batch | v4 medium | nano | nhanh hơn |
|---|---|---|---|
| 1 | 9.64 ms | 2.94 ms | 3.3× |
| 2 | 17.77 ms | 4.25 ms | 4.2× |
| 4 | 35.55 ms | 7.16 ms | 5.0× |

E2E một cửa hàng, 180 s:

| | 4070 medium | 4070 nano | 4060 Ti medium | 4060 Ti nano |
|---|---|---|---|---|
| FPS tổng | 95.51 | **105.49** (+10.5%) | 63.05 | **64.92** (+3.0%) |
| till time trong lane | 7.8 s | 4.4 s (−44%) | 12.5 s | 8.8 s (−30%) |

## Chất lượng — nano bỏ sót hơn nửa số frame ngăn kéo mở

Chạy raw detection (bỏ qua debounce của `TillRegistry`, vì registry sinh ra để
che giấu flicker — đo qua nó thì giấu mất đúng thứ cần đo), `conf=0.70` khớp
mặc định của `till_state.py`.

| | v4 medium | nano | |
|---|---|---|---|
| **`cash_till_open_1`, 25 s đầu** (ngăn kéo đóng suốt) | Open **0**/375 | Open **0**/375 | ✅ đạt |
| **`cash_till_open_1`, toàn bộ 59 s** | Open **27** | Open **27** | ✅ đạt |
| **`cash_till_open_2`, toàn bộ 210 s** | Open **102** | Open **46** | ❌ **bỏ sót 55%** |

Mốc 25 s đầu là mốc đã ghi sẵn trong comment `--till` của `till_state.py`: v2 cũ
báo Open sai ở 84% số frame, v4 sửa xuống 0. Nano giữ được thành tựu đó — nó
**không báo Open nhầm** bao giờ.

Nhưng trên `cash_till_open_2` nó **bỏ sót Open**, và đó là chiều sai nguy hiểm
hơn hẳn với bài toán chống trộm quầy: bỏ sót ngăn kéo mở nghĩa là bỏ sót đúng sự
kiện cần phát hiện.

81/630 frame bất đồng (12.9%). Trong 15 frame hiển thị được: **8 lần nano bỏ sót
Open, 0 lần báo thừa**. Pattern điển hình cho thấy khung hình có hai ngăn kéo, v4
thấy cả hai còn nano chỉ thấy cái đang đóng:

```
f830  v4=['Close','Open']  nano=['Close']
f855  v4=['Close','Open']  nano=['Close']
f870  v4=['Close','Open']  nano=['Close']
```

Chưa chạy chẩn đoán ở ngưỡng thấp (liệu nano có *thấy* mà điểm dưới 0.70 không) —
dừng ở đây theo quyết định bỏ nano. Nếu sau này muốn thử lại, đó là bước đầu tiên.

## Bẫy khi export: opset quyết định mọi thứ

Lần export đầu để ultralytics tự chọn **opset 22**. ORT 1.22 đẩy hàng loạt op về
CPU — **344 Memcpy nodes** trong log — và nano chạy **chậm hơn medium 3.8×**
(37.10 ms so với 9.76 ms ở batch 1). Nhìn con số đó mà không đọc cảnh báo thì sẽ
kết luận sai hoàn toàn là "model nano tệ".

Export lại ở **opset 13** (khớp `till_seg_v4_640.onnx` và `yolo26m-pose.onnx`
đang dùng) → còn **1 Memcpy node**, và thành nhanh hơn 3.3×.

**Quy tắc rút ra: export ONNX phải pin opset khớp với các model đang chạy được,
và phải đọc số Memcpy nodes trong log ORT trước khi tin bất kỳ số đo nào.**

## Một sai lầm suýt dẫn tới kết luận sai

Lượt e2e cho **6 alert ở cả medium lẫn nano**, và điều đó *thoạt nhìn* giống bằng
chứng nano không đánh đổi độ chính xác. Nhưng không phải: trong `store_1`, camera
counter cho **0 alert** ở cả hai lượt — 6 alert đó đến từ cold_storage/storage,
dựa trên nhận diện khuôn mặt, **không liên quan gì tới till**.

Số alert bằng nhau ở đây không kiểm chứng gì về till. Muốn kiểm chất lượng till
thì phải so trực tiếp trên chính đầu ra của model, như bảng trên.

## Trạng thái

- `DEFAULT_TILL_MODEL` giữ nguyên `ai/models/till_seg_v4_640.onnx` — không đổi gì.
- `ai/tools/diag_split_tiers.py` được thêm cờ `--till-model` (trước đó
  hardcode). Giữ lại: nó là cách so hai model mà không phải sửa mã.
- `ai/dev/tools/diag_till_quality.py` mới, giữ lại để lần sau có model mới thì
  chạy lại đúng bộ so sánh này thay vì dựng lại từ đầu.
