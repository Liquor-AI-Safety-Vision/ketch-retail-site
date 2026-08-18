import { VideoCamera, Clock, Warning, CheckCircle } from '@phosphor-icons/react'
import type { ManifestClip } from '../../../types'
import { TIER_META } from '../../../types'
import { thumbUrl } from '../../../lib/demo'
import { useLang, pick } from '../../../lib/i18n'
import { CONSOLE } from '../../../lib/consoleStrings'

interface IncidentGalleryProps {
  clips: ManifestClip[]
  activeClipId: string | null
  onSelect: (clipId: string) => void
  /** 'grid' (default) = full responsive grid for desktop, below the console.
   *  'rail' = compact horizontal swipe-strip placed right under the player on
   *  phones, so switching clips never needs a scroll round-trip. */
  variant?: 'grid' | 'rail'
  className?: string
}

/**
 * Renders whatever manifest.clips contains. Selecting a card switches the active
 * clip in the video panel. Two layouts share one ClipCard (see `variant`).
 */
export default function IncidentGallery({
  clips,
  activeClipId,
  onSelect,
  variant = 'grid',
  className = '',
}: IncidentGalleryProps) {
  const { lang } = useLang()
  const t = CONSOLE[lang]
  if (clips.length === 0) return null
  const factory = clips.some((c) => c.mode === 'factory')

  // Header label inherits the parent's text color (ink on the cream retail
  // console, steel-50 on the dark factory console) so the rail reads on both.
  const header = (
    <div className="mb-3 flex items-center gap-2">
      <span className="text-[11px] font-bold uppercase tracking-widest opacity-60">
        {t.incidentGallery}
      </span>
      <span className="rounded-md bg-cream-200 px-2 py-0.5 text-[10px] text-ink-600">
        {t.clipCount(clips.length)}
      </span>
    </div>
  )

  if (variant === 'rail') {
    return (
      <section className={className}>
        {header}
        {/* Edge-to-edge horizontal strip; -mx-4 cancels the main px-4 so it
            bleeds to the screen edges on phones. */}
        <div className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {clips.map((clip) => (
            <div key={clip.clip_id} className="w-44 shrink-0 snap-start">
              <ClipCard
                clip={clip}
                isActive={clip.clip_id === activeClipId}
                onSelect={() => onSelect(clip.clip_id)}
              />
            </div>
          ))}
        </div>
      </section>
    )
  }

  return (
    <section className={className}>
      <div className="mb-4 flex items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-widest text-ink-600">
          {t.incidentGallery}
        </span>
        <span className="rounded-md bg-cream-200 px-2 py-0.5 text-[10px] text-ink-600">
          {t.clipCount(clips.length)}
        </span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {clips.map((clip) => (
          <ClipCard
            key={clip.clip_id}
            clip={clip}
            isActive={clip.clip_id === activeClipId}
            onSelect={() => onSelect(clip.clip_id)}
          />
        ))}
      </div>

      <p className="mt-4 text-[11px] italic text-ink-600/60">
        {factory ? t.galleryNoteFactory : t.galleryNoteStore}
      </p>
    </section>
  )
}

interface ClipCardProps {
  clip: ManifestClip
  isActive: boolean
  onSelect: () => void
}

function fmtDuration(s: number): string {
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

function ClipCard({ clip, isActive, onSelect }: ClipCardProps) {
  const { lang } = useLang()
  const t = CONSOLE[lang]
  const title = pick(lang, clip.title, clip.title_en)
  // Clip-level poster frame (a representative annotated frame written alongside the
  // render). Falls back to the video-camera placeholder if a clip has none.
  const placeholderThumb = thumbUrl(clip.clip_id, 'poster.jpg')

  // Highest tier present in this clip (tier_counts is Record<string, number>)
  const highestTier = (Object.keys(clip.tier_counts)
    .map(Number)
    .sort((a, b) => a - b)[0]) as 1 | 2 | 3 | undefined

  const tierStyle = highestTier ? TIER_META[highestTier] : null

  const ringClass = isActive
    ? 'ring-2 ring-teal-600'
    : 'ring-1 ring-ink/10 hover:ring-teal-600/40'

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full cursor-pointer overflow-hidden rounded-xl bg-white text-left
        transition-all duration-150 ${ringClass}
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-600`}
    >
      {/* Thumbnail area */}
      <div className="relative aspect-video w-full overflow-hidden bg-ink/10">
        {placeholderThumb ? (
          <img
            src={placeholderThumb}
            alt={title}
            className="h-full w-full object-cover"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <VideoCamera size={32} className="text-ink/20" />
          </div>
        )}

        {/* Active indicator — bottom-left so it never collides with the
            top-right tier badge on narrow rail cards (long VI tier labels). */}
        {isActive && (
          <div className="absolute bottom-2 left-2 flex items-center gap-1.5 rounded-md
            bg-teal-900/90 px-2 py-0.5 backdrop-blur-sm">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-teal-300 opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-teal-300" />
            </span>
            <span className="text-[10px] font-bold uppercase tracking-widest text-cream/90">
              {t.active}
            </span>
          </div>
        )}

        {/* Tier badge overlay — highest tier present, or a calm "Cleared" chip
            for a zero-event clip (the honest counterexample: system stayed quiet). */}
        {tierStyle ? (
          <div className={`absolute right-2 top-2 rounded-md ${tierStyle.bg} px-2 py-0.5`}>
            <span className="text-[10px] font-bold uppercase tracking-wide text-white">
              {t.tier[highestTier!]}
            </span>
          </div>
        ) : (
          <div className="absolute right-2 top-2 flex items-center gap-1 rounded-md
            bg-cream/90 px-2 py-0.5 backdrop-blur-sm">
            <CheckCircle size={11} weight="fill" className="text-teal-600" />
            <span className="text-[10px] font-bold uppercase tracking-wide text-teal-900">
              {t.cleared}
            </span>
          </div>
        )}
      </div>

      {/* Card body */}
      <div className="px-3 py-3">
        <p className="truncate text-sm font-semibold text-ink">{title}</p>

        <div className="mt-1.5 flex items-center gap-3">
          <span className="flex items-center gap-1 text-[11px] text-ink-600">
            <Clock size={11} />
            {fmtDuration(clip.duration)}
          </span>
          <span className="flex items-center gap-1 text-[11px] text-ink-600">
            <Warning size={11} />
            {clip.event_count} event{clip.event_count !== 1 ? 's' : ''}
          </span>
          <span className="flex items-center gap-1 text-[11px] text-ink-600">
            <CheckCircle size={11} />
            {clip.mode}
          </span>
        </div>
      </div>
    </button>
  )
}
