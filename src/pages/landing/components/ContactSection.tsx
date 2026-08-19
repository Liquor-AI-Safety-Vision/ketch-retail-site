import { EnvelopeSimple, WhatsappLogo } from '@phosphor-icons/react'

export default function ContactSection() {
  return (
    <section id="contact" className="bg-cream px-6 py-20 md:py-28">
      <div className="mx-auto max-w-[1200px]">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-12 items-start">
          {/* Left */}
          <div>
            <p className="mb-3 text-xs font-bold uppercase tracking-[0.18em] text-teal-600">Get in touch</p>
            <h2 className="text-3xl md:text-4xl font-semibold tracking-tight leading-tight text-ink mb-4">
              Ready to see it on your cameras?
            </h2>
            <p className="text-base leading-relaxed text-ink-600 max-w-[48ch]">
              Send a message and we'll schedule a 30-minute walkthrough over video call — showing
              the live dashboard with your store's camera layout in mind. No deck, no pitch. Just
              the system running.
            </p>
            <div className="mt-8 text-sm text-ink-600 space-y-2">
              <p>Response within 24 hours.</p>
              <p>Pilot setup is on-site — typically one day.</p>
            </div>

            <div className="mt-8 flex flex-col gap-3 border-t border-ink/10 pt-6">
              <a
                href="mailto:info@ketchenterprise.net"
                className="flex items-center gap-3 text-sm text-ink-600 hover:text-ink transition-colors"
              >
                <EnvelopeSimple size={18} weight="duotone" className="text-teal-600 shrink-0" />
                <span>info@ketchenterprise.net</span>
              </a>
              {/* TODO(Huy): WhatsApp number — plain text until there is one */}
              <div className="flex items-center gap-3 text-sm text-ink-600">
                <WhatsappLogo size={18} weight="duotone" className="text-teal-600 shrink-0" />
                <span>Chat on WhatsApp</span>
              </div>
            </div>
          </div>

          {/* Right — simple form */}
          <form className="flex flex-col gap-4" onSubmit={(e) => e.preventDefault()}>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="name" className="text-xs font-semibold text-ink-600 uppercase tracking-wide">
                  Your name
                </label>
                <input
                  id="name"
                  type="text"
                  placeholder="Alex Johnson"
                  className="rounded-xl border border-ink/15 bg-cream-200 px-4 py-2.5 text-sm text-ink placeholder:text-ink-600/40
                    focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-1 focus:ring-offset-cream
                    transition-shadow"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="store" className="text-xs font-semibold text-ink-600 uppercase tracking-wide">
                  Store / business
                </label>
                <input
                  id="store"
                  type="text"
                  placeholder="Main St Liquors"
                  className="rounded-xl border border-ink/15 bg-cream-200 px-4 py-2.5 text-sm text-ink placeholder:text-ink-600/40
                    focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-1 focus:ring-offset-cream
                    transition-shadow"
                />
              </div>
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="email" className="text-xs font-semibold text-ink-600 uppercase tracking-wide">
                Email
              </label>
              <input
                id="email"
                type="email"
                placeholder="alex@mainliquors.com"
                className="rounded-xl border border-ink/15 bg-cream-200 px-4 py-2.5 text-sm text-ink placeholder:text-ink-600/40
                  focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-1 focus:ring-offset-cream
                  transition-shadow"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="message" className="text-xs font-semibold text-ink-600 uppercase tracking-wide">
                Message <span className="text-ink-600/50 font-normal normal-case tracking-normal">(optional)</span>
              </label>
              <textarea
                id="message"
                rows={3}
                placeholder="How many cameras? Any specific zones of concern?"
                className="rounded-xl border border-ink/15 bg-cream-200 px-4 py-2.5 text-sm text-ink placeholder:text-ink-600/40
                  focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-1 focus:ring-offset-cream
                  transition-shadow resize-none leading-relaxed"
              />
            </div>
            <button
              type="submit"
              className="mt-1 rounded-lg bg-teal-900 text-cream font-semibold text-sm px-6 py-3
                hover:bg-teal-600 transition-colors duration-150 focus-visible:outline-none
                focus-visible:ring-2 focus-visible:ring-teal-600 focus-visible:ring-offset-2
                focus-visible:ring-offset-cream active:translate-y-px"
            >
              Send message
            </button>
            <p className="text-xs text-ink-600/50">
              Your message goes directly to the pilot team. No marketing list, no third-party CRM.
            </p>
          </form>
        </div>
      </div>
    </section>
  )
}
