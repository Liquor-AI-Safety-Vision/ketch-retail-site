import { useCallback, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ShieldCheck, ArrowLeft } from '@phosphor-icons/react'
import { useManifest, useClipEvents } from '../../lib/demo'
import { Button } from '../../components/ui'
import VideoPanel from './components/VideoPanel'
import EventFeed from './components/EventFeed'
import PhonePushMockup from './components/PhonePushMockup'
import IncidentGallery from './components/IncidentGallery'
import type { VideoPanelHandle } from './components/VideoPanel'

/**
 * STREAM C — Loss-Prevention Console
 *
 * Layout: sticky header → two-column grid (video ~65 / feed ~35) → phone mockup → gallery.
 *
 * Data: loaded from public/demo/ via useManifest + useClipEvents (never forked).
 * All honesty rules from DESIGN-CONTRACT §Golden rule are delegated to EventCard/EventFeed.
 * This page is data-wiring and layout only.
 */
export default function DashboardPage() {
  const manifest = useManifest()
  // Retail console shows store clips only — factory clips live on /factory/dashboard.
  const clips = useMemo(
    () => (manifest?.clips ?? []).filter((c) => c.mode !== 'factory'),
    [manifest],
  )

  // Active clip is driven by gallery selection. The gallery is ordered by
  // severity arc (Cleared first), but the console should *open* on the strongest
  // clip — so the default player target is the hero, not clips[0]. Falls back to
  // the first clip if the hero id isn't in the manifest.
  const HERO_CLIP_ID = 'seedance_liquor_01'
  const [activeClipId, setActiveClipId] = useState<string | null>(null)
  const defaultClipId =
    clips.find((c) => c.clip_id === HERO_CLIP_ID)?.clip_id ?? clips[0]?.clip_id ?? null
  const resolvedClipId = activeClipId ?? defaultClipId

  const clip = useClipEvents(resolvedClipId)
  const meta = useMemo(
    () => clips.find((c) => c.clip_id === resolvedClipId) ?? null,
    [clips, resolvedClipId],
  )

  // Video playhead for EventFeed active-card tracking
  const [currentTime, setCurrentTime] = useState(0)

  // Ref to VideoPanel's seek() imperative handle
  const videoPanelRef = useRef<VideoPanelHandle>(null)

  const seek = useCallback((seconds: number) => {
    videoPanelRef.current?.seek(seconds)
  }, [])

  // Wrapper around the video, so a gallery pick can bring the player into view.
  // On phones the gallery sits below the fold; without this you tap a still then
  // have to scroll back up to watch it.
  const videoSectionRef = useRef<HTMLDivElement>(null)

  const selectClip = useCallback((id: string) => {
    setActiveClipId(id)
    videoSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  return (
    <div className="min-h-[100dvh] overflow-x-clip bg-cream text-ink">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-20 border-b border-ink/10 bg-cream/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between gap-3 px-4 py-3 sm:px-6">
          {/* Brand */}
          <div className="flex min-w-0 items-center gap-2.5">
            <ShieldCheck size={20} weight="fill" className="shrink-0 text-teal-600" />
            <span className="whitespace-nowrap text-sm font-bold tracking-tight text-ink">
              Ketch Enterprise<span className="text-teal-600"> AI</span>
            </span>
            {/* Live indicator — redundant with the video badge; hide on phones */}
            <span className="hidden items-center gap-1.5 rounded-md bg-cream-200 px-2 py-0.5 sm:flex">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red opacity-70" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-red" />
              </span>
              <span className="text-[10px] font-bold uppercase tracking-widest text-red">Live</span>
            </span>
          </div>

          {/* Back link */}
          <Link to="/" tabIndex={-1} className="shrink-0">
            <Button variant="ghost" size="sm">
              <ArrowLeft size={14} />
              <span className="hidden sm:inline">Back</span>
            </Button>
          </Link>
        </div>
      </header>

      {/* ── Main content ────────────────────────────────────────────────────── */}
      <main className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6">

        {/* Loading state */}
        {!manifest && (
          <div className="flex min-h-[40vh] items-center justify-center">
            <p className="text-sm text-ink-600">Loading demo data…</p>
          </div>
        )}

        {manifest && meta && clip && (
          <>
            {/* ── Two-column grid: video (65%) + event feed (35%) ───────────── */}
            <div className="grid gap-5 lg:grid-cols-[1.85fr_1fr]">

              {/* Left column: video + (mobile-only) clip rail directly beneath it.
                  min-w-0 lets the rail's overflow-x scroll instead of widening the page. */}
              <div className="flex min-w-0 flex-col gap-4">
                {/* Video panel (scroll-mt offsets the sticky header) */}
                <div ref={videoSectionRef} className="scroll-mt-20">
                  <VideoPanel
                    ref={videoPanelRef}
                    meta={meta}
                    clip={clip}
                    onTimeUpdate={setCurrentTime}
                  />
                </div>

                {/* Phone clip switcher — sits under the player so changing clips
                    never needs a scroll to the bottom gallery. */}
                <IncidentGallery
                  clips={clips}
                  activeClipId={resolvedClipId}
                  onSelect={selectClip}
                  variant="rail"
                  className="lg:hidden"
                />
              </div>

              {/* Right: event feed + phone push */}
              <div className="flex min-w-0 flex-col gap-5">
                {/* Event feed — fills available height */}
                <div className="min-h-0 flex-1">
                  <EventFeed
                    clip={clip}
                    currentTime={currentTime}
                    onSeek={seek}
                  />
                </div>

                {/* Phone push mockup */}
                <PhonePushMockup />
              </div>
            </div>

            {/* ── Incident gallery (full grid — desktop only; phones use the rail) ── */}
            <div className="mt-6 hidden rounded-xl border border-ink/10 bg-white p-5 lg:block">
              <IncidentGallery
                clips={clips}
                activeClipId={resolvedClipId}
                onSelect={selectClip}
              />
            </div>
          </>
        )}

        {/* Edge case: manifest loaded but no clips */}
        {manifest && clips.length === 0 && (
          <div className="flex min-h-[40vh] items-center justify-center">
            <p className="text-sm text-ink-600">No clips found in manifest.</p>
          </div>
        )}
      </main>

      {/* ── Footer strip ────────────────────────────────────────────────────── */}
      <footer className="mt-8 border-t border-ink/10 px-6 py-4">
        <div className="mx-auto flex max-w-[1400px] items-center justify-between">
          <p className="text-[11px] text-ink-600/60">
            Triage &amp; attention system · never a verdict · tuned to your cameras in the pilot
          </p>
          <p className="text-[11px] text-ink-600/40">0 cloud · on-prem inference</p>
        </div>
      </footer>
    </div>
  )
}
