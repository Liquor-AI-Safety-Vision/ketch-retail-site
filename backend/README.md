# Backend

Thư mục này là khu phát triển mới cho hệ thống backend production-shape.

Mục tiêu:
- Tách riêng code hệ thống mới khỏi `scripts/` đang dùng cho POC, benchmark và demo.
- Giữ được cùng một logic nghiệp vụ, nhưng đưa về runtime/cơ cấu phù hợp backend thật.
- Cho phép triển khai local trước, sau đó nâng cấp lên Neon + Cloudflare mà không đập đi làm lại.

Nguyên tắc tổ chức:
- `docs/`: tài liệu solution architecture, implementation notes, ADR.
- `src/`: code backend chính.
- `config/`: config mẫu, store/camera/pipeline binding, zone asset manifests.
- `sql/`: schema SQL và migration local-first.
- `tests/`: test cho runtime, adapters và pipeline contracts.

Tài liệu bắt đầu:
- [docs/SOLUTION_ARCHITECTURE.md](/Users/ngoquangduc/Desktop/workspace/liquir_store/backend/docs/SOLUTION_ARCHITECTURE.md)
- [docs/EXECUTION_PLAN.md](/Users/ngoquangduc/Desktop/workspace/liquir_store/backend/docs/EXECUTION_PLAN.md)
- [config/stores/local_dev_store.json](/Users/ngoquangduc/Desktop/workspace/liquir_store/backend/config/stores/local_dev_store.json)

Lưu ý:
- `scripts/` vẫn là nguồn module/pipeline POC hiện tại và sẽ được tái sử dụng có chọn lọc.
- Code mới cho backend không nên mở rộng tiếp trong `scripts/` trừ khi đó là benchmark/inspection.
- Manifest local-dev hiện đã mô tả cả hai hướng nguồn vào:
  - `video_file` để giả lập streaming trên máy local
  - `rtsp` để sau này thay bằng camera thật mà không đổi business schema
