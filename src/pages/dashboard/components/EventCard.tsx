import { UserCircle } from '@phosphor-icons/react'
import type { DetectionEvent } from '../../../types'
import { TIER_META, tsToSeconds } from '../../../types'
import { TierBadge } from '../../../components/ui'
import { thumbUrl } from '../../../lib/demo'
import { useLang, pick } from '../../../lib/i18n'
import { CONSOLE } from '../../../lib/consoleStrings'

interface EventCardProps {
  event: DetectionEvent
  clipId: string
  onSeek: (seconds: number) => void
  /** Highlight card when video is near this event's timestamp */
  isActive?: boolean
}

/**
 * Single event card for the right-rail feed.
 *
 * Honesty constraints (DESIGN-CONTRACT §Golden rule):
 * - Shows detection confidence labeled explicitly as "detection conf", not "theft probability".
 * - Surfaces how_it_knows verbatim — never paraphrased to be more alarming.
 * - Track-ID re-acquisition difference (amber track 1 → red track 5) is shown transparently
 *   as a pilot tightening note, not hidden.
 * - No language implying a theft verdict.
 */
export default function EventCard({ event, clipId, onSeek, isActive }: EventCardProps) {
  const { lang } = useLang()
  const t = CONSOLE[lang]
  const m = TIER_META[event.tier]
  const thumb = thumbUrl(clipId, event.thumbnail)
  const seconds = tsToSeconds(event.timestamp)

  // Left border accent changes with tier
  const borderAccent =
    event.tier === 1
      ? 'border-l-red'
      : event.tier === 2
        ? 'border-l-amber'
        : 'border-l-teal-600'

  const activeBg = isActive ? 'bg-cream-200' : 'bg-white hover:bg-cream/80'

  return (
    <button
      type="button"
      onClick={() => onSeek(seconds)}
      className={`w-full cursor-pointer border-l-[3px] ${borderAccent} ${activeBg}
        px-4 py-3.5 text-left transition-colors duration-150
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600`}
    >
      {/* Header row: badge + timestamp */}
      <div className="flex items-center justify-between gap-2">
        <TierBadge tier={event.tier} variant="soft" />
        <span className="font-mono text-[11px] text-ink-600">{event.timestamp}</span>
      </div>

      {/* Thumbnail + label row */}
      <div className="mt-2.5 flex gap-3">
        {thumb && (
          <img
            src={thumb}
            alt={t.frameAt(event.timestamp)}
            width={72}
            height={54}
            className="h-[54px] w-[72px] flex-shrink-0 rounded-md object-cover"
            loading="lazy"
          />
        )}

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-snug text-ink">
            {pick(lang, event.label, event.label_en)}
          </p>

          {/* how_it_knows — surfaced verbatim, per DESIGN-CONTRACT §Golden rule */}
          {event.how_it_knows && (
            <p className="mt-1 text-[12px] leading-relaxed text-ink-600">
              {pick(lang, event.how_it_knows, event.how_it_knows_en)}
            </p>
          )}
        </div>
      </div>

      {/* Footer: track id + detection confidence (labeled honestly, not "theft prob") */}
      <div className="mt-2.5 flex items-center gap-3">
        {event.track_id != null && (
          <span className="flex items-center gap-1 text-[11px] text-ink-600">
            <UserCircle size={12} />
            <span>{t.track(event.track_id)}</span>
          </span>
        )}
        <span className={`text-[11px] ${m.text}`}>
          {t.detConf((event.confidence * 100).toFixed(0))}
        </span>
      </div>

      {/* Track re-acquisition transparency note (only shown when tier-1 event track_id differs from prior) */}
      {event.tier === 1 && event.track_id != null && event.track_id !== 1 && (
        <p className="mt-1.5 text-[10px] italic leading-relaxed text-ink-600/70">
          {t.reacquire(event.track_id)}
        </p>
      )}
    </button>
  )
}
