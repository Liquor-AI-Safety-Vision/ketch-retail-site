const SYSTEM_PROFILE = [
  { label: 'Camera input', value: 'Existing CCTV' },
  { label: 'Processing design', value: 'On-site*' },
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
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-band-faint">{item.label}</p>
            <p className="mt-2 text-base font-medium tracking-[-0.01em] text-band-text md:text-lg">{item.value}</p>
          </div>
        ))}
      </div>
      <p className="mx-auto max-w-[1280px] px-5 pb-6 text-[11px] leading-relaxed text-band-muted sm:px-8">
        *Production design. The public demo on this site runs on hosted infrastructure with sample data.
      </p>
    </section>
  )
}
