import type { Lang } from './i18n'

/* Bilingual strings for the shared console widgets (VideoPanel, EventFeed,
   EventCard, IncidentGallery, PhonePushMockup, TierBadge). These components are
   used by BOTH the retail (/dashboard, EN) and factory (/factory/dashboard,
   VI-default) consoles, so they read these via useLang() instead of hardcoding.
   EN defines the shape; VI is type-checked against it. */

const EN = {
  live: 'LIVE',
  zones: 'Zones',
  legend: { hv: 'High-value', restricted: 'Restricted', other: 'Other' },
  eventFeed: 'Event Feed',
  eventCount: (n: number) => `${n} event${n !== 1 ? 's' : ''}`,
  noEvents: 'No events in this clip',
  arc: { label: 'Escalation arc:', a: 'enters zone → dwells', b: 'flagged', c: 'Zalo alert' },
  frameAt: (t: string) => `Frame at ${t}`,
  track: (id: number) => `Track ${id}`,
  detConf: (n: string) => `Detection conf · ${n}%`,
  reacquire: (id: number) =>
    `Track re-acquired after occlusion (ID ${id}). Continuity tightened in the pilot.`,
  tier: { 1: 'Priority Alert', 2: 'Early Warning', 3: 'Zone Breach' } as Record<number, string>,
  alertDelivery: 'Alert Delivery',
  review: 'review',
  pushNote1: 'Alert routes to your phone within seconds.',
  pushNote2: 'You confirm or dismiss — the camera flags, you decide.',
  senderFactory: 'Factory Alert',
  senderStore: 'Store Alert',
  botFactory: 'Ketch AI Bot',
  botStore: 'StoreAlert Bot',
  incidentGallery: 'Incident Gallery',
  clipCount: (n: number) => `${n} clip${n !== 1 ? 's' : ''}`,
  active: 'Active',
  cleared: 'Cleared',
  galleryNoteFactory: "Tuned to your factory's cameras during the pilot.",
  galleryNoteStore: "Tuned to your store's cameras during the pilot.",
}

export type ConsoleStrings = typeof EN

const VI: ConsoleStrings = {
  live: 'TRỰC TIẾP',
  zones: 'Khu vực',
  legend: { hv: 'Giá trị cao', restricted: 'Khu vực hạn chế', other: 'Khác' },
  eventFeed: 'Luồng sự kiện',
  eventCount: (n) => `${n} sự kiện`,
  noEvents: 'Không có sự kiện nào trong clip này',
  arc: { label: 'Trình tự cảnh báo:', a: 'đi vào → lưu lại lâu', b: 'ghi nhận', c: 'cảnh báo Zalo' },
  frameAt: (t) => `Khung hình lúc ${t}`,
  track: (id) => `Theo vết ${id}`,
  detConf: (n) => `Độ tin cậy phát hiện · ${n}%`,
  reacquire: (id) =>
    `Tìm lại được đối tượng theo vết sau khi bị che khuất (ID ${id}). Độ liên tục được tối ưu hơn trong giai đoạn thử nghiệm.`,
  tier: { 1: 'Cảnh báo ưu tiên', 2: 'Cảnh báo sớm', 3: 'Xâm nhập khu vực' },
  alertDelivery: 'Gửi cảnh báo',
  review: 'kiểm tra',
  pushNote1: 'Cảnh báo được gửi đến điện thoại của bạn trong vài giây.',
  pushNote2: 'Bạn xác nhận hoặc bỏ qua — camera ghi nhận, bạn quyết định.',
  senderFactory: 'Cảnh báo Nhà máy',
  senderStore: 'Store Alert',
  botFactory: 'Ketch AI Bot',
  botStore: 'StoreAlert Bot',
  incidentGallery: 'Thư viện sự cố',
  clipCount: (n) => `${n} clip`,
  active: 'Chưa xử lý',
  cleared: 'Đã xử lý',
  galleryNoteFactory: 'Tinh chỉnh theo camera nhà máy của bạn trong giai đoạn chạy thử.',
  galleryNoteStore: "Tuned to your store's cameras during the pilot.",
}

export const CONSOLE: Record<Lang, ConsoleStrings> = { en: EN, vi: VI }
