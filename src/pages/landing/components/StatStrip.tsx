interface StatItem {
  value: string
  label: string
  note?: string
}

const STATS: StatItem[] = [
  { value: '24/7',       label: 'Always watching',   note: 'every camera, never blinks' },
  { value: 'Seconds',    label: 'Shelf to alert',    note: 'flagged straight to your phone' },
  { value: 'Every frame',label: 'Nothing sampled',   note: 'reviewed, not spot-checked' },
  { value: '0 cloud',    label: 'Stays on-site',     note: 'footage never leaves the store' },
]

export default function StatStrip() {
  return (
    <div className="bg-teal-900 border-y border-teal-600/20">
      <div className="mx-auto max-w-[1200px] px-6 py-10">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-8 md:gap-0 md:divide-x md:divide-teal-600/25">
          {STATS.map((s) => (
            <div key={s.label} className="md:px-8 first:pl-0 last:pr-0">
              <p className="text-3xl font-semibold tracking-tight text-cream">{s.value}</p>
              <p className="mt-1 text-sm font-medium text-teal-300">{s.label}</p>
              {s.note && (
                <p className="mt-0.5 text-xs text-teal-300/60 italic">{s.note}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
