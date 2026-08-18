import { forwardRef, useImperativeHandle, useRef, useState } from 'react'
import { Play, Pause } from '@phosphor-icons/react'
import type { ClipEvents } from '../../../types'
import { TIER_META, tsToSeconds } from '../../../types'
import type { ManifestClip } from '../../../types'
import { videoUrl } from '../../../lib/demo'
import { useLang, pick } from '../../../lib/i18n'
import { CONSOLE } from '../../../lib/consoleStrings'
import ZoneOverlay from './ZoneOverlay'

export interface VideoPanelHandle {
  seek: (seconds: number) => void
}

interface VideoPanelProps {
  meta: ManifestClip
  clip: ClipEvents
  onTimeUpdate?: (t: number) => void
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`
}

/**
 * Left panel (~65%): video + SVG zone overlay + recording badge + scrubber with event markers.
 *
 * Seek API exposed via ref so EventFeed cards can control the playhead.
 */
const VideoPanel = forwardRef<VideoPanelHandle, VideoPanelProps>(
  ({ meta, clip, onTimeUpdate }, ref) => {
    const { lang } = useLang()
    const t = CONSOLE[lang]
    const videoRef = useRef<HTMLVideoElement>(null)
    const [playing, setPlaying] = useState(true)
    const [current, setCurrent] = useState(0)
    const [duration, setDuration] = useState(meta.duration ?? 0)

    useImperativeHandle(ref, () => ({
      seek: (seconds: number) => {
        const v = videoRef.current
        if (!v) return
        v.currentTime = seconds
        v.play().catch(() => {})
        setPlaying(true)
      },
    }))

    const toggle = () => {
      const v = videoRef.current
      if (!v) return
      if (v.paused) {
        v.play().catch(() => {})
        setPlaying(true)
      } else {
        v.pause()
        setPlaying(false)
      }
    }

    const handleTimeUpdate = () => {
      const t = videoRef.current?.currentTime ?? 0
      setCurrent(t)
      onTimeUpdate?.(t)
    }

    const handleLoadedMetadata = () => {
      const d = videoRef.current?.duration ?? meta.duration
      setDuration(d)
    }

    const handleScrub = (e: React.ChangeEvent<HTMLInputElement>) => {
      const t = parseFloat(e.target.value)
      if (videoRef.current) videoRef.current.currentTime = t
      setCurrent(t)
    }

    const progress = duration > 0 ? (current / duration) * 100 : 0
    // The zone legend only means something when the clip actually defines zones
    // (ZoneOverlay draws clip.zones). Demo clips have none → no overlay, so hide
    // the legend rather than label colors the viewer never sees.
    const hasZones = Object.keys(clip.zones ?? {}).length > 0

    return (
      <div className="flex flex-col self-start overflow-hidden rounded-xl border border-ink/10 bg-black">
        {/* Video + overlay wrapper */}
        <div className="relative aspect-video w-full">
          <video
            ref={videoRef}
            src={videoUrl(meta)}
            autoPlay
            loop
            muted
            playsInline
            className="h-full w-full object-cover"
            onTimeUpdate={handleTimeUpdate}
            onLoadedMetadata={handleLoadedMetadata}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
          />

          {/* Persistent SVG zone overlay — absolutely positioned, pointer-events-none */}
          <ZoneOverlay clip={clip} />

          {/* Film grain — zero-asset SVG noise, subtle, motion-safe */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-[5] opacity-[0.10] mix-blend-overlay motion-safe:animate-[grain_0.7s_steps(4)_infinite]"
            style={{
              backgroundImage:
                "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
              backgroundSize: '200px 200px',
            }}
          />

          {/* Recording dot + camera label */}
          <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded-md bg-black/60 px-2.5 py-1 backdrop-blur-sm">
            {/* Pulsing red dot — the only animation in the panel */}
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red opacity-70" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-red" />
            </span>
            <span className="text-[11px] font-bold uppercase tracking-widest text-cream/90">
              {t.live}
            </span>
            {/* Full clip title sits in the overlay only where there's room — on
                phones it would cover the subject, and it's already shown in the
                gallery card + the video's own baked-in CCTV timestamp. */}
            <span className="hidden text-[11px] text-cream/60 sm:inline">· {pick(lang, clip.title, clip.title_en)}</span>
          </div>
        </div>

        {/* Controls row: play/pause + time + scrubber with event markers */}
        <div className="flex items-center gap-3 bg-ink px-4 py-3">
          <button
            onClick={toggle}
            aria-label={playing ? 'Pause' : 'Play'}
            className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-cream/70
              transition-colors hover:text-cream focus-visible:outline-none focus-visible:ring-1
              focus-visible:ring-teal-300"
          >
            {playing ? <Pause size={16} weight="fill" /> : <Play size={16} weight="fill" />}
          </button>

          <span className="w-10 flex-shrink-0 font-mono text-[11px] text-cream/60">
            {fmtTime(current)}
          </span>

          {/* Scrubber track with event marker dots */}
          <div className="relative flex flex-1 items-center">
            {/* Track background */}
            <div className="h-1 w-full rounded-full bg-cream/10" />
            {/* Progress fill */}
            <div
              className="pointer-events-none absolute left-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-teal-300/60"
              style={{ width: `${progress}%` }}
            />
            {/* Playhead */}
            <div
              className="pointer-events-none absolute top-1/2 h-3 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-cream/90"
              style={{ left: `${progress}%` }}
            />
            {/* Event marker dots — amber/red per tier */}
            {clip.events.map((ev, i) => {
              const sec = tsToSeconds(ev.timestamp)
              const pct = duration > 0 ? (sec / duration) * 100 : 0
              const m = TIER_META[ev.tier]
              return (
                <button
                  key={i}
                  title={`${ev.timestamp} — ${pick(lang, ev.label, ev.label_en)}`}
                  onClick={() => {
                    if (videoRef.current) videoRef.current.currentTime = sec
                    setCurrent(sec)
                    videoRef.current?.play().catch(() => {})
                    setPlaying(true)
                  }}
                  className={`absolute top-1/2 -translate-x-1/2 -translate-y-1/2 h-3.5 w-3.5
                    rounded-full ${m.dot} ring-2 ring-ink transition-transform hover:scale-150
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-300`}
                  style={{ left: `${pct}%` }}
                />
              )
            })}
            {/* Invisible range for drag-seek */}
            <input
              type="range"
              min={0}
              max={duration || meta.duration}
              step={0.05}
              value={current}
              onChange={handleScrub}
              aria-label="Video scrubber"
              className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            />
          </div>

          <span className="w-10 flex-shrink-0 text-right font-mono text-[11px] text-cream/40">
            {fmtTime(duration)}
          </span>
        </div>

        {/* Zone legend — only when the clip has zones to explain */}
        {hasZones && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-cream/5 px-4 py-2">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-cream/30">
              {t.zones}
            </span>
            <LegendDot color="bg-amber" label={t.legend.hv} />
            <LegendDot color="bg-red" label={t.legend.restricted} />
            <LegendDot color="bg-teal-600" label={t.legend.other} />
          </div>
        )}
      </div>
    )
  },
)

VideoPanel.displayName = 'VideoPanel'
export default VideoPanel

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${color} opacity-80`} />
      <span className="text-[10px] text-cream/40">{label}</span>
    </span>
  )
}
