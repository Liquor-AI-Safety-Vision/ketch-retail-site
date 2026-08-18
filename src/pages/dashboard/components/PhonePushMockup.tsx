import { ChatCircleText, WhatsappLogo } from '@phosphor-icons/react'
import type { ClipEvents } from '../../../types'
import { useLang, pick } from '../../../lib/i18n'
import { CONSOLE } from '../../../lib/consoleStrings'

/**
 * Phone-frame mockup showing a push notification for the CURRENTLY SELECTED clip.
 *
 * Triage-honest copy (per DESIGN-CONTRACT §Golden rule): the body echoes the
 * clip's most-severe event label — a geometric fact, not a theft verdict.
 * Channels adapt to market: factory clips → Zalo (VN); store clips → SMS +
 * WhatsApp (US). Strings follow the active language. Falls back to the static
 * store sample when no clip is passed.
 */
export default function PhonePushMockup({ clip }: { clip?: ClipEvents }) {
  const { lang } = useLang()
  const t = CONSOLE[lang]
  const factory = clip?.mode === 'factory'

  // Most-severe event drives the notification (tier 1 > 2 > 3); else first.
  const top = clip?.events?.length
    ? [...clip.events].sort((a, b) => a.tier - b.tier)[0]
    : null
  const cam = clip?.title?.split('·')[0]?.trim() || 'CAM 01'
  const body = top
    ? `⚠ ${t.tier[top.tier]} · ${cam} · ${pick(lang, top.label, top.label_en)} — ${t.review}`
    : `⚠ ${t.tier[1]} · CAM 01 · subject at exit after dwell — ${t.review}`

  const market = factory ? 'VN · Zalo' : 'US · SMS + WhatsApp'

  return (
    <div className="rounded-xl border border-ink/10 bg-white p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-widest text-ink-600">
          {t.alertDelivery}
        </span>
        <span className="rounded-md bg-cream-200 px-2 py-0.5 text-[10px] text-ink-600">
          {market}
        </span>
      </div>

      {/* Phone frame */}
      <div className="mx-auto w-[220px]">
        <div
          className="relative rounded-[28px] border-[6px] border-ink/20 bg-ink/5 shadow-lg"
          aria-label="Phone notification mockup"
        >
          {/* Notch */}
          <div className="mx-auto mb-2 h-4 w-20 rounded-b-xl bg-ink/10" />

          {/* Lock screen / notification area */}
          <div className="space-y-2 px-3 pb-4">
            {factory ? (
              <>
                <NotifCard
                  icon={<ChatCircleText size={16} weight="fill" className="text-teal-600" />}
                  channel="Zalo"
                  time="now"
                  sender={t.senderFactory}
                  body={body}
                />
                <NotifCard
                  icon={<ChatCircleText size={16} weight="fill" className="text-teal-600" />}
                  channel="Zalo"
                  time="now"
                  sender={t.botFactory}
                  body={body}
                />
              </>
            ) : (
              <>
                <NotifCard
                  icon={<ChatCircleText size={16} weight="fill" className="text-teal-600" />}
                  channel="Messages (SMS)"
                  time="now"
                  sender={t.senderStore}
                  body={body}
                />
                <NotifCard
                  icon={<WhatsappLogo size={16} weight="fill" className="text-teal-600" />}
                  channel="WhatsApp"
                  time="now"
                  sender={t.botStore}
                  body={body}
                />
              </>
            )}
          </div>

          {/* Home bar */}
          <div className="mx-auto mb-2 h-1 w-16 rounded-full bg-ink/20" />
        </div>
      </div>

      {/* Honest framing note */}
      <p className="mt-3 text-center text-[11px] leading-relaxed text-ink-600/70">
        {t.pushNote1}<br />
        <span className="font-medium text-ink-600">{t.pushNote2}</span>
      </p>
    </div>
  )
}

interface NotifCardProps {
  icon: React.ReactNode
  channel: string
  time: string
  sender: string
  body: string
}

function NotifCard({ icon, channel, time, sender, body }: NotifCardProps) {
  return (
    <div className="rounded-2xl bg-white px-3 py-2.5 shadow-sm ring-1 ring-ink/8">
      <div className="mb-1.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {icon}
          <span className="text-[10px] font-semibold text-ink/60">{channel}</span>
        </div>
        <span className="text-[10px] text-ink/40">{time}</span>
      </div>
      <p className="text-[11px] font-semibold text-ink">{sender}</p>
      <p className="mt-0.5 text-[11px] leading-snug text-ink/70">{body}</p>
    </div>
  )
}
