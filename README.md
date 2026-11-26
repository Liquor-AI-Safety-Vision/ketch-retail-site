# Liquor Store Security

Hệ thống giám sát cho chuỗi cửa hàng rượu: phát hiện lấy tiền tại quầy, đột
nhập kho ngoài giờ, người trong danh sách đen ở lối vào; chủ cửa hàng duyệt
alert và xem video bằng chứng trên dashboard.

## Cấu trúc

| thư mục | là gì | ngôn ngữ |
| --- | --- | --- |
| **`ai/`** | Dịch vụ AI. **Chỉ chạy logic phát hiện**: đọc luồng camera, ghi alert vào Neon và clip/snapshot vào R2. Không phục vụ HTTP. | Python |
| **`backend/`** | API duy nhất mà dashboard gọi: đăng nhập, phân quyền, store/camera/alert, duyệt alert, phát video, thông báo. Đọc chung Neon/R2 với `ai/`. | Go |
| **`frontend/`** | Dashboard web (Ketch). | React/TS |
| `deploy/` | Cấu hình chạy cả máy: MediaMTX, process manager, reverse proxy. | |
| `data/` | Footage và dataset. Không nằm trong git. | |
| `outputs/` | Scratch cục bộ. Không nằm trong git. | |

Lưu trữ dùng chung: **Neon** (Postgres) cho dữ liệu, **Cloudflare R2** cho
video và ảnh. Schema nghiệp vụ (`stores`, `cameras`, `alerts`, `alert_media`,
`persons`, `face_embeddings`, `owners`, `owner_stores`) do `ai/sql/` định nghĩa;
`backend/` theo schema đó.

## Hai chế độ chạy

- **test** — video 15 phút phát lặp như camera RTSP (`ai/tools/rtsp_sim.py`
  cùng MediaMTX).
- **production** — camera RTSP thật qua MediaMTX.

AI, backend và frontend không phân biệt hai chế độ; chỉ nguồn của MediaMTX khác.

## AI (`ai/`)

```bash
pip install -r ai/requirements.txt     # đọc kỹ ghi chú onnxruntime trong file
```

Model nằm trong `ai/models/`. Footage mặc định tìm ở `data/`, đổi bằng
`LIQUIR_DATA_ROOT`. Artefact chạy ra `ai/dev/outputs/`, đổi bằng `LIQUIR_OUTPUT_ROOT`.

Benchmark 16 camera trên box GPU:

```bash
./ai/tools/bench/run_n3_16cam.sh        # n=3, full stack
./ai/tools/bench/run_ladder_16cam.sh    # tier ladder: decode -> +infer -> full
./ai/tools/bench/run_cpu_compare.sh     # 4 và 16 camera, kèm CPU từ cgroup
```

Test (không cần pytest — box GPU thường không có):

```bash
for t in ai/tests/test_*.py; do python3 ai/tools/run_tests_nopytest.py $t; done
```

`ai/tests/test_ai_standalone.py` copy riêng `ai/` (bỏ `dev/`, `demo/`,
`docs/`) sang thư mục trống rồi import từng module trong một interpreter mới:
code ship không được với ra ngoài. `ai/dev/` và `ai/demo/` import vào
`ai/src`, không bao giờ ngược lại (`ai/tests/test_repo_layout.py`).

Kết quả đo: RTX 4070 + Ryzen 7700X, 16 camera, n=3: **99.1 FPS, biên 1.55×
realtime**, VRAM 2.9/12.3 GB. Nút thắt là **inference**. Khuyến nghị **4 cửa
hàng mỗi box**. Chi tiết: `ai/docs/benchmarks/`.
