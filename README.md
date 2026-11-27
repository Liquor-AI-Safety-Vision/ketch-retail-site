# Liquor Store Security

Hệ thống giám sát cho chuỗi cửa hàng rượu: phát hiện lấy tiền tại quầy, đột
nhập kho ngoài giờ, người trong danh sách đen ở lối vào; chủ cửa hàng xem live,
duyệt alert và xem video bằng chứng trên dashboard, nhận thông báo Telegram/WhatsApp.

```
camera ──► MediaMTX ──► AI (ai/, Python) ──► Neon + R2 ◄── API (backend/, Go) ◄── dashboard (frontend/)
              └──────── live HLS (/live, API kiểm quyền) ──────────────────────────────┘
```

## Cấu trúc

| thư mục | là gì | ngôn ngữ |
| --- | --- | --- |
| **`ai/`** | Dịch vụ AI. **Chỉ chạy logic phát hiện**: đọc luồng camera, ghi alert vào Neon, clip/snapshot vào R2, heartbeat camera. Không phục vụ HTTP. | Python |
| **`backend/`** | API duy nhất mà dashboard gọi: đăng nhập, phân quyền, store/camera/alert, duyệt alert, phát video qua link ký, live view, thông báo. Đọc chung Neon/R2 với `ai/`. | Go |
| **`frontend/`** | Landing page Ketch, demo công khai (tĩnh) và dashboard của chủ cửa hàng. | React/TS |
| **`deploy/`** | Chạy cả hệ thống trên một server GPU: MediaMTX, AI, API, Caddy dưới supervisor; mode test/production. | |
| `data/` | Footage và dataset. Không nằm trong git. | |
| `outputs/` | Scratch cục bộ. Không nằm trong git. | |

Lưu trữ dùng chung: **Neon** (Postgres) cho dữ liệu, **Cloudflare R2** cho video
và ảnh. Schema nghiệp vụ (`stores`, `cameras`, `alerts`, `alert_media`, `persons`,
`face_embeddings`, `owners`, `owner_stores`) do `ai/sql/postgres_schema.sql` định
nghĩa; `backend/` theo schema đó và thêm bảng của riêng nó (phiên đăng nhập, lịch
sử duyệt, thông báo) — xem `backend/docs/database-design.md`.

Mỗi service có `.env` riêng (không vào git): `ai/.env` và `backend/.env` trỏ cùng
một DB Neon và bucket R2; `frontend/.env` chỉ có `VITE_API_BASE_URL`. Chi tiết:
`backend/docs/environment.md`.

## Hai chế độ chạy

- **test** — mỗi camera phát lặp video của chính nó trong manifest (như camera
  RTSP thật); AI phân tích live và ghi alert thật.
- **production** — camera RTSP thật, khai báo trong `deploy/cameras.production.json`.

AI, backend và frontend không phân biệt hai chế độ; chỉ nguồn của MediaMTX khác.
Đổi bằng `deploy/liquorctl mode test|production`. Hướng dẫn deploy đầy đủ:
[`deploy/README.md`](deploy/README.md).

## Hai kiểu dashboard

- **Demo công khai** (nút "View demo" trên landing): tĩnh hoàn toàn, không gọi
  backend; camera loop 15 s và clip alert cắt từ footage của dự án
  (`ai/tools/build_frontend_demo.py`).
- **Dashboard đăng nhập** (`/#/login`): dữ liệu thật của từng owner — chỉ hiển thị
  những gì AI đã ghi. Tạo tài khoản: `deploy/liquorctl owner <email> <tên> <store_id>`
  (mật khẩu qua `OWNER_PASSWORD`).

## Phát triển

```bash
# AI
pip install -r ai/requirements.txt      # + onnxruntime hoặc onnxruntime-gpu
for t in ai/tests/test_*.py; do python3 ai/tools/run_tests_nopytest.py $t; done

# API (Go 1.26+): test DB cần Postgres; mỗi test chạy trong schema riêng
cd backend && go test ./...                                   # unit
RUN_INTEGRATION_TESTS=1 DATABASE_URL=postgresql://… go test ./...   # + tích hợp

# Frontend
cd frontend && npm ci && npm run dev    # proxy /api sang localhost:3000
npm run lint && npm run test:security && npm run build
```

`openapi_sync_test.go` giữ `backend/internal/server/openapi.json` khớp với route
thật; `internal/migrations` có test bảo đảm bản schema AI nhúng trong Go giống hệt
`ai/sql/postgres_schema.sql`.

## AI (`ai/`)

Model nằm trong `ai/models/`. Footage mặc định tìm ở `data/`, đổi bằng
`LIQUIR_DATA_ROOT`. Artefact chạy ra `ai/dev/outputs/`, đổi bằng `LIQUIR_OUTPUT_ROOT`.

Benchmark 16 camera trên box GPU:

```bash
./ai/tools/bench/run_n3_16cam.sh        # n=3, full stack
./ai/tools/bench/run_ladder_16cam.sh    # tier ladder: decode -> +infer -> full
./ai/tools/bench/run_cpu_compare.sh     # 4 và 16 camera, kèm CPU từ cgroup
```

`ai/tests/test_ai_standalone.py` copy riêng `ai/` (bỏ `dev/`, `demo/`,
`docs/`) sang thư mục trống rồi import từng module trong một interpreter mới:
code ship không được với ra ngoài. `ai/dev/` và `ai/demo/` import vào
`ai/src`, không bao giờ ngược lại (`ai/tests/test_repo_layout.py`).

Kết quả đo: RTX 4070 + Ryzen 7700X, 16 camera, n=3: **99.1 FPS, biên 1.55×
realtime**, VRAM 2.9/12.3 GB. Nút thắt là **inference**. Khuyến nghị **4 cửa
hàng mỗi box**. Chi tiết: `ai/docs/benchmarks/`. Deploy test trên RTX 3070
(7 camera store_test_01, mode test): 5 fps mỗi camera, p99 < 120 ms.
