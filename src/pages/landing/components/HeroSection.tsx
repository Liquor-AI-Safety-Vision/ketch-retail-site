import { ArrowRight } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { Button } from '../../../components/ui'
import { scrollToSection } from '../../../lib/scrollToSection'

export default function HeroSection() {
  return (
    <section data-hero className="relative min-h-[calc(100dvh-4rem)] overflow-hidden border-b border-ink/10 bg-cream px-5 py-14 sm:px-8 md:py-20">
      <div className="relative mx-auto grid w-full max-w-[1280px] items-center gap-14 lg:grid-cols-[minmax(0,0.82fr)_minmax(540px,1.18fr)] lg:gap-16">
        <div className="max-w-[620px]">
          <div data-hero-kicker className="mb-7 flex items-center gap-4">
            <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-teal-600">
              Retail loss prevention
            </span>
            <span className="h-px w-12 bg-teal-600/45" />
          </div>

          <h1
            data-hero-title
            className="max-w-[11ch] text-[clamp(3.2rem,6.1vw,6.5rem)] font-semibold leading-[0.94] tracking-[-0.06em] text-ink"
          >
            Stop loss before it leaves the store.
          </h1>

          <p data-hero-copy className="mt-8 max-w-[54ch] text-lg leading-[1.65] text-ink-600 md:text-xl">
            Ketch turns continuous CCTV footage into reviewable clips, so owners inspect the right
            moment instead of replaying an entire day.
          </p>

          <div data-hero-actions className="mt-9 flex flex-wrap items-center gap-3">
            <Link to="/dashboard">
              <Button size="lg">
                View dashboard
                <ArrowRight size={17} weight="bold" />
              </Button>
            </Link>
            <a href="#contact" onClick={scrollToSection('contact')}>
              <Button size="lg" variant="secondary">Book a pilot</Button>
            </a>
          </div>
        </div>

        <div data-hero-media className="relative lg:pl-4">
          <div
            data-hero-media-frame
            className="overflow-hidden rounded-[10px] border border-ink/15 bg-ink shadow-[0_28px_80px_-48px_rgba(10,30,28,0.55)]"
          >
            <div className="flex items-center justify-between border-b border-white/12 bg-[#0c1514] px-4 py-3 text-white">
              <div className="flex items-center gap-3">
                <span className="h-2 w-2 rounded-full bg-[#79d5a5]" />
                <span className="text-[11px] font-semibold uppercase tracking-[0.17em]">Camera 06 / Counter</span>
              </div>
              <span className="text-[10px] uppercase tracking-[0.16em] text-white/55">Live observation</span>
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

          <div className="mt-4 flex items-center justify-between gap-4 text-[11px] text-ink-600">
            <span>Current dashboard footage</span>
            <span className="shrink-0 font-medium text-teal-600">On-prem processing</span>
          </div>
        </div>
      </div>
    </section>
  )
}
