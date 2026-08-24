# Liquor Store Vision Backend

Repo nay chua code va tai lieu cho he thong backend mo phong multi-camera, multi-pipeline theo huong production-ready.

## Pham vi trong repo

- `backend/`: runtime, pipeline, infra, config local dev
- `docs/`: tai lieu giai phap va note ky thuat
- `scripts/`: utility script
- `bench/`: benchmark script nho, khong kem asset nang
- `outputs/zones/`, `outputs/top-side-view/`: zone config can thiet de chay local runtime

## Khong dua vao repo

De repo nhe va de push nhanh, cac tai nguyen sau duoc giu ngoai repo:

- video nguon
- model ONNX va artifact FP16
- dataset va demo artifact
- sqlite runtime, alert clip, snapshot, face chip
- output benchmark / visualization sinh ra trong qua trinh test

## Runtime modes hien co

- `seq`: xu ly tuan tu tung camera
- `process`: moi camera mot process
- `thread`: multi-camera thread mode, ho tro shared inference prototype

## Muc dich version hoa

Repo nay duoc tao de dong bang code/runtime theo cac moc benchmark:

- baseline `process`
- prototype `shared batching` trong `thread` mode

Ket qua benchmark va lenh chay duoc ghi trong `docs/benchmarks/`.

