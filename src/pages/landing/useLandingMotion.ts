import type { RefObject } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'

gsap.registerPlugin(useGSAP, ScrollTrigger)

export function useLandingMotion(scope: RefObject<HTMLDivElement | null>) {
  useGSAP(() => {
    const root = scope.current
    if (!root) return

    const media = gsap.matchMedia()

    media.add(
      {
        desktop: '(min-width: 900px)',
        reduceMotion: '(prefers-reduced-motion: reduce)',
      },
      (context) => {
        const { desktop, reduceMotion } = context.conditions as {
          desktop: boolean
          reduceMotion: boolean
        }

        if (reduceMotion) {
          gsap.set(
            root.querySelectorAll(
              '[data-hero-kicker], [data-hero-title], [data-hero-copy], [data-hero-actions], [data-hero-media], [data-reveal], [data-reveal-item]',
            ),
            { clearProps: 'all' },
          )
          return
        }

        const hero = gsap.timeline({ defaults: { ease: 'power3.out' } })
        hero
          .from('[data-hero-kicker]', { autoAlpha: 0, y: 12, duration: 0.45 })
          .from('[data-hero-title]', { autoAlpha: 0, y: 34, duration: 0.8 }, '-=0.18')
          .from('[data-hero-copy]', { autoAlpha: 0, y: 18, duration: 0.6 }, '-=0.42')
          .from('[data-hero-actions]', { autoAlpha: 0, y: 14, duration: 0.5 }, '-=0.34')
          .from('[data-hero-media]', { autoAlpha: 0, y: 24, scale: 0.985, duration: 0.85 }, '-=0.68')

        gsap.utils.toArray<HTMLElement>('[data-reveal]').forEach((element) => {
          gsap.from(element, {
            autoAlpha: 0,
            y: 28,
            duration: 0.72,
            ease: 'power2.out',
            scrollTrigger: {
              trigger: element,
              start: 'clamp(top 86%)',
              once: true,
            },
          })
        })

        gsap.utils.toArray<HTMLElement>('[data-reveal-group]').forEach((group) => {
          const items = group.querySelectorAll('[data-reveal-item]')
          if (!items.length) return

          gsap.from(items, {
            autoAlpha: 0,
            y: 20,
            duration: 0.62,
            ease: 'power2.out',
            stagger: 0.08,
            scrollTrigger: {
              trigger: group,
              start: 'clamp(top 84%)',
              once: true,
            },
          })
        })

        if (desktop) {
          gsap.to('[data-hero-media-frame]', {
            yPercent: 4,
            ease: 'none',
            scrollTrigger: {
              trigger: '[data-hero]',
              start: 'top top',
              end: 'bottom top',
              scrub: 0.7,
            },
          })

        }
      },
      root,
    )

    const refresh = () => ScrollTrigger.refresh()
    document.fonts?.ready.then(refresh)

    return () => media.revert()
  }, { scope })
}
