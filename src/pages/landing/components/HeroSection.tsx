import { ArrowRight } from '@phosphor-icons/react'
import { LinkButton } from '../../../components/ui'
import { scrollToSection } from '../../../lib/scrollToSection'

export default function HeroSection() {
  return (
    <section data-hero className="relative isolate min-h-[calc(100dvh-4rem)] overflow-hidden border-b border-ink/10 bg-cream px-5 py-14 sm:px-8 md:py-20">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <img
          src="/landing/hero-retail-aisle-1600.webp"
          srcSet="/landing/hero-retail-aisle-960.webp 960w, /landing/hero-retail-aisle-1600.webp 1600w, /landing/hero-retail-aisle-2560.webp 2560w"
          sizes="100vw"
          alt=""
          width="1600"
          height="900"
          fetchPriority="low"
          decoding="async"
          className="hero-ambient-image absolute h-full w-full object-cover"
        />
        <div className="hero-ambient-wash absolute inset-0" />
      </div>

      <div className="relative z-10 mx-auto grid w-full max-w-[1280px] items-center gap-14 lg:grid-cols-[minmax(0,0.82fr)_minmax(0,1.18fr)] lg:gap-16">
        <div className="max-w-[620px]">
          <div data-hero-kicker className="mb-7 flex items-center gap-4">
            <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-teal-700">
              Retail loss prevention
            </span>
            <span className="h-px w-12 bg-teal-600/45" />
          </div>

          <h1
            data-hero-title
            className="max-w-[11ch] text-[clamp(3.2rem,6.1vw,6.5rem)] font-semibold leading-[0.94] tracking-[-0.06em] text-ink lg:max-w-none lg:text-[clamp(3.2rem,5.6vw,5rem)]"
          >
            Stop loss before it leaves the store.
          </h1>

          <div className="hero-copy-support relative isolate mt-8">
            <p data-hero-copy className="max-w-[54ch] text-lg leading-[1.65] text-ink-600 md:text-xl">
              Ketch turns continuous CCTV footage into reviewable clips, so owners inspect the right
              moment instead of replaying an entire day.
            </p>

            <div data-hero-actions className="mt-9 flex flex-wrap items-center gap-3">
              <LinkButton to="/dashboard" size="lg">
                Explore the demo
                <ArrowRight size={17} weight="bold" aria-hidden="true" />
              </LinkButton>
              <LinkButton href="#contact" onClick={scrollToSection('contact')} size="lg" variant="secondary">
                Book a pilot
              </LinkButton>
            </div>
            <p className="mt-4 text-xs leading-relaxed text-ink-600/80">
              The demo is the public, read-only dashboard with sample store data — no sign-in required.
            </p>
          </div>
        </div>

        <div data-hero-media className="relative lg:pl-4">
          <div
            data-hero-media-frame
            className="overflow-hidden rounded-[10px] border border-ink/15 bg-ink shadow-[0_28px_80px_-48px_rgba(10,30,28,0.55)]"
          >
            <div className="flex items-center justify-between border-b border-white/12 bg-[#0c1514] px-4 py-3 text-white">
              <div className="flex items-center gap-3">
                <span className="h-2 w-2 rounded-full bg-[#79d5a5]" aria-hidden="true" />
                <span className="text-[11px] font-semibold uppercase tracking-[0.17em]">Camera 06 / Counter</span>
              </div>
              <span className="text-[10px] uppercase tracking-[0.16em] text-white/55">Sample camera view</span>
            </div>

            <div className="relative aspect-[16/10] overflow-hidden">
              <img
                src="/demo-source/cameras/cctv-counter/cash_till_open_2.jpg"
                alt="Counter camera view tracking staff, customers, and the cash drawer"
                width="1600"
                height="1000"
                fetchPriority="high"
                decoding="async"
                className="absolute inset-0 h-full w-full object-cover"
              />
              <div className="absolute inset-x-0 bottom-0 z-10 grid grid-cols-2 border-t border-white/15 bg-black/55 text-white backdrop-blur-[2px] sm:grid-cols-3">
                <div className="px-4 py-3">
                  <p className="text-[9px] uppercase tracking-[0.18em] text-white/55">Zone</p>
                  <p className="mt-1 text-xs font-medium">Payment counter</p>
                </div>
                <div className="border-l border-white/15 px-4 py-3 sm:border-x">
                  <p className="text-[9px] uppercase tracking-[0.18em] text-white/55">Tracking</p>
                  <p className="mt-1 text-xs font-medium">Staff + customer</p>
                </div>
                <div className="hidden px-4 py-3 sm:block">
                  <p className="text-[9px] uppercase tracking-[0.18em] text-white/55">State</p>
                  <p className="mt-1 text-xs font-medium">Drawer monitored</p>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-[11px] text-ink-600">
            <span>Sample footage with demo data — not a live feed</span>
            <span className="shrink-0 font-medium text-teal-700">Production design: on-site processing</span>
          </div>
        </div>
      </div>
    </section>
  )
}
