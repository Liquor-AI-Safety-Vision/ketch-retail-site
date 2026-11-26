export type Tier = 1 | 2 | 3
export type Mode = 'store' | 'factory'

export interface DetectionEvent {
  frame: number
  timestamp: string
  behavior: string
  tier: Tier
  label: string
  /** English variant of `label` (factory demo clips; absent on already-English store clips). */
  label_en?: string
  confidence: number
  bbox: [number, number, number, number]
  track_id: number | null
  thumbnail: string | null
  /** One honest sentence: how the system arrived at this event (scoping-spec §5). */
  how_it_knows?: string | null
  /** English variant of `how_it_knows`. */
  how_it_knows_en?: string | null
}

/** Zone polygons are in the SOURCE video's pixel space (frame_width × frame_height).
    Drive the dashboard's SVG overlay with a viewBox of those dims so it scales 1:1. */
export type ZonePolygon = [number, number][]

export interface ClipEvents {
  clip_id: string
  title: string
  title_en?: string
  mode: Mode
  video_file: string
  total_frames: number
  fps: number
  duration: number
  frame_width: number
  frame_height: number
  zones: Record<string, ZonePolygon>
  events: DetectionEvent[]
}

export interface ManifestClip {
  clip_id: string
  title: string
  title_en?: string
  mode: Mode
  duration: number
  fps: number
  total_frames: number
  video: string
  events: string
  event_count: number
  tier_counts: Record<string, number>
}

export interface Manifest {
  clips: ManifestClip[]
}

export interface TierStyle {
  name: string
  bg: string      // solid fill (badge background)
  text: string    // accent text
  border: string  // hairline border
  dot: string     // status dot
  ring: string    // focus/active ring
  chip: string    // soft tinted chip background (on cream)
}

/* Triage-honest tier semantics (scoping-spec §5 — never a "theft" verdict):
   1 = Priority Alert (red, the headline walkout)
   2 = Early Warning  (amber, dwell/suspicious)
   3 = Zone Breach    (teal). Colors map to §8 tokens in index.css. */
export const TIER_META: Record<Tier, TierStyle> = {
  1: { name: 'Priority Alert', bg: 'bg-red',      text: 'text-red-text',      border: 'border-red/40',      dot: 'bg-red-text',      ring: 'ring-red',      chip: 'bg-red-bg' },
  2: { name: 'Early Warning',  bg: 'bg-amber',     text: 'text-amber-text',    border: 'border-amber/40',    dot: 'bg-amber-text',    ring: 'ring-amber',    chip: 'bg-amber-bg' },
  3: { name: 'Zone Breach',    bg: 'bg-teal-600',  text: 'text-teal-600', border: 'border-teal-600/40', dot: 'bg-teal-600', ring: 'ring-teal-600', chip: 'bg-cream-200' },
}

// Base path for pre-rendered demo assets (manifest, videos, events, thumbs).
export const DEMO_BASE = `${import.meta.env.BASE_URL}demo`

export function assetUrl(clipId: string, rel: string): string {
  return `${DEMO_BASE}/${clipId}/${rel}`
}

export function tsToSeconds(ts: string): number {
  const p = ts.split(':').map(Number)
  return (p[0] ?? 0) * 3600 + (p[1] ?? 0) * 60 + (p[2] ?? 0)
}
