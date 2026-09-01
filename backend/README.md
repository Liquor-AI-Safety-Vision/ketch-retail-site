# Backend

Đơn vị ship. Copy riêng thư mục này lên một box GPU, cài
`requirements.txt`, là chạy được — không cần phần còn lại của repo.

Đó không phải mô tả nguyện vọng mà là một hợp đồng có test giữ:
`tests/test_backend_standalone.py` copy `backend/` sang thư mục trống rồi import
từng module trong interpreter mới. Nếu bất kỳ module nào với ra ngoài, test đỏ.

## Cấu trúc

| | |
| --- | --- |
| `src/perception/` | Detector, tracker, zone FSM, face align/identity, till. Lõi thị giác. |
| `src/pipelines/` | Nghiệp vụ: counter cash theft, storage intrusion, tracking baseline. |
| `src/runtime/` | Split architecture: decode / inference / business tier, ring buffer. |
| `src/infra/` | Alert store (SQLite + Postgres), media store (local + R2). |
| `src/app/` | Entry point: `run_split_pipeline.py`. `build_staff_db.py` nạp gallery nhân viên. |
| `config/` | Manifest store/camera/pipeline, zone, staff seed. |
| `models/` | 7 ONNX mà runtime thật sự nạp. |
| `tools/` | Bộ đo bench và test runner. |

## Đường dẫn

Ba gốc, tách nhau vì chúng di chuyển độc lập (`src/common/paths.py`):

- `backend_root()` — code, model, config. Luôn suy ra từ `__file__`, không bao
  giờ từ biến môi trường hay cwd.
- `data_root()` — footage, dataset. Đổi bằng `LIQUIR_DATA_ROOT`.
- `outputs_dir()` — artefact chạy ra. Đổi bằng `LIQUIR_OUTPUT_ROOT`.

Config dùng `${BACKEND_ROOT}` và `${DATA_ROOT}`. `${REPO_ROOT}` cũ vẫn được chấp
nhận và ánh xạ về `data_root()` — mọi chỗ nó từng xuất hiện trong manifest đều là
đường dẫn video.

## Test

```bash
python3 tools/run_tests_nopytest.py tests/test_backend_standalone.py
```

Không cần pytest: box GPU thường không có, và một bộ test không chạy được ở nơi
cần nó nhất thì không bảo vệ được gì.

Hai test giữ cấu trúc:

- `test_backend_standalone.py` — backend không cần ai.
- `test_repo_layout.py` — `dev/` và `demo/` vẫn gọi được vào backend, và backend
  không gọi ngược ra.

## Lưu ý vận hành

**Gọi `cap_native_threads()` trước khi import numpy, cv2 hay onnxruntime.** BLAS
sizing thread pool một lần lúc load thư viện; cap sau đó là lệnh rỗng. Vi phạm
luật này từng làm mất 5× runtime và tốn bốn vòng bisect để tìm ra —
`docs/backend/benchmarks/2026-08-26-till-thread-pool-oversizing.md`.

**Đừng tin `os.cpu_count()` hay `nproc` trong container.** Đọc cgroup `cpu.max`.
Box 4070 báo 16 nhưng quota là 15.36; một box vast.ai báo 192 trong khi quota 23.

**Xác minh execution provider bằng `InferenceSession` thật**, không phải
`get_available_providers()` — cái đó liệt kê thứ wheel được build kèm, không phải
thứ session sẽ nhận.
