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
import CtaBand from './components/CtaBand'
import Footer from './components/Footer'
import { useSeo } from '../../lib/seo'
import { useLandingMotion } from './useLandingMotion'

const JSON_LD = {
  '@context': 'https://schema.org',
  '@type': 'Service',
  name: 'Ketch Enterprise AI | Retail Loss Prevention',
  serviceType: 'AI video loss prevention for liquor and convenience stores',
  description:
    'Computer-vision theft detection for liquor, convenience, and gas-station stores. Flags robbery and employee theft on your existing cameras, on-prem.',
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
      'AI loss prevention for liquor, convenience, and gas-station stores. Detect robbery and employee theft on the cameras you already own, with on-prem processing and no cloud video storage.',
    jsonLd: JSON_LD,
  })

  return (
    <div ref={pageRef} className="landing-page overflow-x-clip bg-cream text-ink">
      <StickyNav />
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
      <CtaBand />
      <Footer />
    </div>
  )
}
