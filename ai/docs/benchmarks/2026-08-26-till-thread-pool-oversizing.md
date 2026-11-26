# 2026-08-26 — Till 57 ms: sáu vòng bisect, và cái thước đo bị hỏng

Ghi lại từ 7 file đã **xoá** (`diag_till.py` + `diag_till_bisect{,2..6}.py`,
~820 dòng trong `ai/tools/`) — chúng không còn trong cây, xem lịch sử git
nếu cần đọc code. Nguyên nhân đã tìm ra và cách sửa đã nằm trong
code (`ai/src/runtime/perf.py`), nhưng **kết quả đo thì chưa được ghi ở
đâu cả** — đây là cú tối ưu lớn nhất của dự án mà lịch sử benchmark không có
dòng nào.

## Triệu chứng

Module sweep chỉ ra till là trần của cả split pipeline:

| model | call/s |
|---|---|
| pose | 172 |
| ReID | 114–357 |
| **till** | **17.9** |

Và till là model duy nhất **chậm đi khi thêm thread**, thay vì nhanh lên. Đó là
dấu hiệu không khớp với "model nặng", nó khớp với "tranh chấp tài nguyên".

Mâu thuẫn trung tâm: cùng một `sess.run`, cùng model, cùng batch=1, cùng
provider —

- gọi trần: **6.5–8.1 ms**
- gọi trong `SharedTillService`: **35.6 ms** (profile của service tự đổ lỗi cho
  đúng dòng `sess.run`), tổng một `detect()` là **56–60 ms**

## Sáu vòng bisect

| vòng | khoá biến gì | kết luận lúc đó |
|---|---|---|
| 1 | session options, input thật, background thread, session của service gọi tay | session **khoẻ** ở mọi biến thể; chỉ đường đi đầy đủ mới chậm |
| 2 | thread-pool handoff giữa OpenCV và onnxruntime | **bác bỏ** — G 10.8 ms so với K 6.5 ms |
| 3 | thân `_infer_batch` so với batch runner | runner không phải thủ phạm; gọi thẳng đã 56 ms |
| 4 | buffer dùng lại so với cấp phát mới mỗi lần | **bác bỏ** — 6.58 so với 6.73 ms |
| 5 | đo từng đoạn bên trong `_infer_batch` | ~45/56 ms vẫn không quy được về đâu |
| 6 | A/B ba session xen kẽ trong cùng một process | **các phép đo trước không so sánh được với nhau** |

Vòng 6 là chỗ cuộc điều tra tự phát hiện nó đang đo sai: cùng một khối lượng
công việc cho **10.14 ms** ở process của bisect4 và **53.6 ms** ở bisect5, mà
code không khác gì nhau.

## Nguyên nhân thật

Container báo `os.cpu_count() = 192`, trong khi **cgroup quota chỉ 23 core**.

OpenBLAS / OpenMP / MKL sizing thread pool **một lần duy nhất lúc thư viện được
load**, đọc từ biến môi trường. Chúng thấy 192 → dựng pool 192 thread để chạy
trên 23 core. OpenBLAS **spin trước khi ngủ**. Một phép matmul mask bé tí trong
postprocess của till để lại hàng chục thread quay tít, cướp mất CPU mà lần
`sess.run` kế tiếp cần.

| | trước | sau |
|---|---|---|
| mỗi call till | 60.08 ms | **11.67 ms** |
| thông lượng | 16.65 call/s | **85.68 call/s** |

Khoảng **5×** toàn bộ runtime.

## Vì sao mất sáu vòng: cái thước đo bị hỏng

Vòng 2 đã thử **đúng** giả thuyết này và **bác bỏ nó**.

Nó sai vì `cap_native_threads(1)` lúc đó **là lệnh rỗng**. Hàm set
`OMP_NUM_THREADS`, nhưng module gọi nó đã `import numpy` trước — mà pool đã được
dựng xong từ lúc đó và không bao giờ đọc lại biến môi trường. Nên "đã cap" và
"chưa cap" cho ra cùng một số, và kết luận rút ra là "không phải thread pool".

Bốn vòng tiếp theo đi tìm một thủ phạm không tồn tại.

**Bài học: khi một giả thuyết bị bác bỏ, phải kiểm chứng rằng phép can thiệp
thật sự đã xảy ra, trước khi tin vào kết quả "không đổi".** Ở đây chỉ cần in ra
`OMP_NUM_THREADS` sau khi gọi cap là đủ để chặn bốn vòng lãng phí.

## Cách sửa, và chỗ nó sống bây giờ

Quy tắc: **gọi `cap_native_threads()` trước khi import numpy, cv2 hay
onnxruntime** — trên thực tế là việc đầu tiên mà một entry point làm. Process
con thừa kế `os.environ` như lúc process cha spawn chúng, nên cha cap đủ sớm là
sửa cho toàn bộ con.

`ai/src/runtime/perf.py` giữ luật này, và **tự cảnh báo khi bị gọi sai thứ
tự** — chính là cái bẫy đã làm hỏng vòng 2:

```
cap_native_threads(1) ran after numpy, cv2, onnxruntime was already imported:
the BLAS/OpenMP pools are already sized and will not shrink in this process.
```

`cpu_quota()` tồn tại vì `os.cpu_count()` là con số sai để sizing bất cứ thứ gì
trong container: nó báo core của host, không phải core mà process này được dùng.
Trên box 4070 độ lệch nhẹ hơn (`nproc` 16 so với quota 15.36) nhưng vẫn phải đo
từ cgroup `cpu.stat`, không phải từ `top`.
