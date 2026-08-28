const SYSTEM_PROFILE = [
  { label: 'Camera input', value: 'Existing CCTV' },
  { label: 'Processing', value: 'On-prem' },
  { label: 'Evidence', value: 'Video-backed' },
  { label: 'Decision', value: 'Owner verified' },
]

export default function StatStrip() {
  return (
    <section className="bg-band text-band-text" aria-label="System profile">
      <div data-reveal-group className="mx-auto grid max-w-[1280px] grid-cols-2 px-5 sm:px-8 md:grid-cols-4">
        {SYSTEM_PROFILE.map((item, index) => (
          <div
            data-reveal-item
            key={item.label}
            className={`px-4 py-7 md:px-7 md:py-8 ${index % 2 === 1 ? 'border-l border-band-border' : ''} ${index > 1 ? 'border-t border-band-border' : ''} ${index > 0 ? 'md:border-l md:border-band-border' : ''} md:border-t-0`}
          >
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-band-faint">{item.label}</p>
            <p className="mt-2 text-base font-medium tracking-[-0.01em] text-band-text md:text-lg">{item.value}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
