# Source Layout

```text
src/
├── app/         entry point: run_split_pipeline.py, build_staff_db.py
├── common/      paths, logging, timing, stream URI helpers
├── domain/      contracts và entities, không phụ thuộc ONNX/OpenCV
├── perception/  detector, tracker, zone, FSM, face, till
├── pipelines/   logic nghiệp vụ theo pipeline_id
├── runtime/     decode / inference / face / business tier, ring buffer, outbox
└── infra/       SQLite, Neon, filesystem, R2, config loading
```

Không có HTTP API ở đây. Dịch vụ AI chỉ phát hiện và ghi kết quả (alert vào
Neon, clip/snapshot vào R2); dashboard đọc chúng qua Go API trong `backend/`.
