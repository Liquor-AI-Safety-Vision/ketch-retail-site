# Chuyển sang RTSP: giả lập, và cái bẫy buffer phải xử lý

`source_kind=rtsp` vẫn `NotImplementedError`
([virtual_video_stream.py:71](../src/runtime/virtual_video_stream.py#L71)). Đây là
khoảng trống lớn nhất giữa số benchmark và thực tế triển khai — mọi con số từ
trước tới giờ đều **đọc file từ đĩa, chạy hết tốc lực** (`pace_realtime=False`).

Tài liệu này ghi lại bước 1 (giả lập RTSP, đã xong) và những gì đo được từ nó,
vốn quyết định thiết kế của `RtspStreamSource` ở bước 2.

## Bước 1 — giả lập RTSP: `backend/tools/rtsp_sim.py`

MediaMTX làm RTSP server, mỗi camera một tiến trình ffmpeg phát lại chính footage
mà benchmark đang dùng.

```bash
python backend/tools/rtsp_sim.py --start        # dựng config rồi phục vụ
python backend/tools/rtsp_sim.py --check        # probe từng luồng
python backend/tools/rtsp_sim.py --print-urls   # URL để gắn vào manifest
```

### Trung thực ở những điểm này

- **Bitstream y hệt.** ffmpeg chạy `-c copy`, không mã hoá lại, nên GOP, khoảng
  keyframe và bitrate đúng như camera đã ghi. Footage này vốn quay từ CCTV thật.
- **Nhịp thời gian thực** qua `-re`. Nguồn không thể chạy nhanh hơn realtime —
  đây là thứ mọi benchmark trước đây thiếu.
- **Luồng luôn sống.** Publisher khởi động cùng server và tự restart, nên client
  rớt rồi nối lại sẽ vào giữa luồng như với camera thật, không phải phát lại file
  từ đầu.
- **Có mật khẩu.** Camera production đều yêu cầu, và mã chỉ từng mở URL không xác
  thực là mã có đường credential chưa bao giờ chạy. Đã kiểm: sai mật khẩu trả về
  `401 Unauthorized`.
- **TCP.** Transport sẽ dùng khi đi qua WAN. UDP vẫn bật để so sánh riêng.

### KHÔNG trung thực ở những điểm này

Không được đọc kết quả từ giả lập như bằng chứng về các điểm sau:

- Jitter, mất gói, độ trễ mạng — chạy trên loopback nên gần bằng 0
- Giới hạn băng thông
- Quirk theo hãng: phương ngữ RTSP của Dahua/Hikvision, lệch đồng hồ, VBR khi
  cảnh thay đổi, camera tự bỏ frame khi quá tải
- Giới hạn số client RTSP đồng thời của đầu ghi
- MediaMTX báo `RTP packets are too big (1460 > 1440), remuxing` — nó tự chia lại
  gói, camera thật thì phát đúng kích thước ngay từ đầu

Nên giả lập trả lời được **"RTSP có đắt hơn đọc file không"** và không nói gì về
**"mạng WAN có ổn định không"**. Tách hai câu là cố ý: gộp lại thì một hồi quy
xuất hiện sẽ không quy được cho bên nào.

### Một sai lệch có chủ đích

Đường dẫn ở đây là `camera_id`, không phải `/cam/realmonitor?channel=N&subtype=0`
của Dahua. MediaMTX định tuyến theo path và bỏ qua query, nên một URL Dahua đúng
chuẩn không thể phân biệt 16 luồng. Thứ thực sự cần kiểm là mã client có xử lý
được credential và query string hay không — hình thức URL thì không.

## Đo được: cái bẫy buffer, và nó nghiêm trọng

Đây là phát hiện quan trọng nhất từ bước 1.

`cv2.VideoCapture` có buffer nội bộ. Với file thì vô hại. Với luồng trực tiếp, khi
**đọc chậm hơn nguồn** thì frame dồn lại và bạn đọc phải frame cũ — độ trễ tăng
dần không giới hạn.

Pipeline này **luôn luôn** ở tình trạng đó: `target_fps` là 3-5 trong khi camera
phát 15.

### Cách sai — `read()` theo target_fps

Đọc 5 fps từ nguồn 15 fps, trong 12 giây:

```
đã đọc 58 frame trong 12.2s = 4.75 fps
nguồn đã phát ~183 frame; ta đọc 58 -> 125 frame nằm trong buffer

độ trễ sau 12s — mỗi read() tốn:
  read 0:  8.3 ms   <- tức thì = đang đọc frame CŨ
  read 1:  6.3 ms
  ...
  read 5:  2.9 ms
```

`read()` trả về tức thì nghĩa là frame đã nằm sẵn trong buffer. Sau 12 giây đã trễ
~8 giây, và trễ tiếp tục tăng. Chạy vài giờ thì alert nói về chuyện xảy ra từ lâu.

### Cách đúng — `grab()` mọi frame, `retrieve()` frame cần

```
grab 194 (16.07/s, nguồn 15) | retrieve 51 (4.22/s, target 5)

độ trễ sau 12s — mỗi grab() tốn:
  grab 0: 58.5 ms   <- chờ frame mới = LIVE
  grab 1: 62.4 ms
  ...
  grab 5: 66.3 ms
```

`grab()` chặn ~66 ms ≈ 1/15 giây, tức đang đợi frame kế tiếp của nguồn. Không tồn
đọng, độ trễ không tăng.

**Kiến trúc hiện tại đã làm đúng kiểu này cho file** — `VirtualVideoStreamSource`
`grab()` mọi frame nguồn và chỉ `retrieve()` frame nó phát ra. Nên chuyển sang RTSP
không phải viết lại từ đầu, nhưng **bắt buộc phải giữ đúng tính chất đó**.

Hệ quả về chi phí: phải giải mã đủ 15 fps dù chỉ dùng 5, vì `grab()` vẫn decode
(nó chỉ bỏ qua bước chuyển màu và copy Mat). Với file cũng vậy, nên đây không phải
chi phí phát sinh thêm khi chuyển sang RTSP.

## Bước 2 — `RtspStreamSource`: xong

[`backend/src/runtime/rtsp_stream.py`](../src/runtime/rtsp_stream.py), cùng surface
với `VirtualVideoStreamSource`. Chọn lớp qua `open_stream_source(spec)`, đã gắn vào
`decode_tier.py` và `camera_runtime.py`.

Khác biệt so với nguồn file, và lý do phải là lớp riêng chứ không phải nhánh `if`:

- **Không có gì để seek.** `start_s` và `loop_to_duration` mô tả việc phát lại bản
  ghi. Cả hai bị **từ chối thẳng**, không phải bỏ qua im lặng — manifest bảo camera
  bắt đầu từ giây 30 là manifest có hiểu nhầm, phải lộ ra lúc khởi động.
- **Luồng chỉ kết thúc bằng lỗi.** EOF của file là bình thường; `grab()` thất bại ở
  camera nghĩa là đứt kết nối, và câu trả lời là **kết nối lại**, không phải kết thúc.
- **Nguồn quyết định nhịp.** Nên `pace_realtime` được nhận nhưng **cố tình bỏ qua**:
  ngủ thêm sẽ giữ nguồn vĩnh viễn tụt sau live, đúng cái lỗi lớp này sinh ra để tránh.

Một chi tiết đáng nói: `native_fps` lấy từ `CAP_PROP_FPS`, và khi camera không báo thì
dùng hằng số, **không đo**. Vì đo lúc khởi động là đo buffer đang xả chứ không phải
camera: đã đo được 21 fps trong giây đầu trên nguồn thật sự 15 fps.

### Hai lỗi im lặng phải sửa kèm

**URI camera bị phá trước khi ai kịp mở nó.** `resolve_repo_path` coi mọi thứ là
đường dẫn file, nên `rtsp://host/path` thành `<repo>/rtsp:/host/path` — `Path()` gộp
hai dấu gạch, kết quả không tuyệt đối nên bị ghép thêm repo root. Không có exception
nào, chỉ là camera cấu hình đúng mà không mở được. Sửa bằng
[`stream_uri.resolve_source_uri`](../src/common/stream_uri.py).

**Sửa xong lỗi trên thì sinh ra lỗi bảo mật.** URI camera chạy được có mật khẩu nằm
trong đó, và `seed_store_topology` ghi `source_uri` vào bảng `cameras` trên cả SQLite
lẫn Postgres. Mật khẩu camera sẽ nằm trong DB, trong mọi bản backup, và trong mọi log
in ra một dòng camera. Sửa bằng `redact_stream_credentials`, gọi ở cả hai alert store.

Manifest cũng không cần chứa mật khẩu: URI hỗ trợ biến môi trường —
`rtsp://admin:${CAM_PASS}@192.168.11.51:554/cam/realmonitor`.

### Đã kiểm

`backend/tests/test_rtsp_source.py` — 11 test, chạy không cần camera lẫn giả lập.
Toàn bộ suite CPU: **46/46 pass**.

Kiểm thủ công với giả lập:

| kiểm | kết quả |
|---|---|
| Đọc live, 12s, target 5 fps | 60 frame, `max_frame_gap_s=0.072` ≈ 1/15s → **không tồn đọng** |
| Giết publisher giữa chừng | `grab_failures=1`, backoff 5 lần, `reopen_count=1`, khoảng trống 9.04s, **vẫn giao đủ 125 frame** |
| Sai mật khẩu | `401 Unauthorized` |
| Tầng decode, 4 camera, 20s | **400/400 frame**, ring không nghẽn (0% chờ slot) |

## Cảnh báo cho bước 3: `grab_s` đổi ý nghĩa khi sang RTSP

Trong lượt decode 4 camera qua RTSP: `decode work = 76.5s` trong đó `grab = 74.0s`,
trên tổng 88.4 camera-giây. Tức **84% thời gian nằm trong `grab()`**.

Với file, `grab()` là công việc CPU thật (nó vẫn giải mã, chỉ bỏ bước chuyển màu).
Với luồng trực tiếp, `grab()` **chặn để chờ frame kế tiếp** — phần lớn con số đó là
đợi, không phải tính toán.

**Nên đừng so `decode work` giữa lượt file và lượt RTSP.** Cùng một tên metric,
hai ý nghĩa khác nhau. Muốn so chi phí CPU thật thì phải đo bằng cgroup `cpu.stat`
như đã làm ở `benchmarks/2026-08-27-16cam-4070-multistore.md`.

Thêm một điểm: mỗi camera tốn **~1.5-2s thiết lập kết nối**. Ở `--duration 20` đó là
~10% thời gian chạy; ở `--duration 180` chỉ còn ~1%. Bench RTSP phải chạy đủ dài.

## Bước 3-5 — chưa làm

3. Đo lại 16 camera qua RTSP localhost → tách được chi phí giao thức
4. Bật `pace_realtime` → lần đầu có số ở đúng chế độ production
5. Thêm biến mạng thật (`tc netem` để mô phỏng mất gói/jitter, rồi WAN thật)

Bước 3 và 4 quan trọng vì trần đo được hiện tại là **~99 FPS cho 16 camera, biên
1.55×** so với nhu cầu 64 FPS. Nếu RTSP tốn thêm 20-30% thì biên tụt còn ~1.2×.
Con số đó quyết định mô hình xử lý tập trung gánh được mấy cửa hàng — xem
`benchmarks/2026-08-27-16cam-4070-multistore.md`.
