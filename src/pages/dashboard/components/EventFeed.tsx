import { Bell } from '@phosphor-icons/react'
import type { ClipEvents } from '../../../types'
import { tsToSeconds } from '../../../types'
import { useLang } from '../../../lib/i18n'
import { CONSOLE } from '../../../lib/consoleStrings'
import EventCard from './EventCard'

interface EventFeedProps {
  clip: ClipEvents
  currentTime: number
  onSeek: (seconds: number) => void
}

/**
 * Scrollable event-feed rail (right ~35%).
 * Amber → red escalation arc is legible in the feed order (chronological).
 * Active card highlight tracks video playhead.
 */
export default function EventFeed({ clip, currentTime, onSeek }: EventFeedProps) {
  const { lang } = useLang()
  const t = CONSOLE[lang]
  const events = clip.events

  // Determine which event the video is currently "near" (within 1.5s window after event)
  const activeIdx = events.findLastIndex(
    (e) => currentTime >= tsToSeconds(e.timestamp) - 0.5,
  )

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-ink/10 bg-white">
      {/* Feed header */}
      <div className="flex items-center justify-between border-b border-ink/10 px-4 py-3">
        <div className="flex items-center gap-2">
          <Bell size={14} className="text-ink-600" />
          <span className="text-[11px] font-bold uppercase tracking-widest text-ink-600">
            {t.eventFeed}
          </span>
        </div>
        <span className="rounded-md bg-cream-200 px-2 py-0.5 text-[11px] font-semibold text-ink-600">
          {t.eventCount(events.length)}
        </span>
      </div>

      {/* Escalation arc label */}
      {events.length >= 2 && (
        <div className="border-b border-ink/5 bg-cream-200/60 px-4 py-2">
          <p className="text-[11px] text-ink-600">
            {t.arc.label}{' '}
            <span className="font-semibold text-amber">{t.arc.a}</span>
            {' → '}
            <span className="font-semibold text-red">{t.arc.b}</span>
            {' → '}
            <span className="font-semibold text-teal-600">{t.arc.c}</span>
          </p>
        </div>
      )}

      {/* Cards — chronological so amber → red arc reads top-to-bottom */}
      <div className="flex-1 divide-y divide-ink/5 overflow-y-auto">
        {events.length === 0 ? (
          <div className="flex h-32 items-center justify-center text-sm text-ink-600">
            {t.noEvents}
          </div>
        ) : (
          events.map((event, i) => (
            <EventCard
              key={i}
              event={event}
              clipId={clip.clip_id}
              onSeek={onSeek}
              isActive={i === activeIdx}
            />
          ))
        )}
      </div>
    </div>
  )
}
