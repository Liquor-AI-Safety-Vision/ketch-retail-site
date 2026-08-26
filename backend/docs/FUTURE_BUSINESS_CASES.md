# Các bài toán nghiệp vụ dự kiến sau này

Ngày tạo: `2026-08-26`

Đây là **backlog/ghi chú**, không phải spec đầy đủ như `docs/01_quay_thu_ngan.md` hay
`docs/02_kho_ngoai_gio.md`. Mục đích duy nhất: ghi lại các bài toán đã bàn và những quyết
định/ràng buộc đã chốt, để lúc bắt tay làm thật không phải nhớ lại hoặc bàn lại từ đầu.
Khi triển khai thật một bài dưới đây, viết một doc riêng theo đúng format của doc 01/02
(nghiệp vụ, phạm vi POC, kiến trúc, luật cảnh báo, edge case, tham số khởi điểm) — doc này
không thay thế bước đó.

---

## Bài toán A — Khách hàng shoplifting: nhận diện tái phạm qua watchlist

### Nghiệp vụ

Khi hệ thống phát hiện một khách hàng có hành vi shoplifting: crop khuôn mặt, embed, lưu
người đó vào DB như một "khách" (không phải nhân viên). Đếm số lần cùng một người (khớp
qua face embedding) bị phát hiện — vượt ngưỡng (ví dụ 3 lần) thì đánh dấu vào watchlist.

Mở rộng: thêm một luồng camera ở lối vào (entrance) — detect mặt mọi người bước vào, align,
embed, so với watchlist ngay lúc họ vào cửa. Khớp với người đã bị đánh dấu thì cảnh báo
ngay, trước khi họ kịp hành động — khác các pipeline hiện tại vốn chỉ hỏi face khi có sự
kiện xảy ra (vào zone cấm).

### Phạm vi watchlist: chốt ở mức store trước

Đã quyết định: watchlist chỉ scope trong **một store** ở giai đoạn đầu, không phải toàn bộ
chuỗi. Có thể mở rộng lên mức **owner** sau này (các store chung một chủ) mà không cần đổi
schema — bảng `owner_stores` (many-to-many owner↔store) đã tồn tại sẵn cho đúng mục đích
này. Việc mở rộng chỉ là sửa `FaceGallery.match()`/`iter_embeddings()` từ nhận một
`store_id` sang nhận danh sách `store_id` (join `owner_stores` để lấy các store cùng chủ),
business/face tier không cần biết gì về thay đổi này.

### Đã có sẵn, dùng lại được

- Nhánh FACE (detect → align → embed, YOLOv6s-face + ArcFace R18) — y hệt cơ chế đang dùng
  cho nhận diện nhân viên.
- `FaceGallery.register()` (`scripts/face_identity.py`) — đã có sẵn hàm "crop face → embed →
  lưu person mới", chỉ chưa pipeline nào gọi tới.
- `persons.is_staff` — schema đã phân biệt sẵn nhân viên / không phải nhân viên.
- `FaceGallery.match()` đã hỗ trợ scoping theo cả `store_id` lẫn `company_id`, không chỉ
  `store_id` — nền tảng sẵn có cho việc mở rộng lên mức owner sau này.
- Cơ chế dispatch pipeline theo `pipeline_id` trong manifest (`business_tier.py`) — đã dùng
  3 lần cho `CounterCashTheft`/`StorageIntrusion`/`TrackingBaseline`, thêm một pipeline mới
  là thêm một nhánh dispatch, không đụng decode/inference/`AlertOutbox`.

### Chưa có, phải làm mới

1. **Model/logic phát hiện hành vi shoplifting** — phần khó nhất. Đây là bài toán nhận diện
   *hành vi*, khác hẳn intrusion (chỉ cần vị trí). Repo có sẵn dataset
   (`datasets/DCSASS_Shoplifting`, `datasets/RetailS`) nhưng **chưa có pipeline production
   nào** dùng chúng.
2. **Luồng match-hoặc-tạo-mới + đếm số lần** — chưa tồn tại. Cần: khi phát hiện shoplifting,
   thử match face với watchlist hiện có; khớp thì +1 lượt, không khớp thì tạo person mới với
   lượt=1. Cần thêm cột/bảng đếm lượt (chưa có) và luật đánh dấu khi vượt ngưỡng.
3. **Pipeline mới cho camera entrance** — khác `StorageIntrusion` (chỉ hỏi face khi có người
   vào zone cụ thể), camera entrance cần kiểm tra watchlist cho *mọi* người bước vào.
4. **Cân nhắc pháp lý/riêng tư** khi lưu embedding khuôn mặt của khách hàng (không phải nhân
   viên đã có hợp đồng lao động) — tuỳ khu vực pháp lý, cần rà trước khi triển khai thật,
   không chỉ là vấn đề kỹ thuật.

### Ảnh hưởng kiến trúc

