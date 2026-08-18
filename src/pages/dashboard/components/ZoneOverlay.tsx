import type { ClipEvents } from '../../../types'

interface ZoneOverlayProps {
  clip: ClipEvents
}

/** Return the stroke/fill color token for a zone name.
 *  Zone-TYPE legend — what kind of area the polygon marks, kept distinct from the
 *  event-severity tiers (TIER_META red/amber/teal). BGR twins live in
 *  process_video._zone_color so the burned-in video and this overlay agree.
 *  hv_* → amber  |  checkout_* → green  |  exit_* → blue  |  restricted_* → red */
function zoneColor(name: string): { stroke: string; fill: string; label: string } {
  if (name.startsWith('hv_')) {
    return { stroke: '#E2A03F', fill: 'rgba(226,160,63,0.12)', label: '#E2A03F' }
  }
  if (name.startsWith('checkout_')) {
    return { stroke: '#3F9E5A', fill: 'rgba(63,158,90,0.12)', label: '#3F9E5A' }
  }
  if (name.startsWith('exit_')) {
    return { stroke: '#3B82C4', fill: 'rgba(59,130,196,0.12)', label: '#3B82C4' }
  }
  if (name.startsWith('restricted_')) {
    return { stroke: '#C24A3A', fill: 'rgba(194,74,58,0.12)', label: '#C24A3A' }
  }
  return { stroke: '#128C8A', fill: 'rgba(18,140,138,0.12)', label: '#128C8A' }
}

/** Convert a zone label key to a human-readable display string. */
function zoneName(key: string): string {
  return key
    .replace(/^hv_/, '')
    .replace(/^restricted_/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Centroid of a polygon for label placement. */
function centroid(pts: [number, number][]): [number, number] {
  const n = pts.length
  const x = pts.reduce((a, p) => a + p[0], 0) / n
  const y = pts.reduce((a, p) => a + p[1], 0) / n
  return [x, y]
}

/**
 * SVG zone overlay rendered absolutely over the video.
 * viewBox matches the source frame (1280×720) so polygon coords are 1:1.
 * This is the only allowed inline SVG in the codebase (per DESIGN-CONTRACT §file-ownership).
 */
export default function ZoneOverlay({ clip }: ZoneOverlayProps) {
  const { frame_width: w, frame_height: h, zones } = clip

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="xMidYMid slice"
      className="pointer-events-none absolute inset-0 h-full w-full"
      aria-hidden="true"
    >
      {Object.entries(zones).map(([key, pts]) => {
        const color = zoneColor(key)
        const points = pts.map(([x, y]) => `${x},${y}`).join(' ')
        const [cx, cy] = centroid(pts)
        const label = zoneName(key)

        return (
          <g key={key}>
            <polygon
              points={points}
              fill={color.fill}
              stroke={color.stroke}
              strokeWidth="2"
              strokeLinejoin="round"
            />
            {/* Zone label */}
            <text
              x={cx}
              y={cy}
              textAnchor="middle"
              dominantBaseline="middle"
              fill={color.label}
              fontSize="14"
              fontWeight="600"
              fontFamily="ui-sans-serif, -apple-system, sans-serif"
              style={{ textShadow: '0 1px 3px rgba(0,0,0,0.5)' }}
            >
              {label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
