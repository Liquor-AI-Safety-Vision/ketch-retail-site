# Liquor Store Vision Backend

Hệ thống backend multi-camera, multi-pipeline cho chuỗi cửa hàng rượu: phát hiện
trộm tiền tại quầy, đột nhập kho ngoài giờ, và tracking toàn cửa hàng.

## Năm thư mục

| thư mục | nội dung |
| --- | --- |
| **`backend/`** | **Đơn vị ship. Chạy độc lập, không import gì bên ngoài.** Code, model, config, schema, test. |
| `data/` | Footage và dataset — nguyên liệu, không phải code. Không nằm trong git. |
| `dev/` | Đồ nghề: probe, điều tra đã đóng, repo tham chiếu, artefact chạy thử. |
| `demo/` | Video đã render và script render chúng. |
| `docs/` | Tài liệu giải pháp, kiến trúc, và toàn bộ benchmark. |

Ranh giới duy nhất phân chia chúng: **cái gì cần để CHẠY ở chỗ khách hàng, và
cái gì chỉ cần để LÀM RA nó.**

### Phụ thuộc là một chiều

`dev/` và `demo/` import vào `backend/`. **Backend không import ngược ra.**

Điều này được giữ bằng test, không bằng thiện chí:
`backend/tests/test_backend_standalone.py` copy riêng `backend/` sang thư mục
trống rồi import từng module trong một interpreter mới. Trước khi có nó,
`backend/src` với tới `scripts/` bằng cách chèn sys.path lúc import — trong
checkout thì chạy hoàn hảo, copy lên box GPU thì 9 module chết, gồm cả ba
pipeline và supervisor. **Không thứ gì nhìn từ trong repo thấy được lỗi đó.**

## Chạy

```bash
pip install -r backend/requirements.txt     # đọc kỹ ghi chú onnxruntime trong file
```

Model nằm trong `backend/models/`. Footage mặc định tìm ở `data/`, đổi bằng
`LIQUIR_DATA_ROOT`. Artefact chạy ra `dev/outputs/`, đổi bằng `LIQUIR_OUTPUT_ROOT`.

Benchmark 16 camera trên box GPU:

```bash
./backend/tools/bench/run_n3_16cam.sh        # n=3, full stack
./backend/tools/bench/run_ladder_16cam.sh    # tier ladder: decode -> +infer -> full
./backend/tools/bench/run_cpu_compare.sh     # 4 và 16 camera, kèm CPU từ cgroup
```

Test:

```bash
python3 backend/tools/run_tests_nopytest.py backend/tests/test_backend_standalone.py
```

`run_tests_nopytest.py` tồn tại vì box GPU thường không có pytest, và một bộ
test không chạy được ở nơi cần nó nhất thì không bảo vệ được gì.

## Kết quả đã đo

RTX 4070 + Ryzen 7700X, 16 camera, n=3: **99.1 FPS, biên 1.55× realtime**, VRAM
2.9/12.3 GB, biên độ giữa ba lượt 0.28%. Nút thắt là **inference**, không phải
decode và không phải CPU. Khuyến nghị **4 cửa hàng mỗi box**.

Chi tiết và phương pháp: `docs/backend/benchmarks/`.