Không phá vỡ 4 tầng hiện tại (decode/inference/business/face) — toàn bộ là add-in qua các
điểm mở rộng đã có sẵn (pipeline_id dispatch, manifest-driven camera config, `alerts` table
đã freeform qua `metadata_json`/`reason_codes_json`). Điểm tốn công thật sự (nếu phát hiện
hành vi cần chạy model riêng trên GPU) là thêm một model call vào inference tier, theo đúng
pattern `_BatchRunner` đã dùng cho pose/till — có tốn công thật, nhưng không phá cấu trúc.

---

## Bài toán B — Đếm & phân loại tô phở/bún tại cửa bếp (store loại hình khác: quán ăn)

### Nghiệp vụ

Một store khác hẳn mô hình cửa hàng rượu — quán bán phở/bún. Camera đặt tại chỗ đưa đồ ăn
từ bếp ra ngoài (pass-through window).

- **Giai đoạn 1 (core):** đếm số tô/bát đi qua khung hình trong ngày.
- **Giai đoạn 2 (mở rộng):** phân loại luôn loại món trong tô lúc đếm — phở bò, phở gà, bún
  cá, bún bò... — để ra được số liệu theo từng món, không chỉ tổng số tô.

### Khác biệt lớn nhất so với mọi bài toán hiện tại

Đối tượng cần nhận diện là **đồ vật** (tô/bát/món ăn), không phải người. Toàn bộ pipeline
pose/ReID/face hiện tại được xây quanh person — **không áp dụng được** cho bài này. Đây là
use case đầu tiên "object-centric" thay vì "person-centric" trong hệ thống.

### Cái gần nhất trong hệ thống hiện tại để tham khảo

Không phải pose/ReID/face, mà là **till detection** (`till_seg` model trong `CounterCashTheft`)
— cũng là detect vật thể tĩnh/di chuyển chậm trong một vùng khung hình cố định, không phải
person tracking. Cấu trúc `_BatchRunner` cho till (cách gọi model, cách gắn vào inference
tier) là điểm khởi đầu hợp lý hơn pose/ReID khi thiết kế model đếm tô.

### Chưa có, phải làm mới

1. **Model detection/classification cho tô/món ăn** — train riêng theo thực đơn của quán
   (fine-tune từ base COCO-like hoặc train từ đầu), hoàn toàn tách biệt khỏi mọi model đang
   dùng (pose, ReID, face, till).
2. **Cơ chế đếm** — line-crossing giống cách đếm người hiện tại, hoặc motion-triggered nếu
   tần suất thấp; cần chốt sau khi biết rõ góc camera thật.
3. **Bảng dữ liệu mới, không phải `alerts`** — đếm tô là số liệu vận hành hàng ngày (đồ vật,
   không phải sự kiện bất thường), khác hẳn khái niệm `alerts` hiện tại (cảnh báo cần con
   người xem xét). Khả năng cần một bảng riêng kiểu `item_counts` (store_id, camera_id, ngày,
   loại món, số lượng), không ép vào schema `alerts` hiện có.
4. **Dữ liệu training** — chưa có, cần thu thập/gán nhãn ảnh tô/món ăn riêng của quán.

### Việc cần chốt trước khi làm

- Góc camera cụ thể tại pass-through window (ảnh hưởng thiết kế đường đếm/zone).
- Cần đếm real-time hay tổng hợp cuối ngày là đủ — quyết định này ảnh hưởng có cần alert
  tức thời hay chỉ cần một dashboard/report đếm theo ngày (khác hẳn mọi pipeline hiện tại,
  vốn đều hướng tới cảnh báo tức thời).

### Ảnh hưởng kiến trúc

Vẫn là add-in ở mức tier (decode/inference/business/`AlertOutbox`/multi-store manifest đều
dùng lại được nguyên vẹn), nhưng là add-in **sâu hơn** bài toán A: cần một model hoàn toàn
mới trong inference tier, một pipeline nghiệp vụ mới không dùng lại logic FSM/zone hiện có,
và nhiều khả năng một bảng dữ liệu mới khác hẳn khái niệm `alerts`.

---

## Ghi chú chung

Cả hai bài toán đều xác nhận: kiến trúc phân tầng hiện tại đủ ổn định để hấp thụ nghiệp vụ
mới qua các điểm mở rộng đã có sẵn (pipeline_id dispatch, manifest-driven camera config,
schema mở rộng được khi cần) — không bài nào đòi hỏi thiết kế lại ranh giới 4 tầng hay
`AlertOutbox`. Bài toán A gần với những gì hệ thống đã làm (person + face); bài toán B là
bước nhảy xa hơn hẳn (object-centric, số liệu vận hành thay vì cảnh báo bảo mật) và nên coi
là một luồng nghiệp vụ tách biệt, không cố ép vào khuôn của các pipeline hiện tại.
