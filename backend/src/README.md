# Source Layout

Code backend mới sẽ được viết trong khu vực này.

Đề xuất package layout:

```text
src/
├── app/
├── api/
├── domain/
├── runtime/
├── pipelines/
├── adapters/
└── infra/
```

Nguyên tắc:
- `domain/`: contracts và entities, không phụ thuộc ONNX/OpenCV.
- `adapters/`: detector/tracker/face/till wrappers.
- `pipelines/`: logic nghiệp vụ theo camera/pipeline type.
- `runtime/`: supervisor, worker, evidence buffer, dispatch.
- `infra/`: SQLite, Neon, filesystem, R2, config loading.
- `api/`: control plane và frontend feed.
