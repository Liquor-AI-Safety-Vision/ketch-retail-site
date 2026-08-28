import { EnvelopeSimple } from '@phosphor-icons/react'
import type { FormEvent } from 'react'

export default function ContactSection() {
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    const formData = new FormData(event.currentTarget)
    const name = String(formData.get('name') ?? '').trim()
    const store = String(formData.get('store') ?? '').trim()
    const email = String(formData.get('email') ?? '').trim()
    const message = String(formData.get('message') ?? '').trim()
    const subject = `Pilot enquiry from ${store || name || 'website visitor'}`
    const body = [
      `Name: ${name}`,
      `Store / business: ${store}`,
      `Email: ${email}`,
      '',
      message || 'I would like to learn more about a pilot.',
    ].join('\n')

    window.location.href = `mailto:info@ketchenterprise.net?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
  }

  return (
    <section id="contact" className="border-t border-ink/10 bg-cream px-5 py-20 sm:px-8 md:py-28">
      <div className="mx-auto max-w-[1280px]">
        <div className="grid grid-cols-1 items-start gap-12 md:grid-cols-[0.78fr_1.22fr] md:gap-20">
          {/* Left */}
          <div data-reveal>
            <p className="mb-3 text-sm font-semibold text-teal-600">Get in touch</p>
            <h2 className="text-3xl md:text-4xl font-semibold tracking-tight leading-tight text-ink mb-4">
              Ready to see it on your cameras?
            </h2>
            <p className="text-base leading-relaxed text-ink-600 max-w-[48ch]">
              Send a message to schedule a 30-minute walkthrough. We will review the current dashboard
              and discuss how the workflow could fit your store's camera layout.
            </p>
            <div className="mt-8 text-sm text-ink-600 space-y-2">
              <p>Response within 24 hours.</p>
              <p>Pilot setup is on-site and typically takes one day.</p>
            </div>

            <div className="mt-8 flex flex-col gap-3 border-t border-ink/10 pt-6">
              <a
                href="mailto:info@ketchenterprise.net"
                className="flex items-center gap-3 text-sm text-ink-600 hover:text-ink transition-colors"
              >
                <EnvelopeSimple size={18} weight="duotone" className="text-teal-600 shrink-0" />
                <span>info@ketchenterprise.net</span>
              </a>
            </div>
          </div>

          {/* Right: simple form */}
          <form data-reveal className="flex flex-col gap-5 border-t border-ink/12 pt-7 md:border-l md:border-t-0 md:pl-10 md:pt-0" onSubmit={handleSubmit}>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="flex flex-col gap-1.5">
                <label htmlFor="name" className="text-xs font-semibold text-ink-600 uppercase tracking-wide">
                  Your name
                </label>
                <input
                  id="name"
                  name="name"
                  type="text"
                  required
                  placeholder="Alex Johnson"
                  className="rounded-md border border-ink/15 bg-cream-200 px-4 py-3 text-sm text-ink placeholder:text-ink-600/40
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
                  name="store"
                  type="text"
                  placeholder="Main St Liquors"
                  className="rounded-md border border-ink/15 bg-cream-200 px-4 py-3 text-sm text-ink placeholder:text-ink-600/40
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
                name="email"
                type="email"
                required
                placeholder="alex@mainliquors.com"
                className="rounded-md border border-ink/15 bg-cream-200 px-4 py-3 text-sm text-ink placeholder:text-ink-600/40
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
                name="message"
                rows={3}
                placeholder="How many cameras? Any specific zones of concern?"
                className="rounded-md border border-ink/15 bg-cream-200 px-4 py-3 text-sm text-ink placeholder:text-ink-600/40
                  focus:outline-none focus:ring-2 focus:ring-teal-600 focus:ring-offset-1 focus:ring-offset-cream
                  transition-shadow resize-none leading-relaxed"
              />
            </div>
            <button
              type="submit"
              className="mt-1 rounded-md bg-teal-900 px-6 py-3.5 text-sm font-semibold text-cream
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
