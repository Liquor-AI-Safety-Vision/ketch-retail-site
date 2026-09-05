import { useRef } from 'react'
import StickyNav from './components/StickyNav'
import HeroSection from './components/HeroSection'
import StatStrip from './components/StatStrip'
import CostRoiSection from './components/CostRoiSection'
import HowItWorks from './components/HowItWorks'
import CoverageBand from './components/CoverageBand'
import HonestFramingStrip from './components/HonestFramingStrip'
import HowItGrows from './components/HowItGrows'
import PilotStrip from './components/PilotStrip'
import ResultsSection from './components/ResultsSection'
import ContactSection from './components/ContactSection'
import Footer from './components/Footer'
import { useSeo } from '../../lib/seo'
import { useLandingMotion } from './useLandingMotion'

const JSON_LD = {
  '@context': 'https://schema.org',
  '@type': 'Service',
  name: 'Ketch Enterprise AI | Retail Loss Prevention',
  serviceType: 'AI video loss prevention for liquor and convenience stores',
  description:
    'Computer-vision loss prevention for liquor, convenience, and gas-station stores. Surfaces reviewable, video-backed alerts from existing cameras for owner decisions; video processing is designed to stay on-site.',
  areaServed: 'United States',
  provider: { '@type': 'Organization', name: 'Ketch Enterprise AI' },
  offers: { '@type': 'Offer', category: 'Two-month pilot' },
}

export default function LandingPage() {
  const pageRef = useRef<HTMLDivElement>(null)
  useLandingMotion(pageRef)

  useSeo({
    lang: 'en',
    title:
      'Ketch Enterprise AI | Stop Retail Theft Before It Leaves the Store | Loss Prevention for Liquor & Convenience Stores',
    description:
      'AI loss prevention for liquor, convenience, and gas-station stores. Surfaces reviewable, video-backed alerts from the cameras you already own for owner review — with video processing designed to stay on-site.',
    jsonLd: JSON_LD,
  })

  const skipToContent = (event: { preventDefault: () => void }) => {
    event.preventDefault()
    const main = document.getElementById('main-content')
    main?.focus()
    main?.scrollIntoView()
  }

  return (
    <div ref={pageRef} className="landing-page overflow-x-clip bg-cream text-ink">
      <a
        href="#main-content"
        onClick={skipToContent}
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60]
          focus:rounded-md focus:bg-teal-900 focus:px-4 focus:py-2 focus:text-sm focus:font-semibold
          focus:text-cream focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-2"
      >
        Skip to content
      </a>
      <StickyNav />
      <main id="main-content" tabIndex={-1} className="focus:outline-none">
        <HeroSection />
        <StatStrip />
        <CostRoiSection />
        <HowItWorks />
        <CoverageBand />
        <HonestFramingStrip />
        <HowItGrows />
        <PilotStrip />
        <ResultsSection />
        <ContactSection />
      </main>
      <Footer />
    </div>
  )
}
